from __future__ import annotations

import asyncio
import copy
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from opencollab.adapters.env import LocalEnvironment
from opencollab.adapters.tools.fs import FileReadTool, GrepTool
from opencollab.application.submit_findings import SubmitFindingsTool
from opencollab.bootstrap import build_session, snapshot_session
from opencollab.domain.agent import Agent
from opencollab.domain.session import SessionPhase
from tests.support.session_characterization_test_support import llm_response


class _ForkableEnvironment(LocalEnvironment):
    def fork_snapshot(self):
        directory = tempfile.mkdtemp(prefix="snapshot-", dir=Path(self.workspace).parent)
        shutil.copytree(self.workspace, directory, dirs_exist_ok=True)
        return type(self)(directory)


def _call(name, arguments, ident):
    return {
        "id": ident, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


class _MarkerModel:
    def __init__(self, wind_down):
        self.wind_down = wind_down
        self.calls = []

    async def complete(self, messages, tools=None, **kwargs):
        names = [tool["function"]["name"] for tool in tools or []]
        self.calls.append({"tools": names, "choice": copy.deepcopy(kwargs.get("tool_choice"))})
        followups = [
            index for index, message in enumerate(messages)
            if message["role"] == "user" and "Read the marker." in str(message["content"])
        ]
        if followups:
            if "file_read" not in names:
                return llm_response(content="READ_TOOL_UNAVAILABLE")
            results = [
                message["content"] for message in messages[followups[-1] + 1:]
                if message["role"] == "tool"
            ]
            if not results:
                return llm_response(tool_calls=[
                    _call("file_read", {"path": "marker.txt"}, f"read-{len(self.calls)}"),
                ], finish_reason="tool_calls")
            return llm_response(content=(
                "FOUND_MARKER" if any("EXPECTED_MARKER" in result for result in results)
                else "MARKER_MISSING"
            ))
        if not self.wind_down:
            return llm_response(content="FIRST_DONE")
        if names == ["submit_findings"]:
            return llm_response(tool_calls=[_call("submit_findings", {
                "findings": [], "summary": "No matching evidence was found.",
                "insufficient_evidence": True,
            }, f"submit-{len(self.calls)}")], finish_reason="tool_calls")
        return llm_response(tool_calls=[_call("grep", {
            "pattern": f"missing-needle-{len(self.calls)}", "path": ".",
        }, f"grep-{len(self.calls)}")], finish_reason="tool_calls")


@pytest.mark.parametrize("moment", ["ordinary", "completed_wind_down", "interrupted_wind_down"])
@pytest.mark.parametrize("tool_choice", [
    None, "auto", {"type": "function", "function": {"name": "file_read"}},
])
async def test_snapshot_restores_original_tools_only_for_the_next_user_turn(tmp_path, moment, tool_choice):
    workspace = tmp_path / "source"
    workspace.mkdir()
    (workspace / "marker.txt").write_text("EXPECTED_MARKER\n")
    source_done, branch_done = asyncio.Event(), asyncio.Event()
    held, release = asyncio.Event(), asyncio.Event()
    model = _MarkerModel(moment != "ordinary")

    async def source_events(event):
        if event.data.get("tool") != "submit_findings":
            return
        if event.type == "tool_start" and moment == "interrupted_wind_down" and not held.is_set():
            held.set()
            await release.wait()
        if event.type == "tool_end":
            source_done.set()

    def branch_events(event):
        if event.type == "tool_end" and event.data.get("tool") == "submit_findings":
            branch_done.set()

    source = build_session(
        agent=Agent(
            name="worker", system_prompt="Read the requested file.",
            tools=[FileReadTool(), GrepTool(), SubmitFindingsTool()], tool_choice=tool_choice,
        ),
        llm=model, env=_ForkableEnvironment(str(workspace)), max_budget_tokens=100_000,
        event_sink=source_events,
    )
    source.runner.configure_enforcement(enforcement_strength="needs-enforcement", commit_reserve=25_000)
    branch = running = None
    try:
        await source.add_user_message("Investigate the missing pattern.")
        running = asyncio.create_task(source.run_loop(source_done))
        if moment == "interrupted_wind_down":
            await asyncio.wait_for(held.wait(), 5)
        else:
            await asyncio.wait_for(running, 5)
        current_names = [tool.name for tool in source.agent.tools]
        expected_names = ["file_read", "grep", "submit_findings"]
        assert current_names == (expected_names if moment == "ordinary" else ["submit_findings"])
        if moment != "ordinary":
            assert [call["tools"] for call in model.calls[:3]] == [expected_names] * 3
            assert source.state.wind_down_done
            assert source.used_tokens < 100_000 - 25_000
            assert source.state.budget_reserve_consumed is False

        branch = snapshot_session(source, event_sink=branch_events)
        assert [tool.name for tool in branch.agent.tools] == current_names
        assert branch.agent.tool_choice == source.agent.tool_choice
        assert branch.state.wind_down_done == source.state.wind_down_done
        assert branch.state.wind_down_attempts == source.state.wind_down_attempts
        assert branch.state.wind_down_token_mark == source.state.wind_down_token_mark
        assert branch.state.budget_reserve_consumed == source.state.budget_reserve_consumed
        branch_submit = branch.agent.find_tool("submit_findings")
        source_submit = source.agent.find_tool("submit_findings")
        assert branch_submit is not source_submit
        assert branch_submit.captured == source_submit.captured

        if moment == "interrupted_wind_down":
            before = len(model.calls)
            await asyncio.wait_for(branch.run_loop(branch_done), 5)
            assert model.calls[before:] == [{
                "tools": ["submit_findings"],
                "choice": {"type": "function", "function": {"name": "submit_findings"}},
            }]
            assert source_submit.captured is None
            assert branch_submit.captured is not None
            release.set()
            await asyncio.wait_for(running, 5)

        for session in (source, branch):
            before = len(model.calls)
            await session.add_user_message("Read the marker.")
            assert [tool.name for tool in session.agent.tools] == expected_names
            assert session.agent.tool_choice == tool_choice
            assert session.state.wind_down_done is False
            assert await session.run_loop() == "FOUND_MARKER"
            assert session.phase is SessionPhase.DONE
            assert model.calls[before:] == [{"tools": expected_names, "choice": tool_choice}] * 2
            assert session.max_budget_tokens == 100_000
        assert branch.agent.find_tool("submit_findings") is branch_submit
        branch.agent.find_tool("file_read").max_read_chars = 123
        assert source.agent.find_tool("file_read").max_read_chars == 30_000
        if isinstance(tool_choice, dict):
            branch.agent.tool_choice["function"]["name"] = "grep"
            assert source.agent.tool_choice == tool_choice
    finally:
        release.set()
        if running is not None:
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        for session in (branch, source):
            if session is not None:
                await session.aclose()
