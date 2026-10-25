from __future__ import annotations

import asyncio
import copy
import json
import os
import shutil
import sys
import tempfile
from functools import partial
from pathlib import Path

import pytest

from opencollab.adapters.env import LocalEnvironment
from opencollab.adapters.safety import SandboxInterceptor
from opencollab.adapters.session_environments import SessionEnvironmentPool
from opencollab.adapters.tools.bash import BashTool
from opencollab.adapters.tools.fs import FileReadTool, FileWriteTool
from opencollab.application.event_bus import EventBus
from opencollab.application.run_control import BudgetDecision, RunControl
from opencollab.application.submit_findings import SubmitFindingsTool
from opencollab.application.tool_execution import ToolRuntime
from opencollab.application.workflow_agents import WorkflowAgentsMixin
from opencollab.application.workflow_registry import Registry, WorkflowSpec
from opencollab.bootstrap import DefaultSessionFactory, build_session, snapshot_session
from opencollab.bootstrap.agent_profiles import resolve_agent_profile
from opencollab.bootstrap.authored_workflow import AuthoredWorkflowTool
from opencollab.bootstrap.context_builder import SpawnConfig
from opencollab.bootstrap.context_policy import ContextPolicy
from opencollab.bootstrap.team_config import RoleConfig, TeamConfig
from opencollab.bootstrap.team_workflow import TeamWorkflowTool
from opencollab.domain.agent import Agent
from tests.support.session_characterization_test_support import llm_response


class _ForkableEnvironment(LocalEnvironment):
    def fork_snapshot(self):
        directory = tempfile.mkdtemp(prefix="snapshot-", dir=Path(self.workspace).parent)
        shutil.copytree(self.workspace, directory, dirs_exist_ok=True)
        return type(self)(directory)


@pytest.fixture(autouse=True)
def _quiet_nudges(monkeypatch):
    monkeypatch.setenv("OPENCOLLAB_BUDGET_NUDGE_MODE", "off")
    monkeypatch.setenv("OPENCOLLAB_WRITE_NUDGE_MODE", "off")


