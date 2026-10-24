from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

from opencollab.adapters.env import LocalEnvironment
from opencollab.adapters.llm.types import LLMResponse, Usage
from opencollab.adapters.tools.fs import FileWriteTool
from opencollab.adapters.tools.message import MessageAgentTool
from opencollab.adapters.tools.use_skill import UseSkillTool
from opencollab.application.event_bus import EventBus
from opencollab.application.tool_execution import ToolRuntime
from opencollab.bootstrap.authored_workflow import AuthoredWorkflowTool
from opencollab.bootstrap.context_builder import ContextBuilder, SpawnConfig
from opencollab.bootstrap.team_config import RoleConfig, TeamConfig
from opencollab.domain.skill import SkillManifest
from opencollab.domain.team import Topology
from opencollab.sdk import OpenCollab
from tests.support.environment_test_support import local_environment_factory as local_environment_factory
from tests.support.paths import REPO_ROOT


def _config():
    return SpawnConfig(model="gpt-4o", provider="openai", api_key=None, base_url=None,
                       llm_timeout=60, tracer=None, event_bus=EventBus(), permission_policy=None)


class _Model:
    def __init__(self, **kwargs):
        pass

    def context_window(self):
        return 200_000

    async def close(self):
        pass


class _Skills:
    def list_manifests(self):
        return (SkillManifest(name="inspect-query-plan", description="Analyze database query plans."),)

    def get_body(self, name):
        return "Inspect the execution plan before changing indexes." if name == "inspect-query-plan" else None


async def test_skill_can_be_discovered_and_loaded_from_its_tool_schema():
    tool = UseSkillTool(_Skills())
    schema = tool.to_openai_schema()["function"]
    options = schema["parameters"]["properties"]["name"].get("enum", [])
    assert options == ["inspect-query-plan"]
    assert "query plans" in json.dumps(schema)
    result = await tool.execute_with_runtime({"name": options[0]}, ToolRuntime(None, None, None))
    assert result == "Inspect the execution plan before changing indexes."


def test_open_topology_reveals_configured_specialists():
    team = TeamConfig(entry="lead", roles={
        "lead": RoleConfig(prompt="Choose a specialist for the task.", tools=["spawn_agent"]),
        "query_planner": RoleConfig(prompt="Analyze database query plans.", tools=["file_read"]),
    }, topology=Topology(allow_all=True))
    agent = ContextBuilder(team, _config()).build_agent("lead", scheduler=object())
    assert "query_planner" in agent.system_prompt
    assert "- lead" in agent.system_prompt
    assert "Analyze database query plans" not in agent.system_prompt


def test_message_only_role_context_describes_only_granted_tools():
    team = TeamConfig(entry="lead", roles={
        "lead": RoleConfig(prompt="Coordinate the team.", tools=["message_agent"]),
        "worker": RoleConfig(prompt="Analyze the task.", tools=[]),
    }, topology=Topology(edges={"lead": frozenset({"worker"})}))
    agent = ContextBuilder(team, _config()).build_agent("lead", scheduler=object())
    assert "message_agent" in agent.system_prompt
    assert "team_status" not in agent.system_prompt
    assert "spawn" not in agent.system_prompt.lower()


async def test_serialized_message_explains_how_to_yield_without_submit():
    class Scheduler:
        turns_serialized = True

        async def send_message(self, *args):
            return "Message queued."

    tool = MessageAgentTool(Scheduler())
    result = await tool.execute_with_runtime(
        {"to_aid": 1, "summary": "Inspect", "content": "Inspect the query plan."},
        ToolRuntime(None, None, None, aid=0),
    )
    assert "final response without tool calls" in result
    assert "Call submit now" not in result


async def test_restricted_file_writer_advertises_an_executable_mode(local_environment_factory, tmp_path):
    target = tmp_path / "value.txt"
    target.write_text("before\n")
    environment = local_environment_factory(str(tmp_path))
    tool = FileWriteTool(allow_create=False)
    modes = tool.to_openai_schema()["function"]["parameters"]["properties"]["mode"]["enum"]
    assert modes == ["str_replace"]
    result = await tool.execute_with_runtime(
        {"path": "value.txt", "mode": modes[0], "old_str": "before", "new_str": "after"},
        ToolRuntime(environment, None, None),
    )
    assert "content changed" in result
    assert target.read_text() == "after\n"


