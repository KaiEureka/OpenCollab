"""Cancellation keeps the next request's tool exchange locally complete."""

from __future__ import annotations

import asyncio

import pytest

from opencollab.adapters.llm.openai_provider import _build_request_kwargs
from opencollab.adapters.tools.spawn import SpawnAgentTool
from opencollab.application.tool_execution import DeferredCall
from opencollab.bootstrap import build_session
from opencollab.domain.pending import RowStatus
from opencollab.domain.session import SessionPhase
from tests.support.environment_test_support import local_environment_factory as local_environment_factory
from tests.support.session_characterization_test_support import (
    FakeAgent,
    FakeLLMClient,
    FakeTool,
    llm_response,
    tool_call,
)


class WaitingTool(FakeTool):
    def __init__(self):
        super().__init__(name="wait_tool")
        self.started = asyncio.Event()
        self.executions = 0

    async def execute_with_runtime(self, args, runtime):
        self.executions += 1
        self.started.set()
        await asyncio.Event().wait()

@pytest.mark.asyncio
@pytest.mark.parametrize("completed_first", [False, True])
async def test_cancelled_tool_batch_can_continue_with_complete_chat_history(
    local_environment_factory, completed_first, tmp_path,
):
    waiting = WaitingTool()
    output = tmp_path / "completed.txt"

    def complete_write(_args):
        output.write_text("work already completed", encoding="utf-8")
        return "work already completed"

    completed = FakeTool(name="completed_tool", result=complete_write)
    tools = [completed, waiting] if completed_first else [waiting]
    calls = [tool_call("waiting", "wait_tool", "{}")]
    if completed_first:
        calls.insert(0, tool_call("completed", "completed_tool", "{}"))
    llm = FakeLLMClient(
        [
            llm_response(tool_calls=calls, finish_reason="tool_calls"),
            llm_response(content="continued answer"),
        ]
    )
    session = build_session(env=local_environment_factory(), agent=FakeAgent(tools=tools), llm=llm)
    await session.add_user_message("run tools")
    active = asyncio.create_task(session.run_loop())
    await asyncio.wait_for(waiting.started.wait(), timeout=0.5)
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(active, timeout=0.5)

    assert session.phase is SessionPhase.STOPPED
    assert session._open_tool_call_ids() == []
    interrupted = [message for message in session.messages if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in interrupted] == [call["id"] for call in calls]
    assert "outcome is unknown" in interrupted[-1]["content"]
    assert len(completed.calls) == int(completed_first)
    assert output.exists() is completed_first
    if completed_first:
        assert interrupted[0]["content"] == "work already completed"
        assert output.read_text(encoding="utf-8") == "work already completed"

    await session.add_user_message("continue")
    assert await session.run_loop() == "continued answer"
    request = _build_request_kwargs(
        "fake-model", llm.calls[-1]["messages"], llm.calls[-1]["tools"],
        0.0, False, None, None, None, None, None,
    )
    assert [
        message["tool_call_id"] for message in request["messages"] if message["role"] == "tool"
    ] == [call["id"] for call in calls]
    assert waiting.executions == 1
    assert len(completed.calls) == int(completed_first)

@pytest.mark.asyncio
@pytest.mark.parametrize("completed_prefix", [False, True])
async def test_cancellation_keeps_deferred_result_with_a_live_producer(local_environment_factory, completed_prefix):
    class ProducingTool(FakeTool):
        def __init__(self):
            super().__init__(name="spawn_agent")
            self.release = asyncio.Event()
            self.producer = None
            self.state = None

        async def execute_with_runtime(self, args, runtime):
            async def produce():
                await self.release.wait()
                self.state.pending_events.fill(runtime.tool_call_id, result="real child result")

            self.producer = asyncio.create_task(produce())
            return DeferredCall(ref=7)

    producer = ProducingTool()
    waiting = WaitingTool()
    completed = FakeTool(name="completed_tool", result="completed output")
    completed_event = asyncio.Event()

    async def hold_completed_event(event):
        if completed_prefix and event.type == "tool_end" and event.data.get("tool_call_id") == "completed":
            completed_event.set()
            await asyncio.Event().wait()

    calls = [
        tool_call("child", "spawn_agent", "{}"),
        tool_call("waiting", "wait_tool", "{}"),
    ]
    if completed_prefix:
        calls.insert(1, tool_call("completed", "completed_tool", "{}"))
    llm = FakeLLMClient(
        [llm_response(tool_calls=calls, finish_reason="tool_calls")]
    )
    session = build_session(
        env=local_environment_factory(), agent=FakeAgent(tools=[producer, completed, waiting]), llm=llm,
        event_sink=hold_completed_event,
    )
    producer.state = session.state
    active = asyncio.create_task(session.run_loop())
    try:
        await asyncio.wait_for((completed_event if completed_prefix else waiting.started).wait(), timeout=0.5)
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(active, timeout=0.5)

        row = session.state.pending_events.rows["child"]
        assert row.ref == 7
        assert row.status is RowStatus.PENDING
        assert producer.producer is not None and not producer.producer.done()
        assert session._open_tool_call_ids() == [call["id"] for call in calls]
        assert session.state.pending_events.rows["waiting"].status is RowStatus.FAILED
        if completed_prefix:
            row = session.state.pending_events.rows["completed"]
            assert row.status is RowStatus.DONE
            assert row.result == "completed output"
        assert all(
            message.get("tool_call_id") != "child" for message in session.messages
        )
        producer.release.set()
        await asyncio.wait_for(producer.producer, timeout=0.5)
        assert session.state.pending_events.rows["child"].result == "real child result"
        assert session.state.pending_events.rows["child"].status is RowStatus.DONE
    finally:
        producer.release.set()
        if producer.producer is not None:
            await producer.producer