def _call(name, arguments, ident):
    return {
        "id": ident, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


async def _close(*sessions):
    for session in sessions:
        if session is not None:
            await session.aclose()
            await session.env.cleanup()


class _FileModel:
    def __init__(self):
        self.calls = []

    async def complete(self, messages, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        user = max(i for i, row in enumerate(messages) if row["role"] == "user")
        results = [row["content"] for row in messages[user + 1:] if row["role"] == "tool"]
        if results:
            return llm_response(content=results[-1], input_tokens=9, output_tokens=4)
        prompt = messages[user]["content"]
        if "Create" in prompt:
            name, args = "file_write", {"path": "created.txt", "mode": "create", "content": "CREATED"}
        elif "Edit" in prompt:
            name, args = "file_write", {
                "path": "marker.txt", "mode": "str_replace", "old_str": "EXPECTED_TAIL", "new_str": "EDITED_TAIL",
            }
        else:
            name, args = "file_read", {"path": "marker.txt"}
        return llm_response(tool_calls=[_call(name, args, f"file-{len(self.calls)}")],
                            finish_reason="tool_calls", input_tokens=7, output_tokens=3)


@pytest.mark.parametrize("hard_budget", [50_000, None])
async def test_snapshot_preserves_dynamic_budget_and_its_current_allowance(tmp_path, hard_budget):
    workspace = tmp_path / "source"
    workspace.mkdir()
    (workspace / "marker.txt").write_text("BUDGET_MARKER")
    decisions, events = [], []

    def decide(snapshot):
        decisions.append(snapshot)
        return BudgetDecision(soft_budget_tokens=10_000)

    source = build_session(
        agent=Agent(name="reader", system_prompt="Read the marker.", tools=[FileReadTool()]),
        env=_ForkableEnvironment(str(workspace)), llm=_FileModel(), max_budget_tokens=hard_budget,
        run_control=RunControl(
            initial_soft_budget_tokens=500, decide_budget=decide, on_event=events.append,
            history_trigger_tokens=20_000, tool_cancellation_cleanup_timeout=0.75,
        ),
    )
    source.max_budget_tokens = 1000
    branch = None
    try:
        branch = snapshot_session(source)
        task = "Read marker.txt.\n" + "Additional local task context. " * 300
        for session in (source, branch):
            before = len(source._llm.calls)
            await session.add_user_message(task)
            answer = await session.run_loop()
            assert "BUDGET_MARKER" in answer, (session.state.terminal_reason, session.messages[-3:])
            assert len(source._llm.calls) - before == 2
            assert session.runner.hard_budget_tokens == hard_budget
            assert session.max_budget_tokens == 10_000
            assert session.used_tokens == 23
        assert branch.runner._run_control.initial_soft_budget_tokens == 1000
        assert branch.runner._run_control.decide_budget is decide
        assert branch.runner._run_control.on_event is None
        assert branch.runner._run_control.history_trigger_tokens == 20_000
        assert branch.tool_execution._cancellation_cleanup_timeout == 0.75
        source_ids = {event.session_id for event in events}
        assert source_ids == {source.runner._response_session_id}
        assert {decision.session_id for decision in decisions} == {
            source.runner._response_session_id, branch.runner._response_session_id,
        }
    finally:
        await _close(branch, source)


async def test_snapshot_preserves_an_unbounded_allowance(tmp_path):
    (tmp_path / "marker.txt").write_text("UNBOUNDED_MARKER")
    source = build_session(
        agent=Agent(name="reader", system_prompt="Read.", tools=[FileReadTool()]),
        env=_ForkableEnvironment(str(tmp_path)), llm=_FileModel(), max_budget_tokens=None,
        run_control=RunControl(decide_budget=lambda snapshot: BudgetDecision(None)),
    )
    branch = None
    try:
        branch = snapshot_session(source)
        assert branch.max_budget_tokens is None
        assert branch.runner.hard_budget_tokens is None
        assert branch.runner._run_control.decide_budget is source.runner._run_control.decide_budget
        await branch.add_user_message("Read marker.txt.")
        assert "UNBOUNDED_MARKER" in await branch.run_loop()
        assert branch.max_budget_tokens is None
    finally:
        await _close(branch, source)


@pytest.mark.parametrize("policy_alias", [False, True])
async def test_single2_snapshot_reads_creates_and_edits_its_own_workspace(tmp_path, policy_alias):
    workspace = tmp_path / "source"
    workspace.mkdir()
    (workspace / "marker.txt").write_text("HEAD_" + "x" * 1600 + "_EXPECTED_TAIL\n")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    policy_root = workspace
    if policy_alias:
        policy_root = tmp_path / "source-alias"
        policy_root.symlink_to(workspace, target_is_directory=True)
    source = build_session(
        agent=Agent(name="reader", system_prompt="Read and edit the requested file.",
                    tools=[FileReadTool(), FileWriteTool()]),
        env=_ForkableEnvironment(str(workspace)), llm=_FileModel(),
        safety_policy=SandboxInterceptor(str(policy_root), allowed_dirs=[str(allowed)]),
        agent_profile=resolve_agent_profile("single2"),
        context_policy=ContextPolicy("no_history_compaction", 240),
    )
    branch = None
    try:
        await source.add_user_message("Read the marker.")
        answer = await source.run_loop()
        assert "EXPECTED_TAIL" in answer, (source.state.terminal_reason, source.messages[-3:])
        source_view = source._llm.calls[-1][-1]["content"]
        branch = snapshot_session(source)
        await branch.add_user_message("Read the marker in the branch.")
        assert "EXPECTED_TAIL" in await branch.run_loop()
        assert source._llm.calls[-1][-1]["content"] == source_view
        assert len(source_view) == 240
        for task in ("Create a file in the branch.", "Edit the marker in the branch."):
            await branch.add_user_message(task)
            assert "Error" not in await branch.run_loop()
        assert await branch.env.read_file("created.txt") == "CREATED"
        assert "EDITED_TAIL" in await branch.env.read_file("marker.txt")
        assert not (workspace / "created.txt").exists()
        assert "EXPECTED_TAIL" in (workspace / "marker.txt").read_text()
        policy = branch.tool_execution.safety_policy
        assert policy.check_path("marker.txt") == str(Path(branch.env.workspace) / "marker.txt")
        assert policy.check_path(str(allowed / "scratch")) == str(allowed / "scratch")
        with pytest.raises(PermissionError, match="escapes workspace"):
            policy.check_path(str(workspace / "marker.txt"))
        with pytest.raises(PermissionError, match="Blocked container-wide"):
            policy.check_cmd("find /")
        with pytest.raises(PermissionError, match="Blocked destructive"):
            policy.check_cmd("rm -rf /")
    finally:
        await _close(branch, source)


class _ScoutModel:
    def __init__(self):
        self.calls = []

    async def complete(self, messages, tools=None, **kwargs):
        names = [tool["function"]["name"] for tool in tools or []]
        self.calls.append((names, copy.deepcopy(kwargs.get("tool_choice"))))
        if names == ["submit_findings"]:
            return llm_response(tool_calls=[_call("submit_findings", {
                "findings": [], "summary": "No task evidence has been read.", "insufficient_evidence": True,
            }, f"submit-{len(self.calls)}")], finish_reason="tool_calls")
        return llm_response(content="READY")


@pytest.mark.parametrize("spent,forced", [(7499, False), (8001, True)])
async def test_snapshot_preserves_scout_commit_threshold_and_capture(tmp_path, spent, forced):
    model = _ScoutModel()
    source = build_session(
        agent=Agent(name="scout", system_prompt="Read evidence and submit findings.",
                    tools=[FileReadTool(), SubmitFindingsTool()]),
        env=_ForkableEnvironment(str(tmp_path)), llm=model, max_budget_tokens=10_000,
    )
    WorkflowAgentsMixin._configure_session_enforcement(source, "needs-enforcement", 2500)
    source.used_tokens = spent
    branch = None
    try:
        branch = snapshot_session(source)
        for session in (source, branch):
            before = len(model.calls)
            await session.add_user_message("Read the marker and submit the available evidence.")
            await session.run_loop()
            expected = (["submit_findings"], {"type": "function", "function": {"name": "submit_findings"}})
            assert model.calls[before] == (expected if forced else (["file_read", "submit_findings"], None))
            assert (session.agent.find_tool("submit_findings").captured is not None) is forced
            if not forced:
                assert session.used_tokens == 7501
                before = len(model.calls)
                await session.add_user_message("Submit the available evidence.")
                await session.run_loop()
                assert model.calls[before] == expected
                assert session.agent.find_tool("submit_findings").captured is not None
            if session is source:
                assert branch.agent.find_tool("submit_findings").captured is None
        assert branch.runner._enforcement_strength == "needs-enforcement"
        assert branch.runner._commit_reserve == 2500
        assert branch.agent.find_tool("submit_findings") is not source.agent.find_tool("submit_findings")
    finally:
        await _close(branch, source)


async def _wait_file(path):
    async def wait():
        while not path.exists():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), 10)


