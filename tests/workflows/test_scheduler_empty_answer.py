from __future__ import annotations

import pytest

from opencollab.application.event_bus import EventBus
from opencollab.application.scheduler import Scheduler
from opencollab.application.scheduler_types import SchedulerTurnError
from opencollab.application.session_run import _EMPTY_STOP_PLACEHOLDER
from opencollab.bootstrap import build_session
from opencollab.domain.agent import Agent
from opencollab.domain.session import SessionPhase
from tests.support.session_characterization_test_support import FakeLLMClient, fake_worktree_pool, llm_response


@pytest.mark.parametrize("serialized", [False, True])
@pytest.mark.parametrize(
    ("replies", "max_steps", "expected"),
    [
        ([None, None], 100, ""),
        ([None], 1, ""),
        ([None, "The verified answer"], 100, "The verified answer"),
        ([f"Answer quoting {_EMPTY_STOP_PLACEHOLDER}"], 100, f"Answer quoting {_EMPTY_STOP_PLACEHOLDER}"),
        ([_EMPTY_STOP_PLACEHOLDER], 100, _EMPTY_STOP_PLACEHOLDER),
        ([None, _EMPTY_STOP_PLACEHOLDER], 100, _EMPTY_STOP_PLACEHOLDER),
    ],
    ids=["empty-twice", "empty-step-limit", "retry-answer", "quoted-placeholder", "same-text", "retry-same-text"],
)
async def test_scheduler_answer_filters_only_retry_placeholder(
    serialized, replies, max_steps, expected,
):
    provider = FakeLLMClient([llm_response(content=reply) for reply in replies])
    lead = build_session(
        agent=Agent(name="lead", system_prompt="Return the final task answer."),
        llm=provider, max_steps=max_steps,
    )
    lead.state.append_message({"role": "assistant", "content": "Previous answer"})
    previous_history = [message.copy() for message in lead.messages]
    scheduler = Scheduler(
        session_factory=object(), worktree_pool=fake_worktree_pool(),
        event_sink=EventBus(), serialize_turns=serialized,
    )
    scheduler.register_lead(lead)
    try:
        if max_steps == 1:
            with pytest.raises(SchedulerTurnError) as caught:
                await scheduler.run("Return the computed result.")
            assert caught.value.partial_answer is None
            assert lead.phase is SessionPhase.STOPPED
        else:
            assert await scheduler.run("Return the computed result.") == expected
            assert lead.phase is SessionPhase.DONE
        assert len(provider.calls) == len(replies)
        assert lead.messages[:len(previous_history)] == previous_history
        if replies[0] is None:
            assert any(message.get("content") == _EMPTY_STOP_PLACEHOLDER for message in lead.messages)
    finally:
        await scheduler.cleanup()
