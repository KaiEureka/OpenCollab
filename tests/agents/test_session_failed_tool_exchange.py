"""A failed tool batch must leave a continuable transcript."""

from __future__ import annotations

import pytest

from opencollab.adapters.llm.openai_provider import _build_request_kwargs
from opencollab.bootstrap import build_session
from opencollab.domain.session import SessionPhase
from tests.support.environment_test_support import local_environment_factory as local_environment_factory
from tests.support.session_characterization_test_support import (
    FakeAgent,
    FakeLLMClient,
    FakeTool,
    llm_response,
    tool_call,
)


@pytest.mark.asyncio
async def test_failed_tool_batch_answers_open_calls_and_releases_the_reservation(
    local_environment_factory,
):
    llm = FakeLLMClient([
        llm_response(tool_calls=[tool_call("call-1", "bash", "{}")], finish_reason="tool_calls"),
        llm_response(content="continued answer"),
    ])
    session = build_session(
        env=local_environment_factory(),
        agent=FakeAgent(tools=[FakeTool(name="bash", result="unused")]),
        llm=llm,
    )
    await session.add_user_message("run")
    session.state.turn.loop_state.reserve(
        "same",
        recent_same=1,
        limit=5,
        workspace_sensitive=False,
        tool_call_id="call-1",
    )

    async def fail_checkpoint(_tool_calls):
        assert session.state.turn.loop_state.operations["same"].inflight_tool_call_id == "call-1"
        raise RuntimeError("loop retry reservation could not be persisted")

    session.tool_execution.process = fail_checkpoint
    with pytest.raises(RuntimeError, match="loop retry reservation"):
        await session.run_loop()

    assert session.phase is SessionPhase.ERROR
    assert session.state.turn.loop_state.operations["same"].inflight_tool_call_id is None
    tool_messages = [message for message in session.messages if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in tool_messages] == ["call-1"]
    assert "before its result was recorded" in tool_messages[0]["content"]

    await session.add_user_message("continue")
    assert await session.run_loop() == "continued answer"
    request = _build_request_kwargs(
        "fake-model", llm.calls[-1]["messages"], llm.calls[-1]["tools"],
        0.0, False, None, None, None, None, None,
    )
    assert [
        message["tool_call_id"] for message in request["messages"] if message["role"] == "tool"
    ] == ["call-1"]