@pytest.mark.parametrize("close_first", ["source", "branch"])
@pytest.mark.parametrize("narrowed", [False, True])
async def test_snapshot_authored_workflow_processes_belong_to_each_session(
    tmp_path, monkeypatch, close_first, narrowed,
):
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", os.devnull)
    workspace = tmp_path / "source"
    workspace.mkdir()
    (workspace / "authored.py").write_text('''import asyncio
import os
from pathlib import Path

async def main(ctx, args):
    Path("entered").write_text(str(os.getpid()))
    try:
        await asyncio.sleep(300)
    finally:
        Path("cleaned").write_text("complete")
''')
    config = SpawnConfig(model="gpt-4o", provider="openai", api_key=None, base_url=None,
                         llm_timeout=60, tracer=None, event_bus=EventBus(), permission_policy=None)
    tool = AuthoredWorkflowTool(lambda: source, config, None, allow_unisolated_shell=True)
    source = build_session(
        agent=Agent(name="parent", system_prompt="Run a workflow.", tools=[BashTool(), tool]),
        env=_ForkableEnvironment(str(workspace)), llm=_FileModel(), max_budget_tokens=1000,
    )
    branch = None
    try:
        with pytest.raises(ValueError, match="unknown child"):
            await tool.execute_with_runtime({"action": "status", "id": "unknown"}, ToolRuntime(source.env, None, None))
        if narrowed:
            source.agent.tools = []
        branch = snapshot_session(source)
        if narrowed:
            assert branch.agent.tools == []
            for session in (source, branch):
                await session.add_user_message("Run the owned workflow.")
        branch_manager = branch.agent.find_tool("manage_workflow")
        assert branch_manager is branch.runner._initial_agent_tools[-1]
        children, pids = {}, {}
        for label, session in (("source", source), ("branch", branch)):
            manager = session.agent.find_tool("manage_workflow")
            launched = json.loads(await manager.execute_with_runtime({
                "action": "launch", "source": "authored.py", "python": sys.executable, "budget": 400,
            }, ToolRuntime(session.env, None, None)))
            await _wait_file(Path(session.env.workspace) / "entered")
            children[label] = manager._children[launched["id"]]
            pids[label] = int((Path(session.env.workspace) / "entered").read_text())
        first, other = (source, branch) if close_first == "source" else (branch, source)
        other_label = "branch" if close_first == "source" else "source"
        await first.aclose()
        assert children[close_first].execution.done()
        assert (Path(first.env.workspace) / "cleaned").read_text() == "complete"
        with pytest.raises(ProcessLookupError):
            os.kill(pids[close_first], 0)
        assert not children[other_label].execution.done()
        os.kill(pids[other_label], 0)
        assert other.runner.remaining_budget_tokens == 600
        await other.aclose()
        assert children[other_label].execution.done()
        assert (Path(other.env.workspace) / "cleaned").read_text() == "complete"
        with pytest.raises(ProcessLookupError):
            os.kill(pids[other_label], 0)
    finally:
        await _close(branch, source)