async def test_read_only_agent_prompt_does_not_claim_unavailable_shell(tmp_path):
    observed = {}

    class Model(_Model):
        async def complete(self, messages, **kwargs):
            observed.update(system=messages[0]["content"],
                            tools=[t["function"]["name"] for t in kwargs["tools"]])
            return LLMResponse(content="Reviewed", finish_reason="stop",
                               usage=Usage(input_tokens=10, output_tokens=5))

    result = await OpenCollab(tmp_path, model="gpt-4o", provider="openai").agent(
        "Review the existing source.", tools="read", llm=Model(), trace=False,
    )
    assert result.output == "Reviewed"
    assert "bash" not in observed["tools"]
    assert "This session provides `bash`" not in observed["system"]
    assert "You have a shell" not in observed["system"]


def test_authored_workflow_tool_offers_discovery_before_execution():
    parent = SimpleNamespace(agent=SimpleNamespace(name="writer"))
    tool = AuthoredWorkflowTool(lambda: parent, _config(), None)
    schema = tool.to_openai_schema()["function"]
    assert "describe" in schema["parameters"]["properties"]["action"]["enum"]
    assert "worktree" in schema["parameters"]["properties"]["isolation"].get("enum", [])


def test_file_writer_schema_tracks_inherited_restrictions_without_changing_other_instances():
    restricted, unrestricted = FileWriteTool(), FileWriteTool()
    restricted.allow_create = False
    assert restricted.to_openai_schema()["function"]["parameters"]["properties"]["mode"]["enum"] == ["str_replace"]
    assert unrestricted.to_openai_schema()["function"]["parameters"]["properties"]["mode"]["enum"] == [
        "create", "str_replace",
    ]


async def test_prebuilt_context_directs_members_to_existing_teammates(tmp_path, monkeypatch):
    from opencollab.bootstrap import container

    team = tmp_path / "team.yaml"
    team.write_text("entry: lead\nroles:\n  lead:\n    prompt: Coordinate.\n"
                    "    tools: [spawn_agent, message_agent]\n  worker:\n"
                    "    prompt: Analyze the task.\n    tools: []\ntopology:\n  lead: [worker]\n")

    class Model(_Model):
        async def complete(self, messages, **kwargs):
            prompt = "\n".join(item["content"] for item in messages if item["role"] == "system")
            assert "already seated" in prompt
            assert "New agents cannot be spawned" in prompt
            assert "Use `spawn_agent`" not in prompt
            assert "message_agent" in prompt
            return _reply("Ready")

    monkeypatch.setattr(container, "LLMClient", Model)
    result = await OpenCollab(tmp_path, model="gpt-4o").team(
        "Inspect the configured team", config=team, prebuild_team=True, use_worktrees=False, trace=False,
    )
    assert result.output == "Ready"


def _call(name, arguments):
    return {"id": "call", "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments),
    }}


def _reply(content=None, tool=None):
    return LLMResponse(content=content, tool_calls=[tool] if tool else [],
                       finish_reason="tool_calls" if tool else "stop", usage=Usage(input_tokens=10, output_tokens=5))


