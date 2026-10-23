from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from opencollab.adapters.llm.types import LLMResponse, Usage
from opencollab.bootstrap import container
from opencollab.bootstrap.team_config import default_team_config
from opencollab.bootstrap.tool_registry import build_tools_for_role
from opencollab.sdk import OpenCollab
from tests.support.environment_test_support import local_environment_factory as local_environment_factory


def _call(name, args):
    return {"id": "call", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


@pytest.mark.parametrize(("prebuild", "delegate"), [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize(("fail", "escape"), [(False, False), (True, False), (False, True)])
async def test_team_runs_nested_workflow_and_accounts_usage(
    local_environment_factory, tmp_path, monkeypatch, prebuild, delegate, fail, escape,
):
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "nested.py").write_text('''
from opencollab.workflows import workflow

async def child(ctx, args):
    return await ctx.agent(args["goal"], tools="coding")

@workflow
async def nested(ctx, args):
    answer = await child(ctx, args)
    if args.get("fail"):
        raise ValueError("child failed after using tokens")
    return {"answer": answer, "budget": ctx.budget.total}
''')
    team = tmp_path / "team.yaml"
    team.write_text('''entry: lead
roles:
  lead:
    prompt: Team caller
    tools: [run_workflow]
''')
    if delegate:
        team.write_text(team.read_text().replace("entry: lead", "entry: manager") + """
  manager:
    prompt: Team manager
    tools: [spawn_agent]
topology:
  manager: [lead]
""")
    observed = {}

    class OfflineLLM:
        def __init__(self, **kwargs):
            pass

        def context_window(self):
            return 200_000

        async def close(self):
            pass

        async def complete(self, messages, **kwargs):
            usage = Usage(input_tokens=10, output_tokens=5)
            if messages[0]["content"].startswith("Team manager"):
                if not any(m["role"] == "tool" for m in messages):
                    return LLMResponse(tool_calls=[_call("spawn_agent", {
                        "role": "lead", "task": "delegate to workflow",
                    })], usage=usage, finish_reason="tool_calls")
                return LLMResponse(content="collected", usage=usage, finish_reason="stop")
            if messages[0]["content"] == "Team caller":
                replies = [m for m in messages if m["role"] == "tool"]
                if not replies:
                    names = {t["function"]["name"] for t in kwargs["tools"]}
                    assert names == {"run_workflow"}
                    return LLMResponse(tool_calls=[_call("run_workflow", {
                        "name": "nested", "args": {"goal": "write marker", "fail": fail},
                        "budget": 1_000_000,
                    })], usage=usage, finish_reason="tool_calls")
                observed["reply"] = replies[-1]["content"]
                return LLMResponse(content="collected", usage=usage, finish_reason="stop")
            replies = [m for m in messages if m["role"] == "tool"]
            if not replies:
                names = {t["function"]["name"] for t in kwargs["tools"]}
                assert "run_workflow" not in names
                assert "spawn_agent" not in names
                return LLMResponse(tool_calls=[_call("file_write", {
                    "mode": "create", "path": "../outside.txt" if escape else "marker.txt",
                    "content": "from nested workflow",
                })], usage=usage, finish_reason="tool_calls")
            observed["child_reply"] = replies[-1]["content"]
            return LLMResponse(content="child result", usage=usage, finish_reason="stop")

    monkeypatch.setattr(container, "LLMClient", OfflineLLM)
    workspace = tmp_path / "actual"
    workspace.mkdir()
    env = local_environment_factory(str(workspace))
    result = await OpenCollab(tmp_path, environment=env, config={
        "model": "gpt-4o", "provider": "openai", "api_key": "test-key",  # pragma: allowlist secret
        "budget": 100_000,
    }).team("delegate", config=team, artifacts=tmp_path / "run", trace=False,
            use_worktrees=False, prebuild_team=prebuild)
    assert result.output == "collected"
    assert result.tokens == (90 if delegate else 60)
    if escape:
        assert not (tmp_path / "outside.txt").exists()
        assert "Permission denied" in observed["child_reply"]
    else:
        assert (workspace / "marker.txt").read_text() == "from nested workflow"
    assert not (tmp_path / "marker.txt").exists()
    if fail:
        assert "child failed after using tokens" in observed["reply"]
    else:
        output = json.loads(observed["reply"])
        assert output["output"]["answer"] == "child result"
        assert 0 < output["output"]["budget"] <= 99_985
        if not delegate:
            assert output["output"]["budget"] == 99_985
        assert output["tokens"] == 30
        manifest = json.loads((Path(output["artifacts"]) / "workflow.json").read_text())
        assert manifest["status"] == "completed"


def test_workflow_dispatch_requires_explicit_team_permission():
    assert all("run_workflow" not in r.tools for r in default_team_config().roles.values())
    with pytest.raises(ValueError, match="requires a Team session"):
        build_tools_for_role(["run_workflow"])


async def test_cancelled_workflow_quiesces_before_tool_finishes(local_environment_factory, tmp_path):
    from opencollab.application.event_bus import EventBus
    from opencollab.application.tool_execution import ToolRuntime
    from opencollab.application.workflow_registry import Registry, WorkflowSpec
    from opencollab.bootstrap.context_builder import SpawnConfig
    from opencollab.bootstrap.session_factory import build_session
    from opencollab.bootstrap.team_workflow import TeamWorkflowTool
    from opencollab.domain.agent import Agent

    entered = asyncio.Event()
    stopped = asyncio.Event()

    class WaitingLLM:
        def context_window(self):
            return 200_000

        async def complete(self, messages, **kwargs):
            if not any(m["role"] == "tool" for m in messages):
                return LLMResponse(tool_calls=[_call("file_read", {"path": "marker.txt"})],
                                   usage=Usage(input_tokens=10, output_tokens=5),
                                   finish_reason="tool_calls")
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                stopped.set()

    async def flow(ctx, args):
        return await ctx.agent("wait", tools="read")

    registry = Registry()
    registry.register(WorkflowSpec(name="waiting", description="wait", fn=flow))
    env = local_environment_factory(str(tmp_path))
    parent = build_session(agent=Agent(name="caller", system_prompt="caller", tools=[]),
                           env=env, max_budget_tokens=100_000, llm=WaitingLLM())
    config = SpawnConfig(model="gpt-4o", provider="openai", api_key=None, base_url=None,
                         llm_timeout=60, tracer=None, event_bus=EventBus(), permission_policy=None)
    tool = TeamWorkflowTool(lambda: parent, registry, config, None)
    task = asyncio.create_task(tool.execute_with_runtime(
        {"name": "waiting", "args": {}}, ToolRuntime(env, None, None)))
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert stopped.is_set()
    assert parent.used_tokens == 15
    assert not env.revoked


@pytest.mark.parametrize(
    "mode", ["agent", "agent_run", "structured", "parallel", "candidate_agent", "candidate_workflow"],
)
@pytest.mark.parametrize("release_response", [False, True])
async def test_user_stop_cancels_workflow_before_further_execution(
    local_environment_factory, tmp_path, mode, release_response,
):
    import subprocess

    from opencollab.adapters.candidate_workspace import EnvCandidateWorkspace
    from opencollab.application.event_bus import EventBus
    from opencollab.application.scheduler_types import SchedulerTurnError
    from opencollab.application.workflow_registry import Registry, WorkflowSpec
    from opencollab.bootstrap.context_builder import SpawnConfig
    from opencollab.bootstrap.session_factory import build_session
    from opencollab.bootstrap.team_workflow import TeamWorkflowTool
    from opencollab.domain.agent import Agent
    from opencollab.domain.session import SessionPhase
    from tests.support.scheduler_awaiting_test_support import build_scheduler

    if mode.startswith("candidate"):
        for args in (("init",), ("add", "."),
                     ("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                      "commit", "--allow-empty", "-m", "initial")):
            subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    entered, release, stopped, cancel = (asyncio.Event() for _ in range(4))
    requests = 0
    child_workspaces = []

    class WaitingLLM:
        def context_window(self):
            return 200_000

        async def complete(self, messages, **kwargs):
            nonlocal requests
            usage = Usage(input_tokens=10, output_tokens=5)
            if messages[0]["content"] == "caller":
                return LLMResponse(tool_calls=[_call("run_workflow", {
                    "name": "waiting", "args": {},
                })], usage=usage, finish_reason="tool_calls")
            requests += 1
            if any(message["role"] == "tool" for message in messages):
                return LLMResponse(content="finished", usage=usage, finish_reason="stop")
            entered.set()
            try:
                await release.wait()
            finally:
                stopped.set()
            return LLMResponse(tool_calls=[_call("file_write", {
                "mode": "create", "path": "after-cancel.txt", "content": "unexpected",
            })], usage=usage, finish_reason="tool_calls")

    env = local_environment_factory(str(tmp_path))

    async def child(ctx, args):
        child_workspaces.append(ctx.workspace_root)
        return await ctx.agent("write", tools="coding")

    async def flow(ctx, args):
        if mode.startswith("candidate"):
            ctx._candidate_workspace = EnvCandidateWorkspace(env)
        if mode == "candidate_workflow":
            return await ctx.candidate_workflow(child, {}, label="candidate")
        if mode == "candidate_agent":
            from opencollab.bootstrap.programmatic import resolve_tools

            return await ctx.candidate_agent("write", label="candidate", tools=resolve_tools("coding"))
        if mode == "parallel":
            return await ctx.parallel([lambda: child(ctx, {})])
        if mode == "agent_run":
            return await ctx.agent_run("write", tools="coding")
        if mode == "structured":
            return await ctx.agent("write", tools="coding", schema={"type": "object"})
        return await child(ctx, {})

    registry = Registry()
    registry.register(WorkflowSpec(name="waiting", description="wait", fn=flow))
    config = SpawnConfig(model="gpt-4o", provider="openai", api_key=None, base_url=None,
                         llm_timeout=60, tracer=None, event_bus=EventBus(), permission_policy=None)
    tool = TeamWorkflowTool(lambda: parent, registry, config, None)
    parent = build_session(agent=Agent(name="caller", system_prompt="caller", tools=[tool]),
                           env=env, max_budget_tokens=100_000, llm=WaitingLLM())
    scheduler, _ = build_scheduler(parent, [])
    task = asyncio.create_task(scheduler.run("write", cancel_event=cancel))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        cancel.set()
        if release_response:
            release.set()
        with pytest.raises(SchedulerTurnError, match="interrupted by user"):
            await asyncio.wait_for(task, 3)
        assert stopped.is_set()
        assert requests == 1
        assert parent.phase is SessionPhase.STOPPED
        assert parent.used_tokens == (30 if release_response else 15)
        assert not (tmp_path / "after-cancel.txt").exists()
        assert all(not (Path(path) / "after-cancel.txt").exists() for path in child_workspaces)
        assert not env.revoked
        await env.write_file("retry.txt", "usable")
        assert await env.read_file("retry.txt") == "usable"
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await scheduler.cleanup()