async def test_snapshot_registered_workflow_charges_its_own_session(tmp_path):
    workspace = tmp_path / "source"
    workspace.mkdir()
    (workspace / "marker.txt").write_text("SOURCE_MARKER")

    async def read_marker(ctx, args):
        return await ctx.agent("Read marker.txt.", tools=[FileReadTool()])

    registry = Registry()
    registry.register(WorkflowSpec("read-marker", "Read the marker.", read_marker))
    config = SpawnConfig(model="gpt-4o", provider="openai", api_key=None, base_url=None,
                         llm_timeout=60, tracer=None, event_bus=EventBus(), permission_policy=None)
    tool = TeamWorkflowTool(lambda: source, registry, config, None)
    source = build_session(
        agent=Agent(name="parent", system_prompt="Run a workflow.", tools=[FileReadTool(), tool]),
        env=_ForkableEnvironment(str(workspace)), llm=_FileModel(), max_budget_tokens=100_000,
    )
    branch = None
    try:
        branch = snapshot_session(source)
        await branch.env.write_file("marker.txt", "BRANCH_MARKER")
        manager = branch.agent.find_tool("run_workflow")
        assert manager is branch.runner._initial_agent_tools[-1]
        response = json.loads(await manager.execute_with_runtime(
            {"name": "read-marker", "args": {}}, ToolRuntime(branch.env, None, None),
        ))
        assert "BRANCH_MARKER" in response["output"]
        assert response["tokens"] == branch.used_tokens == 23
        assert source.used_tokens == 0
        assert await source.env.read_file("marker.txt") == "SOURCE_MARKER"
    finally:
        await _close(branch, source)


class _SessionOwner:
    def get_session(self):
        return self.session


@pytest.mark.parametrize("tool_type", [AuthoredWorkflowTool, TeamWorkflowTool])
async def test_snapshot_workflow_bound_method_keeps_its_owner_out_of_the_copy(tmp_path, tool_type):
    owner = _SessionOwner()
    config = SpawnConfig(model="gpt-4o", provider="openai", api_key=None, base_url=None,
                         llm_timeout=60, tracer=None, event_bus=EventBus(), permission_policy=None)
    tool = (
        TeamWorkflowTool(owner.get_session, Registry(), config, None)
        if tool_type is TeamWorkflowTool else AuthoredWorkflowTool(owner.get_session, config, None)
    )
    source = build_session(
        agent=Agent(name="parent", system_prompt="Run a workflow.", tools=[tool]),
        env=_ForkableEnvironment(str(tmp_path)), llm=_FileModel(),
    )
    owner.session = source
    branch = None
    try:
        branch = snapshot_session(source)
        manager = branch.agent.find_tool(tool.name)
        assert manager is branch.runner._initial_agent_tools[0]
        assert manager._get_session() is branch
        assert tool._get_session() is source
    finally:
        await _close(branch, source)