async def test_team_discovers_workflow_inputs_before_executing(tmp_path, monkeypatch):
    from opencollab.bootstrap import container

    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "multiply.py").write_text('''from opencollab.workflows import workflow

@workflow
async def multiply(ctx, args):
    """Required input factor is an integer. Example args: {"factor": 21}."""
    return args["factor"] * 2
''')
    team = tmp_path / "team.yaml"
    team.write_text("entry: lead\nroles:\n  lead:\n    prompt: Find the workflow inputs.\n    tools: [run_workflow]\n")
    observed = []

    class Model(_Model):
        async def complete(self, messages, **kwargs):
            replies = [item["content"] for item in messages if item["role"] == "tool"]
            if not replies:
                schema = kwargs["tools"][0]["function"]["parameters"]
                assert "describe" in schema["properties"]["action"]["enum"]
                assert "multiply" in schema["properties"]["name"]["enum"]
                return _reply(tool=_call("run_workflow", {"name": "multiply", "action": "describe"}))
            value = json.loads(replies[-1])
            observed.append(value)
            if len(replies) == 1:
                assert '"factor": 21' in value["inputs"]
                return _reply(tool=_call("run_workflow", {"name": "multiply", "args": {"factor": 21}}))
            assert value["output"] == 42
            return _reply("Collected 42")

    monkeypatch.setattr(container, "LLMClient", Model)
    result = await OpenCollab(tmp_path, model="gpt-4o").team(
        "Run the described workflow", config=team, trace=False, use_worktrees=False,
    )
    assert result.output == "Collected 42"
    assert len(observed) == 2
    assert observed[-1]["tokens"] == 0


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("isolation", [None, "shared"])
async def test_team_describes_configured_vm_strategies_without_allocating_them(
    tmp_path, monkeypatch, configured, isolation,
):
    from opencollab.bootstrap import container

    team = tmp_path / "team.yaml"
    team.write_text("entry: lead\nroles:\n  lead:\n    prompt: Inspect the environment.\n"
                    "    tools: [bash, manage_workflow]\n")
    descriptions = []

    def provider(**kwargs):
        pytest.fail("Describing configured strategies must not allocate a resource")

    class Model(_Model):
        async def complete(self, messages, **kwargs):
            replies = [item["content"] for item in messages if item["role"] == "tool"]
            tool = next(item["function"] for item in kwargs["tools"] if item["function"]["name"] == "manage_workflow")
            assert ("vm-session" in tool["parameters"]["properties"]["isolation"]["enum"]) is configured
            if not replies:
                return _reply(tool=_call("manage_workflow", {"action": "describe"}))
            value = json.loads(replies[-1])
            descriptions.append(value)
            assert value["default_isolation"] == "shared"
            assert ("vm-session" in [row["name"] for row in value["strategies"]]) is configured
            assert all(row["availability"] == "checked_on_allocation" for row in value["strategies"])
            assert value["launch_allowed_in_current_environment"] is False
            assert value["builtin_child_tools"] == ["bash"]
            return _reply("Inspected")

    monkeypatch.setattr(container, "LLMClient", Model)
    result = await OpenCollab(tmp_path, model="gpt-4o", environment_providers=(
        {"vm-session": provider} if configured else {}
    )).team("Describe the configured execution environment", config=team, trace=False, isolation=isolation)
    assert result.output == "Inspected"
    assert len(descriptions) == 1


async def test_member_learns_authoring_interface_and_runs_generated_source(tmp_path, monkeypatch):
    from opencollab.bootstrap import container

    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", os.devnull)
    team = tmp_path / "team.yaml"
    team.write_text("entry: lead\nroles:\n  lead:\n    prompt: Author a workflow.\n"
                    "    tools: [bash, file_write, manage_workflow]\n")
    observed = {}

    class Model(_Model):
        async def complete(self, messages, **kwargs):
            if not messages[0]["content"].startswith("Author a workflow."):
                return _reply("child result")
            replies = [item["content"] for item in messages if item["role"] == "tool"]
            if not replies:
                return _reply(tool=_call("manage_workflow", {"action": "describe"}))
            if len(replies) == 1:
                value = json.loads(replies[-1])
                assert value["launch_allowed_in_current_environment"] is True
                observed["source"] = value["example"]
                return _reply(tool=_call("file_write", {
                    "mode": "create", "path": "generated.py", "content": value["example"],
                }))
            if len(replies) == 2:
                return _reply(tool=_call("manage_workflow", {
                    "action": "launch", "source": "generated.py", "python": sys.executable,
                    "args": {"tasks": ["first", "second"]}, "isolation": "shared",
                }))
            if len(replies) == 3:
                return _reply(tool=_call("manage_workflow", {"action": "wait", "id": json.loads(replies[-1])["id"]}))
            observed["result"] = json.loads(replies[-1])
            assert observed["result"]["status"] == "completed"
            assert observed["result"]["result"]["output"] == ["child result", "child result"], observed["result"]
            return _reply("Collected")

    monkeypatch.setattr(container.LLMClient, "complete", Model.complete)
    environment = LocalEnvironment(str(tmp_path))
    try:
        client = OpenCollab(tmp_path, model="gpt-4o", environment=environment,
                            api_key="offline-test")  # pragma: allowlist secret
        result = await client.team(
            "Write and execute the discovered example", config=team, trace=False,
            use_worktrees=False, allow_unisolated_shell=True,
        )
        assert result.output == "Collected"
        assert (tmp_path / "generated.py").read_text() == observed["source"]
        assert observed["result"]["tokens"] == 30
    finally:
        await environment.cleanup()