@pytest.mark.asyncio
async def test_cancellation_before_deferred_producer_can_continue(local_environment_factory):
    started = asyncio.Event()

    async def block_start(event):
        if event.type == "tool_start" and event.data.get("tool_call_id") == "child":
            started.set()
            await asyncio.Event().wait()

    llm = FakeLLMClient([
        llm_response(tool_calls=[tool_call("child", "spawn_agent", "{}")], finish_reason="tool_calls"),
        llm_response(content="continued answer"),
    ])
    session = build_session(
        env=local_environment_factory(),
        agent=FakeAgent(tools=[FakeTool(name="spawn_agent", result="unused")]),
        llm=llm,
        event_sink=block_start,
    )
    await session.add_user_message("spawn")
    active = asyncio.create_task(session.run_loop())
    await asyncio.wait_for(started.wait(), timeout=0.5)
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(active, timeout=0.5)

    row = session.state.pending_events.rows["child"]
    assert row.status is RowStatus.FAILED
    assert row.ref is None
    await session.add_user_message("continue")
    assert await session.run_loop() == "continued answer"
    request = _build_request_kwargs(
        "fake-model", llm.calls[-1]["messages"], llm.calls[-1]["tools"],
        0.0, False, None, None, None, None, None,
    )
    tool_messages = [message for message in request["messages"] if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in tool_messages] == ["child"]
    assert "outcome is unknown" in tool_messages[0]["content"]


@pytest.mark.asyncio
async def test_cancellation_after_deferred_error_keeps_the_error(local_environment_factory):
    class RejectingSpawn(FakeTool):
        def __init__(self):
            super().__init__(name="spawn_agent")

        async def execute_with_runtime(self, _args, _runtime):
            return "Not spawned: invalid role."

    ended = asyncio.Event()

    async def block_end(event):
        if event.type == "tool_end" and event.data.get("tool_call_id") == "child":
            ended.set()
            await asyncio.Event().wait()

    llm = FakeLLMClient([
        llm_response(tool_calls=[tool_call("child", "spawn_agent", "{}")], finish_reason="tool_calls"),
        llm_response(content="continued answer"),
    ])
    session = build_session(
        env=local_environment_factory(),
        agent=FakeAgent(tools=[RejectingSpawn()]),
        llm=llm,
        event_sink=block_end,
    )
    await session.add_user_message("spawn")
    active = asyncio.create_task(session.run_loop())
    await asyncio.wait_for(ended.wait(), timeout=0.5)
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(active, timeout=0.5)

    row = session.state.pending_events.rows["child"]
    assert row.status is RowStatus.FAILED
    assert row.result == "Not spawned: invalid role."
    await session.add_user_message("continue")
    assert await session.run_loop() == "continued answer"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellation", [None, "before_waiter", "cleanup"])
