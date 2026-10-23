"""Public session construction and restoration retain effective execution limits."""

from __future__ import annotations

import pytest

from opencollab.application.session import Session
from opencollab.bootstrap import build_session, load_session
from opencollab.bootstrap.container import build_session_runtime
from opencollab.domain.session import SessionPhase
from tests.support.environment_test_support import local_environment_factory as local_environment_factory
from tests.support.session_characterization_test_support import (
    FakeAgent,
    FakeLLMClient,
    FakeTool,
    llm_response,
    tool_call,
)


def _model():
    return FakeLLMClient([
        llm_response(tool_calls=[tool_call(arguments="{}")]),
        llm_response(content="completed"),
    ])


@pytest.mark.parametrize("entry", ["direct", "factory"])
@pytest.mark.parametrize(
    ("limit", "value", "runtime_value", "calls", "phase"),
    [
        ("max_budget_tokens", 0, 10_000, 0, SessionPhase.STOPPED),
        ("max_budget_tokens", 1, 10_000, 0, SessionPhase.STOPPED),
        ("max_budget_tokens", 10_000, 0, 2, SessionPhase.DONE),
        ("max_budget_tokens", None, 0, 2, SessionPhase.DONE),
        ("max_steps", 0, 10, 0, SessionPhase.STOPPED),
        ("max_steps", 1, 10, 1, SessionPhase.STOPPED),
        ("max_steps", 10, 0, 2, SessionPhase.DONE),
        ("max_steps", None, 0, 2, SessionPhase.DONE),
    ],
)
async def test_explicit_constructor_limits_control_execution(local_environment_factory,
    entry, limit, value, runtime_value, calls, phase,
):
    tool = FakeTool()
    agent = FakeAgent([tool])
    model = _model()
    if entry == "direct":
        runtime = build_session_runtime(
            env=local_environment_factory(), agent=agent, llm=model, **{limit: runtime_value})
        session = Session(agent, runtime=runtime, **{limit: value})
    else:
        session = build_session(env=local_environment_factory(), agent=agent, llm=model, **{limit: value})

    await session.add_user_message("Complete the assigned task.")
    await session.run_loop()

    assert len(model.calls) == calls
    assert session.phase is phase
    assert len(tool.calls) == min(calls, 1)
    assert getattr(session, limit) == value
    await session.aclose()


async def test_omitted_constructor_limits_inherit_the_runtime(local_environment_factory):
    agent = FakeAgent()
    model = FakeLLMClient([llm_response(content="completed")])
    runtime = build_session_runtime(
        env=local_environment_factory(), agent=agent, llm=model, max_budget_tokens=0, max_steps=7)
    session = Session(agent, runtime=runtime)

    await session.add_user_message("Complete the assigned task.")
    await session.run_loop()

    assert model.calls == []
    assert session.phase is SessionPhase.STOPPED
    assert session.max_budget_tokens == 0
    assert session.max_steps == 7
    await session.aclose()


@pytest.mark.parametrize("entry", ["direct", "factory"])
@pytest.mark.parametrize("limit", ["max_budget_tokens", "max_steps"])
@pytest.mark.parametrize("value", [0, 1, None])
async def test_restoration_retains_the_constructed_limits(local_environment_factory, tmp_path, entry, limit, value):
    path = tmp_path / "saved.json"
    agent = FakeAgent()
    source = build_session(
        env=local_environment_factory(), agent=agent,
        llm=FakeLLMClient([llm_response(content="saved answer")]),
    )
    await source.add_user_message("Create the saved answer.")
    await source.run_loop()
    source.save(str(path))
    await source.aclose()

    model = FakeLLMClient([llm_response(content="restored answer")])
    if entry == "direct":
        runtime = build_session_runtime(
            env=local_environment_factory(), agent=agent, llm=model, **{limit: 0 if value is None else 10_000})
        session = Session(agent, runtime=runtime, **{limit: value})
        session.restore(str(path))
    else:
        session = load_session(str(path), env=local_environment_factory(), agent=agent, llm=model, **{limit: value})
    await session.add_user_message("Continue the saved task.")
    await session.run_loop()

    assert len(model.calls) == (1 if value is None else 0)
    assert session.phase is (SessionPhase.DONE if value is None else SessionPhase.STOPPED)
    assert getattr(session, limit) == value
    await session.aclose()