@pytest.mark.parametrize("close_first", ["source", "branch"])
@pytest.mark.parametrize("bound_factory", [False, True])
async def test_snapshot_factory_preserves_environment_providers_and_current_catalog(
    tmp_path, monkeypatch, close_first, bound_factory,
):
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", os.devnull)
    workspace = tmp_path / "source"
    workspace.mkdir()
    (workspace / "marker.txt").write_text("SOURCE_MARKER\n")
    (workspace / "authored.py").write_text(
        'from pathlib import Path\n'
        'async def main(ctx, args):\n'
        '    marker = Path("marker.txt").read_text()\n'
        '    Path("workflow-marker.txt").write_text(marker)\n'
        '    return marker\n'
    )
    environment = _ForkableEnvironment(str(workspace))
    pool = SessionEnvironmentPool(str(workspace), isolation="worktree", base_environment=environment)
    pool.set_source(environment, isolation="shared")
    provider = pool.child_pool if bound_factory else lambda env, *, role: pool.child_pool(env, role=role)
    config = SpawnConfig(model="gpt-4o", provider="openai", api_key="offline-test",  # pragma: allowlist secret
                         base_url="http://127.0.0.1:9/v1", llm_timeout=1, tracer=None,
                         event_bus=EventBus(), permission_policy=None)
    factory = DefaultSessionFactory(
        config, team_cfg=TeamConfig(roles={
            "worker": RoleConfig(prompt="Read the marker.", tools=["bash", "manage_workflow"]),
        }), environment_pool_factory=provider, environment_catalog=pool.describe,
        lead_workspace=str(workspace), lead_environment=environment, allow_unisolated_child_shell=True,
    )
    source = factory.build_spawn_session(role="worker", env=environment, budget=10_000)
    branch = None
    try:
        source_manager = source.agent.find_tool("manage_workflow")
        assert source_manager._catalog()["default_isolation"] == "shared"
        branch = snapshot_session(source)
        await branch.env.write_file("marker.txt", "BRANCH_MARKER\n")
        manager = branch.agent.find_tool("manage_workflow")
        assert manager is branch.runner._initial_agent_tools[-1]
        assert manager._get_session() is branch
        assert manager._environment_pool_factory is provider
        assert manager._catalog() == pool.describe(branch.env, role="worker")
        schema = manager.to_openai_schema()["function"]["parameters"]["properties"]["isolation"]
        assert schema["default"] == "worktree"
        source_schema = source_manager.to_openai_schema()["function"]["parameters"]["properties"]["isolation"]
        assert source_schema["default"] == "shared"
        description = json.loads(await manager.execute_with_runtime(
            {"action": "describe"}, ToolRuntime(branch.env, None, None),
        ))
        assert description["default_isolation"] == "worktree"

        child_pool = manager._environment_pool_factory(branch.env, role="worker")
        try:
            assert child_pool.default_isolation == "worktree"
            child = await child_pool.acquire("child")
            assert child is not branch.env
            assert child.workspace != branch.env.workspace
            assert await child.read_file("marker.txt") == "BRANCH_MARKER\n"
            await child.write_file("marker.txt", "CHILD_MARKER\n")
            outcome = await child.exec_cmd("cat marker.txt")
            assert outcome.returncode == 0 and outcome.stdout == "CHILD_MARKER\n"
            patch = await child.get_diff()
            assert "-BRANCH_MARKER" in patch and "+CHILD_MARKER" in patch
            assert await branch.env.read_file("marker.txt") == "BRANCH_MARKER\n"
            assert await source.env.read_file("marker.txt") == "SOURCE_MARKER\n"
            await child_pool.release_env(child)
        finally:
            await child_pool.release()

        first, other = (source, branch) if close_first == "source" else (branch, source)
        await first.aclose()
        await first.env.cleanup()
        manager = other.agent.find_tool("manage_workflow")
        runtime = ToolRuntime(other.env, None, None)
        launched = json.loads(await manager.execute_with_runtime({
            "action": "launch", "source": "authored.py", "python": sys.executable,
        }, runtime))
        finished = json.loads(await manager.execute_with_runtime({
            "action": "wait", "id": launched["id"], "timeout": 20,
        }, runtime))
        expected = "BRANCH_MARKER\n" if other is branch else "SOURCE_MARKER\n"
        assert finished["status"] == "completed", finished
        assert finished["result"]["output"] == expected
        assert await other.env.read_file("workflow-marker.txt") == expected
        assert not (Path(first.env.workspace) / "workflow-marker.txt").exists()
        child = manager._children[launched["id"]]
        assert child.bridge._pool._source is other.env
        assert child.bridge._pool.default_isolation == manager._catalog()["default_isolation"]
        assert manager._reserved_tokens() == 0
    finally:
        await _close(branch, source)
        await pool.release()


async def test_snapshot_preserves_legacy_zero_argument_environment_catalog(tmp_path):
    environment = _ForkableEnvironment(str(tmp_path))
    pool = SessionEnvironmentPool(str(tmp_path), isolation="worktree", base_environment=environment)
    pool.set_source(environment, isolation="shared")
    catalog = partial(pool.describe, environment, role="worker")
    config = SpawnConfig(model="gpt-4o", provider="openai", api_key=None, base_url=None,
                         llm_timeout=60, tracer=None, event_bus=EventBus(), permission_policy=None)
    tool = AuthoredWorkflowTool(lambda: source, config, None, environment_catalog=catalog)
    source = build_session(
        agent=Agent(name="worker", system_prompt="Run a workflow.", tools=[tool]),
        env=environment, llm=_FileModel(),
    )
    branch = None
    try:
        assert tool._catalog() == catalog()
        branch = snapshot_session(source)
        manager = branch.agent.find_tool("manage_workflow")
        assert manager._environment_catalog is catalog
        assert manager._catalog() == catalog()
        assert manager.to_openai_schema()["function"]["parameters"]["properties"]["isolation"]["default"] == "shared"
    finally:
        await _close(branch, source)
        await pool.release()
