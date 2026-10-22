"""Real child producers survive a cancelled parent's public continuation."""

from __future__ import annotations

import asyncio

import pytest

from opencollab.adapters.tools.spawn import SpawnAgentTool
from opencollab.application.event_bus import EventBus
from opencollab.application.scheduler import Scheduler
from opencollab.application.scheduler_types import SchedulerTurnError
from opencollab.bootstrap import build_session
from opencollab.domain.session import SessionPhase
from tests.agents.test_session_tool_cancellation_recovery import WaitingTool
from tests.support.session_characterization_test_support import (
    FakeAgent,
    FakeLLMClient,
    FakeTool,
    fake_worktree_pool,
    llm_response,
    tool_call,
)


class ChildClient:
    def __init__(self):
        self.release = asyncio.Event()
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        await self.release.wait()
        return llm_response(content="actual child result", input_tokens=5, output_tokens=7)

class Factory:
    def __init__(self):
        self.client = ChildClient()
        self.sessions = []

    def build_spawn_session(self, **kwargs):
        agent = FakeAgent()
        agent.name = kwargs["role"]
        session = build_session(agent=agent, llm=self.client, max_budget_tokens=kwargs["budget"])
        session.state.aid = kwargs["aid"]
        self.sessions.append(session)
        return session

def make_team():
    factory = Factory()
    scheduler = Scheduler(
        session_factory=factory, worktree_pool=fake_worktree_pool(), event_sink=EventBus()
    )
    waiting = WaitingTool()
    completed = FakeTool(name="completed_tool", result="actual immediate result")
    skipped = FakeTool(name="skipped_tool")
    calls = [
        tool_call("completed", "completed_tool", "{}"),
        tool_call("child", "spawn_agent", '{"role":"coder","task":"produce a result"}'),
        tool_call("waiting", "wait_tool", "{}"),
        tool_call("skipped", "skipped_tool", "{}"),
    ]
    llm = FakeLLMClient([
        llm_response(tool_calls=calls, finish_reason="tool_calls"),
        llm_response(content="continued answer"),
    ])
    agent = FakeAgent(tools=[completed, SpawnAgentTool(scheduler), waiting, skipped])
    session = build_session(agent=agent, llm=llm)
    scheduler.register_lead(session)
    return scheduler, factory, session, llm, waiting, completed, skipped

async def cancel_parent(session, waiting):
    await session.add_user_message("run")
    active = asyncio.create_task(session.run_loop())
    await asyncio.wait_for(waiting.started.wait(), 0.5)
    return active

def assert_complete_request(llm, *, queued):
    messages = llm.calls[-1]["messages"]
    results = [message for message in messages if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in results] == ["completed", "child", "waiting", "skipped"]
    assert results[0]["content"] == "actual immediate result"
    assert results[1]["content"] == "actual child result"
    if queued:
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"].startswith("continue")

@pytest.mark.asyncio
@pytest.mark.parametrize("child_ready", ["before_cancel", "before_resume", "after_resume"])
@pytest.mark.parametrize("queued", [False, True])
async def test_cancelled_parent_resumes_real_child_once_in_tool_order(child_ready, queued):
    scheduler, factory, session, llm, waiting, completed, skipped = make_team()
    active = await cancel_parent(session, waiting)
    try:
        if child_ready == "before_cancel":
            factory.client.release.set()
            await asyncio.wait_for(scheduler.wait_until_terminal(1), 0.5)
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active
        assert session.phase is SessionPhase.STOPPED
        assert len(completed.calls) == 1
        assert skipped.calls == []
        if child_ready == "before_resume":
            factory.client.release.set()
            await asyncio.wait_for(scheduler.wait_until_terminal(1), 0.5)
        if queued:
            await session.add_user_message("continue")
        if child_ready == "after_resume":
            assert await session.run_loop() == ""
            assert session.phase is SessionPhase.AWAITING_EVENTS
            assert len(llm.calls) == 1
            assert all(message.get("content") != "continue" for message in session.messages)
            factory.client.release.set()
            await asyncio.wait_for(scheduler.wait_until_terminal(0), 0.5)
        else:
            assert await session.run_loop() == "continued answer"
        assert session.phase is SessionPhase.DONE
        assert session.state.pending_events.is_empty()
        assert session.state.pending_external_user_turn is None
        assert_complete_request(llm, queued=queued)
        assert waiting.executions == 1
        assert len(completed.calls) == 1
        assert skipped.calls == []
        assert factory.client.calls == 1
        assert len(factory.sessions) == 1
        assert scheduler.table.get(1).state.used_tokens == 12
        assert scheduler.table.total_used_tokens == session.used_tokens + 12
    finally:
        factory.client.release.set()
        await scheduler.cleanup()

@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_again", [False, True])
async def test_scheduler_user_turn_waits_or_cancels_surviving_child(cancel_again):
    scheduler, factory, session, llm, waiting, completed, skipped = make_team()
    active = await cancel_parent(session, waiting)
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active
    cancel_event = asyncio.Event()
    continued = asyncio.create_task(scheduler.run("continue", cancel_event=cancel_event))
    try:
        async def wait_for_suspension():
            while session.phase is not SessionPhase.AWAITING_EVENTS:
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_suspension(), 0.5)
        assert len(llm.calls) == 1
        if cancel_again:
            cancel_event.set()
            with pytest.raises(SchedulerTurnError, match="interrupted by user"):
                await asyncio.wait_for(continued, 0.5)
            assert session.phase is SessionPhase.STOPPED
            assert session.state.pending_events.is_empty()
            assert session.state.pending_external_user_turn is None
            assert scheduler._shutting_down is False
            assert await scheduler.run("retry") == "continued answer"
            results = [message for message in llm.calls[-1]["messages"] if message["role"] == "tool"]
            assert [message["tool_call_id"] for message in results] == ["completed", "child", "waiting", "skipped"]
            assert results[0]["content"] == "actual immediate result"
            assert results[1]["content"].startswith("Error:")
        else:
            factory.client.release.set()
            assert await asyncio.wait_for(continued, 0.5) == "continued answer"
            assert_complete_request(llm, queued=True)
        assert waiting.executions == 1
        assert len(completed.calls) == 1
        assert skipped.calls == []
        assert len(factory.sessions) == 1
    finally:
        factory.client.release.set()
        await scheduler.cleanup()
        await asyncio.gather(continued, return_exceptions=True)


