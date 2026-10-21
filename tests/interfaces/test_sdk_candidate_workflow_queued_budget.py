from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from opencollab import OpenCollab
from opencollab.adapters.llm.types import LLMResponse, Usage
from opencollab.application.workflow import WorkflowBudgetExceeded


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCOLLAB_UNBOUNDED_LIMITS", raising=False)
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", "")
    (tmp_path / "source.txt").write_text("initial\n")
    for args in (
        ("init", "-q"),
        ("add", "source.txt"),
        ("-c", "user.name=Test User", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm", "Initialize test repository"),
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


@pytest.mark.parametrize("method", ["agent", "agent_run"])
@pytest.mark.parametrize(
    "first_tokens,child_cap,expected_grant",
    [(5_000, None, 10_000), (15_000, None, 5_000), (15_000, 3_000, 3_000),
     (20_000, None, None), (21_000, 3_000, None)],
)
async def test_candidate_child_refreshes_parent_budget_after_queue(
    repository, method, first_tokens, child_cap, expected_grant,
):
    started, release = asyncio.Event(), asyncio.Event()
    parents, children, calls, grants = [], [], [], []
    refused = []

    class Model:
        async def complete(self, messages, **kwargs):
            calls.append(messages[-1]["content"].splitlines()[0])
            owner = parents[0] if len(calls) == 1 else children[0]
            grants.append(owner.sessions[-1].max_budget_tokens)
            if len(calls) == 1:
                started.set()
                await release.wait()
                return LLMResponse(content="first answer", usage=Usage(first_tokens - 10, 10))
            return LLMResponse(content="candidate answer", usage=Usage(5, 10))

    async def nested(child, _args):
        children.append(child)
        try:
            value = await getattr(child, method)("candidate", budget=child_cap, tools=[])
        except WorkflowBudgetExceeded:
            refused.append(True)
            return "budget refused"
        if method == "agent_run":
            assert value.status == "completed"
            assert value.hard_budget_tokens == expected_grant
            return value.output
        return value

    async def flow(ctx, _args):
        parents.append(ctx)
        tasks = [asyncio.create_task(ctx.agent("first", budget=10_000, tools=[]))]

        async def wait_for_child_reservation():
            while not children or not children[0].budget._leases:
                await asyncio.sleep(0)

        try:
            await asyncio.wait_for(started.wait(), 10)
            tasks.append(asyncio.create_task(ctx.candidate_workflow(
                nested, {}, label="candidate", budget=10_000,
            )))
            await asyncio.wait_for(wait_for_child_reservation(), 10)
            assert len(ctx.sessions) == 1
            assert children[0].budget.total == 10_000
            assert ctx.tokens_remaining() == 0
            release.set()
            values = await asyncio.wait_for(asyncio.gather(*tasks), 10)
            return values[1].output
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await ctx.wait_for_pending_cleanup()

    result = await OpenCollab(
        repository, model="test-model", config={"max_output_tokens": 128},
    ).workflow(flow, llm=Model(), budget=20_000, concurrency=1, trace=False, limit_mode="explicit")

    assert result.status == "completed", result.reason
    if expected_grant is None:
        assert result.output == "budget refused"
        assert refused == [True]
        assert calls == ["first"]
        assert grants == [10_000]
        assert result.tokens == first_tokens
    else:
        assert result.output == "candidate answer"
        assert refused == []
        assert calls == ["first", "candidate"]
        assert grants == [10_000, expected_grant]
        assert result.tokens == first_tokens + 15
    assert parents[0].tokens_remaining() == 20_000 - result.tokens
    assert parents[0].agent_failures == ()
    for ctx in [*parents, *children]:
        assert ctx.budget._leases == []
        assert ctx.pending_cleanup_tasks == ()
        assert ctx._semaphore._value == 1
    assert not Path(children[0].workspace_root).exists()
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=repository) == b""


@pytest.mark.parametrize("method", ["agent", "agent_run"])
async def test_started_candidate_request_finishes_after_parent_overspend(repository, method):
    parent_started, child_started = asyncio.Event(), asyncio.Event()
    release_parent, release_child = asyncio.Event(), asyncio.Event()
    parents, children, calls = [], [], []

    class Model:
        async def complete(self, messages, **kwargs):
            prompt = messages[-1]["content"].splitlines()[0]
            calls.append(prompt)
            if prompt == "first":
                parent_started.set()
                await release_parent.wait()
                return LLMResponse(content="first answer", usage=Usage(20_990, 10))
            assert children[0].sessions[-1].max_budget_tokens == 10_000
            child_started.set()
            await release_child.wait()
            return LLMResponse(content="candidate answer", usage=Usage(5, 10))

    async def nested(child, _args):
        children.append(child)
        value = await getattr(child, method)("candidate", budget=10_000, tools=[])
        if method == "agent_run":
            assert value.status == "completed"
            assert value.hard_budget_tokens == 10_000
            return value.output
        return value

    async def flow(ctx, _args):
        parents.append(ctx)
        tasks = [asyncio.create_task(ctx.agent("first", budget=10_000, tools=[]))]
        try:
            await asyncio.wait_for(parent_started.wait(), 10)
            tasks.append(asyncio.create_task(ctx.candidate_workflow(
                nested, {}, label="candidate", budget=10_000,
            )))
            await asyncio.wait_for(child_started.wait(), 10)
            release_parent.set()
            await asyncio.wait_for(tasks[0], 10)
            assert ctx.tokens_spent() == 21_000
            assert not tasks[1].done()
            release_child.set()
            return (await asyncio.wait_for(tasks[1], 10)).output
        finally:
            release_parent.set()
            release_child.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await ctx.wait_for_pending_cleanup()

    result = await OpenCollab(
        repository, model="test-model", config={"max_output_tokens": 128},
    ).workflow(flow, llm=Model(), budget=20_000, concurrency=2, trace=False, limit_mode="explicit")

    assert result.status == "completed", result.reason
    assert result.output == "candidate answer"
    assert result.tokens == 21_015
    assert calls == ["first", "candidate"]
    for ctx in [*parents, *children]:
        assert ctx.budget._leases == []
        assert ctx.pending_cleanup_tasks == ()
        assert ctx._semaphore._value == 2
    assert not Path(children[0].workspace_root).exists()


@pytest.mark.parametrize("method", ["agent", "agent_run"])
async def test_cancelled_queued_child_releases_parent_reservation(repository, method):
    started, release = asyncio.Event(), asyncio.Event()
    parents, children, calls = [], [], []

    class Model:
        async def complete(self, messages, **kwargs):
            prompt = messages[-1]["content"].splitlines()[0]
            calls.append(prompt)
            if prompt == "first":
                started.set()
                await release.wait()
                return LLMResponse(content="first answer", usage=Usage(4_990, 10))
            return LLMResponse(content="later answer", usage=Usage(5, 10))

    async def nested(child, _args):
        children.append(child)
        return await getattr(child, method)("candidate", budget=10_000, tools=[])

    async def flow(ctx, _args):
        parents.append(ctx)
        tasks = [asyncio.create_task(ctx.agent("first", budget=10_000, tools=[]))]

        async def wait_for_child_reservation():
            while not children or not children[0].budget._leases:
                await asyncio.sleep(0)

        try:
            await asyncio.wait_for(started.wait(), 10)
            tasks.append(asyncio.create_task(ctx.candidate_workflow(
                nested, {}, label="candidate", budget=10_000,
            )))
            await asyncio.wait_for(wait_for_child_reservation(), 10)
            tasks[1].cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(tasks[1], 10)
            assert len(ctx.budget._leases) == 1
            assert children[0].budget._leases == []
            assert not Path(children[0].workspace_root).exists()
            assert calls == ["first"]
            release.set()
            await asyncio.wait_for(tasks[0], 10)
            return await ctx.agent("later", budget=10_000, tools=[])
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await ctx.wait_for_pending_cleanup()

    result = await OpenCollab(
        repository, model="test-model", config={"max_output_tokens": 128},
    ).workflow(flow, llm=Model(), budget=20_000, concurrency=1, trace=False, limit_mode="explicit")

    assert result.status == "completed", result.reason
    assert result.output == "later answer"
    assert result.tokens == 5_015
    assert calls == ["first", "later"]
    for ctx in [*parents, *children]:
        assert ctx.budget._leases == []
        assert ctx.pending_cleanup_tasks == ()
        assert ctx._semaphore._value == 1
