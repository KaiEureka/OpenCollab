from __future__ import annotations

import asyncio

import pytest

from opencollab import OpenCollab
from opencollab.adapters.llm.types import LLMResponse, Usage
from opencollab.application.workflow import WorkflowBudgetExceeded


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["agent", "agent_run"])
@pytest.mark.parametrize("first_tokens", [5_000, 15_000, 20_000, 21_000])
async def test_queued_call_uses_budget_after_prior_usage_lands(tmp_path, method, first_tokens):
    started, release = asyncio.Event(), asyncio.Event()
    contexts, call_starts, grants = [], [], []

    class Model:
        async def complete(self, messages, **kwargs):
            ctx = contexts[0]
            call_starts.append(ctx.tokens_spent())
            grants.append(ctx.sessions[-1].max_budget_tokens)
            if len(call_starts) == 1:
                started.set()
                await release.wait()
                return LLMResponse(content="first answer", usage=Usage(first_tokens - 10, 10))
            return LLMResponse(content="second answer", usage=Usage(5, 10))

    async def flow(ctx, _args):
        contexts.append(ctx)
        run = getattr(ctx, method)
        tasks = [asyncio.create_task(run("first", label="first", budget=10_000, tools=[]))]

        async def wait_for_reservations():
            while len(ctx.budget._leases) < 2:
                await asyncio.sleep(0)

        try:
            await asyncio.wait_for(started.wait(), 10)
            tasks.append(asyncio.create_task(run("second", label="second", budget=10_000, tools=[])))
            await asyncio.wait_for(wait_for_reservations(), 10)
            assert len(ctx.sessions) == 1
            assert ctx.tokens_remaining() == 0
            release.set()
            values = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
            if first_tokens >= 20_000:
                assert isinstance(values[1], WorkflowBudgetExceeded)
            elif method == "agent_run":
                assert values[1].status == "completed"
                assert values[1].hard_budget_tokens == min(10_000, 20_000 - first_tokens)
            else:
                assert values[1] == "second answer"
            return "finished"
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await ctx.wait_for_pending_cleanup()

    result = await OpenCollab(
        tmp_path, model="test-model", config={"max_output_tokens": 128},
    ).workflow(flow, llm=Model(), budget=20_000, concurrency=1, trace=False, limit_mode="explicit")

    assert result.status == "completed", result.reason
    expected_calls = 1 if first_tokens >= 20_000 else 2
    assert call_starts == ([0] if expected_calls == 1 else [0, first_tokens])
    assert grants == ([10_000] if expected_calls == 1 else [10_000, min(10_000, 20_000 - first_tokens)])
    assert result.tokens == first_tokens + (15 if expected_calls == 2 else 0)
    ctx = contexts[0]
    assert len(ctx.sessions) == expected_calls
    assert ctx.tokens_remaining() == 20_000 - result.tokens
    assert ctx.budget._leases == []
    assert not ctx.pending_cleanup_tasks
    assert ctx._semaphore._value == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["agent", "agent_run"])
async def test_overspend_retains_already_started_requests(tmp_path, method):
    first_started, second_started = asyncio.Event(), asyncio.Event()
    release_first, release_second = asyncio.Event(), asyncio.Event()
    contexts, calls = [], []

    class Model:
        async def complete(self, messages, **kwargs):
            prompt = messages[-1]["content"].splitlines()[0]
            calls.append(prompt)
            if prompt == "first":
                first_started.set()
                await release_first.wait()
                return LLMResponse(content="first answer", usage=Usage(20_990, 10))
            second_started.set()
            await release_second.wait()
            return LLMResponse(content="second answer", usage=Usage(5, 10))

    async def flow(ctx, _args):
        contexts.append(ctx)
        run = getattr(ctx, method)
        tasks = [asyncio.create_task(run("first", budget=10_000, tools=[]))]
        try:
            await asyncio.wait_for(first_started.wait(), 10)
            tasks.append(asyncio.create_task(run("second", budget=10_000, tools=[])))
            await asyncio.wait_for(second_started.wait(), 10)
            release_first.set()
            await asyncio.wait_for(tasks[0], 10)
            assert ctx.tokens_spent() == 21_000
            assert not tasks[1].done()
            release_second.set()
            value = await asyncio.wait_for(tasks[1], 10)
            assert getattr(value, "output", value) == "second answer"
            return "finished"
        finally:
            release_first.set()
            release_second.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await ctx.wait_for_pending_cleanup()

    result = await OpenCollab(
        tmp_path, model="test-model", config={"max_output_tokens": 128},
    ).workflow(flow, llm=Model(), budget=20_000, concurrency=2, trace=False, limit_mode="explicit")

    assert result.status == "completed", result.reason
    assert calls == ["first", "second"]
    assert result.tokens == 21_015
    assert contexts[0].budget._leases == []
    assert contexts[0]._semaphore._value == 2
