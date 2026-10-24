from __future__ import annotations

import asyncio

import pytest

from opencollab.adapters.llm import client as transport
from opencollab.adapters.llm.client import LLMClient
from opencollab.adapters.llm.request_admission import request_slot
from opencollab.adapters.llm.types import LLMResponse, Usage
from opencollab.application.session_run import GenerationTimeoutError
from opencollab.bootstrap import build_session
from opencollab.domain.agent import Agent
from tests.runtime.test_async_compaction import history
from tests.support.environment_test_support import local_environment_factory as local_environment_factory


@pytest.mark.parametrize("summary", [False, True])
async def test_queue_wait_leaves_the_full_provider_timeout(
    tmp_path, monkeypatch, local_environment_factory, summary,
):
    calls = []

    async def complete(*_args, **_kwargs):
        calls.append("started")
        await asyncio.sleep(0.06)
        return LLMResponse(content="<summary>Earlier context.</summary>" if summary and len(calls) == 1 else "answer")

    monkeypatch.setattr(transport, "complete_openai", complete)
    async with LLMClient(
        api_key="local", request_pool=str(tmp_path), request_capacity=1, context_window=40_000,
    ) as client:
        session = build_session(
            agent=Agent(name="queued", system_prompt="system"), llm=client,
            env=local_environment_factory(str(tmp_path)), llm_timeout=0.1,
        )
        session.messages = history(summary)
        async with request_slot(str(tmp_path), 1):
            owner = asyncio.create_task(session.run_loop())
            await asyncio.sleep(0.18)
            assert calls == []
            assert not owner.done()
        assert await asyncio.wait_for(owner, 2) == "answer"
        assert len(calls) == (2 if summary else 1)
        await session.aclose()


@pytest.mark.parametrize("summary", [False, True])
async def test_admitted_timeout_retains_slot_and_late_usage_until_cleanup(
    tmp_path, monkeypatch, local_environment_factory, summary,
):
    cancelled = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def complete(*_args, **_kwargs):
        calls.append("started")
        if len(calls) == 1:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
        return LLMResponse(content="<summary>Earlier context.</summary>", usage=Usage(13, 24))

    monkeypatch.setattr(transport, "complete_openai", complete)
    async with LLMClient(
        api_key="local", request_pool=str(tmp_path), request_capacity=1, context_window=40_000,
    ) as client:
        session = build_session(
            agent=Agent(name="slow", system_prompt="system"), llm=client,
            env=local_environment_factory(str(tmp_path)), llm_timeout=0.08,
        )
        session.messages = history(summary)
        async with request_slot(str(tmp_path), 1):
            owner = asyncio.create_task(session.run_loop())
            await asyncio.sleep(0.15)
            assert not owner.done()
            assert calls == []
        try:
            with pytest.raises(GenerationTimeoutError):
                await asyncio.wait_for(owner, 1)
            await asyncio.wait_for(cancelled.wait(), 1)
            assert session.pending_cleanup_tasks
            following = asyncio.create_task(client.complete([]))
            await asyncio.sleep(0.1)
            assert calls == ["started"]
            assert not following.done()
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(*session.pending_cleanup_tasks, return_exceptions=True), 1)
        await asyncio.wait_for(following, 1)
        assert session.used_tokens == 37
        assert session.runner.late_provider_usage == (37,)
        assert session.pending_cleanup_tasks == ()
        await session.aclose()


@pytest.mark.parametrize("summary", [False, True])
@pytest.mark.parametrize("stop", ["cancel", "deadline"])
async def test_outer_cancellation_removes_the_waiter_before_admission(
    tmp_path, monkeypatch, local_environment_factory, summary, stop,
):
    calls = []

    async def complete(*_args, **_kwargs):
        calls.append("started")
        return LLMResponse(content="answer")

    monkeypatch.setattr(transport, "complete_openai", complete)
    async with LLMClient(
        api_key="local", request_pool=str(tmp_path), request_capacity=1, context_window=40_000,
    ) as client:
        session = build_session(
            agent=Agent(name="cancelled", system_prompt="system"), llm=client,
            env=local_environment_factory(str(tmp_path)), llm_timeout=0.08,
        )
        session.messages = history(summary)
        async with request_slot(str(tmp_path), 1):
            owner = asyncio.create_task(session.run_loop())
            if stop == "cancel":
                await asyncio.sleep(0.12)
                assert not owner.done()
                owner.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(owner, 1)
            else:
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(owner, 0.12)
            await asyncio.wait_for(asyncio.gather(*session.pending_cleanup_tasks, return_exceptions=True), 1)
        await asyncio.sleep(0.1)
        assert calls == []
        assert session.used_tokens == 0
        assert session.pending_cleanup_tasks == ()
        assert (await asyncio.wait_for(client.complete([]), 1)).content == "answer"
        await session.aclose()