async def test_completed_spawn_survives_cancellation_at_the_execution_boundary(
    local_environment_factory, cancellation,
):
    started = asyncio.Event()
    release = asyncio.Event()

    class Scheduler:
        producer = None

        async def spawn(self, parent_aid, role, task, context, tool_call_id=None):
            async def produce():
                await release.wait()
                session.state.pending_events.fill(tool_call_id, result="real child result")

            self.producer = asyncio.create_task(produce())
            started.set()
            if cancellation == "cleanup":
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    pass
            elif cancellation == "before_waiter":
                asyncio.get_running_loop().call_soon(active.cancel)
            return 7

    scheduler = Scheduler()
    llm = FakeLLMClient([
        llm_response(tool_calls=[tool_call(
            "child", "spawn_agent", '{"role":"coder","task":"work"}',
        )], finish_reason="tool_calls"),
        llm_response(content="continued answer"),
    ])
    session = build_session(
        env=local_environment_factory(),
        agent=FakeAgent(tools=[SpawnAgentTool(scheduler)]),
        llm=llm,
    )
    await session.add_user_message("spawn")
    active = asyncio.create_task(session.run_loop())
    try:
        await asyncio.wait_for(started.wait(), timeout=0.5)
        if cancellation == "cleanup":
            active.cancel()
        if cancellation is None:
            await asyncio.wait_for(active, timeout=0.5)
        else:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(active, timeout=0.5)

        row = session.state.pending_events.rows["child"]
        assert row.status is RowStatus.PENDING
        assert row.ref == 7
        assert scheduler.producer is not None and not scheduler.producer.done()
        release.set()
        await asyncio.wait_for(scheduler.producer, timeout=0.5)
        assert await session.run_loop() == "continued answer"
        assert [message for message in llm.calls[-1]["messages"] if message["role"] == "tool"] == [
            {"role": "tool", "tool_call_id": "child", "content": "real child result"},
        ]
    finally:
        release.set()
        if not active.done():
            active.cancel()
        await asyncio.gather(active, return_exceptions=True)
        if scheduler.producer is not None:
            await asyncio.gather(scheduler.producer, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellation", ["before_waiter", "cleanup"])
async def test_deferred_rejection_survives_cancellation_at_the_execution_boundary(
    local_environment_factory, cancellation,
):
    started = asyncio.Event()

    class RejectingSpawn(FakeTool):
        def __init__(self):
            super().__init__(name="spawn_agent")

        async def execute_with_runtime(self, args, runtime):
            started.set()
            if cancellation == "cleanup":
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    pass
            else:
                asyncio.get_running_loop().call_soon(active.cancel)
            return "Not spawned: invalid role."

    llm = FakeLLMClient([
        llm_response(tool_calls=[tool_call("child", "spawn_agent", "{}")], finish_reason="tool_calls"),
        llm_response(content="continued answer"),
    ])
    session = build_session(
        env=local_environment_factory(), agent=FakeAgent(tools=[RejectingSpawn()]), llm=llm,
    )
    active = asyncio.create_task(session.run_loop())
    await asyncio.wait_for(started.wait(), timeout=0.5)
    if cancellation == "cleanup":
        active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(active, timeout=0.5)

    row = session.state.pending_events.rows["child"]
    assert row.status is RowStatus.FAILED
    assert row.result == "Not spawned: invalid role."
    assert row.ref is None
    await session.add_user_message("continue")
    assert await session.run_loop() == "continued answer"


@pytest.mark.asyncio
async def test_cancellation_before_second_spawn_does_not_reuse_the_first_child(
    local_environment_factory,
):
    started = asyncio.Event()

    async def block_second_start(event):
        if event.type == "tool_start" and event.data.get("tool_call_id") == "second":
            started.set()
            await asyncio.Event().wait()

    class Scheduler:
        async def spawn(self, parent_aid, role, task, context, tool_call_id=None):
            assert tool_call_id == "first"
            return 7

    llm = FakeLLMClient([
        llm_response(tool_calls=[
            tool_call("first", "spawn_agent", '{"role":"coder","task":"first"}'),
            tool_call("second", "spawn_agent", '{"role":"coder","task":"second"}'),
        ], finish_reason="tool_calls"),
        llm_response(content="continued answer"),
    ])
    session = build_session(
        env=local_environment_factory(), agent=FakeAgent(tools=[SpawnAgentTool(Scheduler())]),
        llm=llm, event_sink=block_second_start,
    )
    active = asyncio.create_task(session.run_loop())
    await asyncio.wait_for(started.wait(), timeout=0.5)
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(active, timeout=0.5)

    first = session.state.pending_events.rows["first"]
    second = session.state.pending_events.rows["second"]
    assert first.status is RowStatus.PENDING
    assert first.ref == 7
    assert second.status is RowStatus.FAILED
    assert second.ref is None
    session.state.pending_events.fill("first", result="real first result")
    assert await session.run_loop() == "continued answer"
    tool_messages = [message for message in llm.calls[-1]["messages"] if message["role"] == "tool"]
    assert tool_messages[0]["content"] == "real first result"
    assert "outcome is unknown" in tool_messages[1]["content"]