@pytest.mark.parametrize("serialized", [False, True])
async def test_held_inbox_suspension_finishes_before_the_next_user_turn(serialized):
    class MessageFactory(Factory):
        def build_spawn_session(self, **kwargs):
            if kwargs["role"] != "worker":
                return super().build_spawn_session(**kwargs)
            agent = FakeAgent()
            agent.name = kwargs["role"]
            return build_session(
                agent=agent, aid=kwargs["aid"], max_budget_tokens=kwargs["budget"],
                llm=FakeLLMClient([llm_response(content="sender ready")]),
            )

    delivered = asyncio.Event()

    async def delayed_delivery(event):
        if event.type == "agent_message_delivered" and event.data["to_aid"] == 0:
            # Let the recipient suspend before its asynchronous delivery observer returns.
            driver = scheduler._tasks[0]
            await asyncio.shield(driver)
            assert session.phase is SessionPhase.AWAITING_EVENTS
            assert scheduler._tasks.get(0) is None
            assert session.state.pending_events.rows["inbox-child"].ref in scheduler._tasks
            delivered.set()

    factory = MessageFactory()
    scheduler = Scheduler(
        session_factory=factory, worktree_pool=fake_worktree_pool(),
        event_sink=EventBus(delayed_delivery), serialize_turns=serialized,
    )
    llm = FakeLLMClient([
        llm_response(tool_calls=[tool_call(
            "first-child", "spawn_agent", '{"role":"coder","task":"work to cancel"}',
        )], finish_reason="tool_calls"),
        llm_response(tool_calls=[tool_call(
            "inbox-child", "spawn_agent", '{"role":"coder","task":"finish teammate work"}',
        )], finish_reason="tool_calls"),
        llm_response(content="completed teammate request"),
        llm_response(content="completed external request"),
    ])
    session = build_session(agent=FakeAgent(tools=[SpawnAgentTool(scheduler)]), llm=llm)
    scheduler.register_lead(session)
    sender = await scheduler.spawn(0, "worker", "prepare sender")
    await scheduler.wait_until_terminal(sender)
    cancel_event = asyncio.Event()
    first = asyncio.create_task(scheduler.run("first request", cancel_event=cancel_event))
    continued = None

    async def wait_for_child():
        while session.phase is not SessionPhase.AWAITING_EVENTS or factory.client.calls != 1:
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(wait_for_child(), 2)
        assert "queued" in await scheduler.send_message(sender, 0, "queued work", "TEAMMATE_WORK")
        cancel_event.set()
        with pytest.raises(SchedulerTurnError, match="interrupted by user"):
            await asyncio.wait_for(first, 2)
        assert session.phase is SessionPhase.STOPPED
        assert scheduler._message_inbox[0]
        assert scheduler._shutting_down is False

        continued = asyncio.create_task(scheduler.run("new independent request"))
        await asyncio.wait_for(delivered.wait(), 2)
        assert len(llm.calls) == 2
        assert not any("new independent request" in str(message.get("content", "")) for message in session.messages)
        factory.client.release.set()
        assert await asyncio.wait_for(continued, 2) == "completed external request"
        assert len(llm.calls) == 4
        assert "TEAMMATE_WORK" in str(llm.calls[1]["messages"])
        assert "new independent request" not in str(llm.calls[2]["messages"])
        assert llm.calls[3]["messages"][-1]["content"].startswith("new independent request")
        assert session.state.pending_events.is_empty()
        assert not scheduler._message_inbox[0]
    finally:
        factory.client.release.set()
        await scheduler.cleanup()
        await asyncio.gather(first, *([continued] if continued is not None else []), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("child_ready", [False, True])
@pytest.mark.parametrize("queued", [False, True])
async def test_cancelled_parent_snapshot_retains_completed_results(child_ready, queued, tmp_path):
    scheduler, factory, session, llm, waiting, completed, skipped = make_team()
    active = await cancel_parent(session, waiting)
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active
    try:
        if child_ready:
            factory.client.release.set()
            await asyncio.wait_for(scheduler.wait_until_terminal(1), 0.5)
        if queued:
            await session.add_user_message("continue")
            if not child_ready:
                assert await session.run_loop() == ""
        path = tmp_path / "cancelled.json"
        session.save(str(path))
        restored = build_session(agent=FakeAgent(), llm=llm)
        restored.restore(str(path))
        if not queued:
            await restored.add_user_message("continue")
        assert await restored.run_loop() == "continued answer"
        results = [message for message in llm.calls[-1]["messages"] if message["role"] == "tool"]
        assert [message["tool_call_id"] for message in results] == ["completed", "child", "waiting", "skipped"]
        assert results[0]["content"] == "actual immediate result"
        expected = "actual child result" if child_ready else "deferred child interrupted by session restore"
        assert results[1]["content"] == expected
        assert restored.state.pending_events.is_empty()
        assert restored.state.pending_external_user_turn is None
        assert waiting.executions == 1
        assert len(completed.calls) == 1
        assert skipped.calls == []
    finally:
        factory.client.release.set()
        await scheduler.cleanup()