def test_headless_analyst_instructions_make_human_questions_conditional():
    from opencollab.bootstrap.team_config import default_team_config

    team = default_team_config()
    agent = ContextBuilder(team, _config()).build_agent(team.entry, scheduler=object())
    assert "ask_user" not in {tool.name for tool in agent.tools}
    assert "when that tool is available" in agent.system_prompt
    assert "Report a required unresolved decision" in agent.system_prompt


@pytest.mark.parametrize(("name", "required_terms"), [
    ("duo", ("goal", "description", "submission_mode")),
    ("weave", ("groups", "id", "prompt", "check_commands", "expected_output")),
    ("reconstruct", ("task", "module:callable", "task_args")),
])
async def test_builtin_workflow_inputs_are_available_without_running_it(name, required_terms):
    from opencollab.bootstrap.team_workflow import TeamWorkflowTool
    from opencollab.builtin_workflows import get_builtin_workflows

    def parent():
        pytest.fail("Describing a workflow must not open or charge a parent session")

    tool = TeamWorkflowTool(parent, get_builtin_workflows(), _config(), None)
    value = json.loads(await tool.execute_with_runtime({"action": "describe", "name": name}, None))
    assert all(term in value["inputs"] for term in required_terms)


@pytest.mark.parametrize("protocol", ["chat_completions", "responses", "anthropic"])
def test_provider_adapters_retain_discovered_options(protocol):
    from opencollab.adapters.llm.anthropic_provider import _convert_tool
    from opencollab.adapters.llm.responses_provider import _responses_tools
    from opencollab.adapters.llm.tool_contracts import normalize_function_tools

    original = UseSkillTool(_Skills()).to_openai_schema()
    if protocol == "anthropic":
        parameters = _convert_tool(original)["input_schema"]
    elif protocol == "responses":
        parameters = _responses_tools([original])[0]["parameters"]
    else:
        parameters = normalize_function_tools([original])[0]["function"]["parameters"]
    assert parameters == original["function"]["parameters"]
    assert parameters["properties"]["name"]["enum"] == ["inspect-query-plan"]


def test_skill_catalog_refresh_preserves_existing_schema_constraints():
    store = _Skills()
    tool = UseSkillTool(store)
    tool.parameters = {**tool.parameters, "additionalProperties": False}
    assert tool.to_openai_schema()["function"]["parameters"]["additionalProperties"] is False
    store.list_manifests = lambda: (SkillManifest(name="new-skill", description="A new operation."),)
    assert tool.to_openai_schema()["function"]["parameters"]["properties"]["name"]["enum"] == ["new-skill"]


@pytest.mark.parametrize("requires_sandbox", [True, False])
async def test_model_is_told_when_shell_policy_blocks_the_current_environment(
    tmp_path, local_environment_factory, requires_sandbox,
):
    from opencollab.adapters.tools.bash import BashTool

    env = local_environment_factory(str(tmp_path))
    tool = BashTool(require_process_isolation=requires_sandbox)
    original = tool.to_openai_schema()

    class Model(_Model):
        async def complete(self, messages, **kwargs):
            description = kwargs["tools"][0]["function"]["description"]
            assert ("Unavailable in the current execution environment" in description) is requires_sandbox
            if requires_sandbox:
                return _reply("An isolated execution environment is required")
            replies = [item["content"] for item in messages if item["role"] == "tool"]
            if not replies:
                return _reply(tool=_call("bash", {"command": "printf ready"}))
            assert "ready" in replies[-1]
            return _reply("Executed")

    result = await OpenCollab(tmp_path, model="gpt-4o", environment=env).agent(
        "Run the shell probe if available", tools=[tool], llm=Model(), trace=False,
    )
    assert result.output == ("An isolated execution environment is required" if requires_sandbox else "Executed")
    assert tool.to_openai_schema() == original
    if requires_sandbox:
        refusal = await tool.execute_with_runtime({"command": "printf forbidden"}, ToolRuntime(env, None, None))
        assert "does not provide an OS process sandbox" in refusal
