"""Scheduler answers belong to the latest completed turn."""

from __future__ import annotations

import asyncio

import pytest

from opencollab.domain.session import SessionPhase
from tests.support.scheduler_awaiting_test_support import (
    ScriptedSession,
    build_scheduler,
    resume_done,
    suspend_spawning,
    terminal,
)


@pytest.mark.asyncio
async def test_suspended_followup_clears_the_previous_turn_answer():
    started, release = asyncio.Event(), asyncio.Event()

    async def complete_child(session):
        started.set()
        await release.wait()
        return await terminal("child answer")(session)

    lead = ScriptedSession("lead", [
        terminal("first answer"),
        suspend_spawning([("coder", "continue", "spawn-1")]),
        resume_done(lambda results: "followup " + results[0]),
    ])
    child = ScriptedSession("coder", [complete_child])
    scheduler, _events = build_scheduler(lead, [child])
    assert await scheduler.run("first task") == "first answer"
    scb = scheduler.table.get(0)
    assert scb.answer == "first answer"

    task = asyncio.create_task(scheduler.run("followup task"))
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert scb.state.phase is SessionPhase.AWAITING_EVENTS
        assert scb.answer is None
    finally:
        release.set()
        result = await asyncio.wait_for(task, 2)

    assert result == "followup child answer"
    assert scb.answer == result
    assert scheduler.table.get(1).answer == "child answer"
