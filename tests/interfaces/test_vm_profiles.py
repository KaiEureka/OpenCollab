from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

from opencollab import OpenCollab
from opencollab.adapters.session_environments import SessionEnvironmentPool
from opencollab.application.scheduler import Scheduler
from opencollab.bootstrap import _workflow_runtime_execution as workflow_execution
from opencollab.bootstrap import programmatic
from opencollab.bootstrap.config import build_config
from opencollab.bootstrap.environment_profiles import configured_environment_providers
from opencollab.environments import VMConnection, VMEnvironment, VMResource, vm_isolation_provider
from tests.interfaces.test_member_authored_workflow import _call, _manager, _serve_model
from tests.interfaces.test_session_isolation import _repo
from tests.support.environment_test_support import local_environment_factory as local_environment_factory
from tests.support.paths import REPO_ROOT


@pytest.fixture(autouse=True)
def _child_import_path(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", os.devnull)


def _profile(tmp_path):
    return {
        "resources": [{"resource_id": "guest-a", "connection": {"host": "guest.example", "user": "worker"},
                       "reset_command": "/usr/local/sbin/reset-opencollab-guest"}],
        "lease_database": str(tmp_path / "leases.sqlite3"),
        "wait_timeout": 0,
    }


def test_vm_config_round_trips_and_environment_factories_remain_overridable(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    monkeypatch.setenv("OPENCOLLAB_VM", json.dumps(profile))
    loaded = build_config(str(tmp_path))
    assert loaded.vm.resources[0].connection == VMConnection("guest.example", "worker")
    dumped = json.loads(loaded.model_dump_json())
    assert build_config(str(tmp_path), dumped).vm == loaded.vm
    client = OpenCollab(tmp_path, config=dumped)
    replacement = object()
    providers = configured_environment_providers(client._config, {"vm-session": replacement})
    assert providers["vm-session"] is replacement
    assert providers["vm-group"].keywords["scope"] == "group"
    assert providers["vm-group"].keywords["lease_database"] == profile["lease_database"]
    assert isinstance(loaded.vm.resources[0], VMResource)
    assert callable(vm_isolation_provider)
    assert VMEnvironment is not None


@pytest.mark.parametrize("change", [
    {"resources": []}, {"lease_database": "relative.sqlite3"}, {"wait_timeout": -1},
    {"wait_timeout": True}, {"wait_timeout": float("inf")}, {"unexpected": "value"},
    {"cleanup_timeout": 0}, {"cleanup_timeout": True}, {"cleanup_timeout": float("nan")},
    {"resources": [{"resource_id": "a", "connection": {"host": "guest", "user": "worker", "port": True}}]},
    {"resources": [{"resource_id": "a", "connection": {"host": "guest", "user": "worker", "password": "unused"}}]},
])
def test_invalid_vm_configuration_reports_the_field(tmp_path, change):
    with pytest.raises(ValueError):
        OpenCollab(tmp_path, config={"vm": {**_profile(tmp_path), **change}})


@pytest.mark.parametrize("strategy", ["vm-group", "vm-session"])
async def test_vm_strategy_without_resources_fails_before_model_use(tmp_path, strategy):
    client = OpenCollab(tmp_path)

    async def flow(ctx, _args):
        return await ctx.agent("unused", tools=[])

    with pytest.raises(ValueError, match="requires VM resources"):
        await client.workflow(flow, isolation=strategy, trace=False)


@pytest.mark.parametrize("selected", [None, "worktree", "shared"])
async def test_authored_workflow_can_override_or_inherit_child_default(
    tmp_path, local_environment_factory, selected,
):
    _repo(tmp_path, dirty=False)
    environment = local_environment_factory(str(tmp_path))
    (tmp_path / "authored.py").write_text(
        "from opencollab.tools import builtin_tools\n"
        "async def main(ctx, args):\n"
        "    return await ctx.agent('child', tools=builtin_tools('bash', headless=False))\n"
    )
    parent, tool, runtime = _manager(environment, budget=20_000)
    tool._environment_pool_factory = lambda environment, **_kwargs: SessionEnvironmentPool(
        str(tmp_path), isolation="worktree", base_environment=environment,
    )
    def respond(body):
        replies = [message for message in body["messages"] if message["role"] == "tool"]
        if not replies:
            return {"role": "assistant", "tool_calls": [_call("bash", {
                "command": "pwd && printf child > child.txt",
            })]}, {"prompt_tokens": 3, "completion_tokens": 2}
        return {"role": "assistant", "content": replies[-1]["content"]}, {"prompt_tokens": 3, "completion_tokens": 2}

    with _serve_model(respond) as (endpoint, _requests):
        parent.agent.base_url = endpoint
        parent.agent.api_key = "host-only-secret"  # pragma: allowlist secret
        try:
            launched = json.loads(await tool.execute_with_runtime({
                "action": "launch", "source": "authored.py", "python": sys.executable,
                **({"isolation": selected} if selected is not None else {}),
            }, runtime))
            result = json.loads(await tool.execute_with_runtime({"action": "wait", "id": launched["id"]}, runtime))
            assert result["status"] == "completed", result
            assert (tmp_path / "child.txt").exists() is (selected == "shared"), result
            output = result["result"]["output"]
            assert str(tmp_path) in output if selected == "shared" else str(tmp_path) not in output, result
        finally:
            await parent.aclose()


async def test_authored_isolation_override_is_validated_at_launch(tmp_path, local_environment_factory):
    environment = local_environment_factory(str(tmp_path))
    (tmp_path / "authored.py").write_text("async def main(ctx, args):\n    return 'unused'\n")
    parent, tool, runtime = _manager(environment)
    try:
        with pytest.raises(ValueError, match="only supported by the launch"):
            await tool.execute_with_runtime({"action": "status", "isolation": "worktree"}, runtime)
        with pytest.raises(ValueError, match="needs an environment provider"):
            await tool.execute_with_runtime({
                "action": "launch", "source": "authored.py", "isolation": "missing",
            }, runtime)
        assert not tool._children
    finally:
        await parent.aclose()


@pytest.mark.parametrize("with_vm, explicit, parent_grace, child_grace", [
    (False, None, 10, 2), (True, None, 60, 60), (False, 7, 7, 7), (True, 7, 7, 7),
])
async def test_team_cleanup_defaults_preserve_child_grace_and_accept_explicit_override(
    tmp_path, monkeypatch, with_vm, explicit, parent_grace, child_grace,
):
    captured = {}

    class Scheduler:
        used_tokens = 0
        table = SimpleNamespace(entries={0: object()})
        lead_session = SimpleNamespace(phase=SimpleNamespace(value="done"), state=SimpleNamespace(terminal_reason=None))

        async def run(self, prompt):
            return "done"

        async def cleanup(self, *, cleanup_timeout):
            captured["parent_grace"] = cleanup_timeout
            if "worktree_pool" in captured:
                await captured["worktree_pool"].release()

    def build(_ctx, **kwargs):
        captured.update(kwargs)
        return Scheduler()

    monkeypatch.setattr(programmatic, "build_scheduler", build)
    result = await OpenCollab(tmp_path, config={"vm": _profile(tmp_path)} if with_vm else {}).team(
        "done", use_worktrees=False, trace=False,
        **({"cleanup_timeout": explicit} if explicit is not None else {}),
    )
    assert result.ok
    assert captured["parent_grace"] == parent_grace
    assert captured["cleanup_timeout"] == parent_grace
    assert captured["workflow_cleanup_timeout"] == child_grace


@pytest.mark.parametrize("with_vm, explicit, expected", [(False, None, 10), (True, None, 60), (True, 7, 7)])
async def test_cancelled_public_team_uses_resolved_grace_on_its_first_internal_cleanup(
    tmp_path, monkeypatch, with_vm, explicit, expected,
):
    entered = asyncio.Event()
    observed = []
    cleanup = Scheduler._cleanup_impl

    async def block_turn(scheduler, aid, prompt):
        entered.set()
        await asyncio.Event().wait()

    async def remember(scheduler, *, timeout):
        observed.append(timeout)
        return await cleanup(scheduler, timeout=timeout)

    monkeypatch.setattr(Scheduler, "_run_turn_exclusive", block_turn)
    monkeypatch.setattr(Scheduler, "_cleanup_impl", remember)
    team = tmp_path / "team.yaml"
    team.write_text("entry: author\nroles:\n  author:\n    prompt: Author\n    tools: []\n")
    task = asyncio.create_task(OpenCollab(
        tmp_path, api_key="offline-test",  # pragma: allowlist secret
        config={"vm": _profile(tmp_path)} if with_vm else {},
    ).team(
        "run", config=team, use_worktrees=False, trace=False,
        **({"cleanup_timeout": explicit} if explicit is not None else {}),
    ))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert observed == [expected]


@pytest.mark.parametrize("with_vm, explicit, expected", [(False, None, 2), (True, None, 60), (True, 7, 7)])
async def test_workflow_cleanup_uses_profile_default_and_explicit_override(
    tmp_path, monkeypatch, with_vm, explicit, expected,
):
    captured = []
    finalize = workflow_execution._quiesce_and_finalize_workflow_context

    async def remember(ctx, *, timeout):
        captured.append(timeout)
        return await finalize(ctx, timeout=timeout)

    monkeypatch.setattr(workflow_execution, "_quiesce_and_finalize_workflow_context", remember)

    async def flow(ctx, args):
        return "done"

    result = await OpenCollab(tmp_path, config={"vm": _profile(tmp_path)} if with_vm else {}).workflow(
        flow, trace=False, **({"cleanup_timeout": explicit} if explicit is not None else {}),
    )
    assert result.ok
    assert captured == [expected]


@pytest.mark.parametrize("inherited, explicit, expected", [(2, None, 2), (60, None, 60), (60, 7, 7)])
async def test_authored_launch_inherits_cleanup_grace_and_can_override_it(
    tmp_path, local_environment_factory, inherited, explicit, expected,
):
    environment = local_environment_factory(str(tmp_path))
    (tmp_path / "authored.py").write_text("async def main(ctx, args):\n    return 'done'\n")
    parent, tool, runtime = _manager(environment)
    tool._config.workflow_cleanup_timeout = inherited
    try:
        launched = json.loads(await tool.execute_with_runtime({
            "action": "launch", "source": "authored.py", "python": sys.executable,
            **({"cleanup_timeout": explicit} if explicit is not None else {}),
        }, runtime))
        child = tool._children[launched["id"]]
        assert child.cleanup_timeout == expected
        result = json.loads(await tool.execute_with_runtime({"action": "wait", "id": child.id}, runtime))
        assert result["result"]["output"] == "done", result
        with pytest.raises(ValueError, match="only supported by the launch"):
            await tool.execute_with_runtime({"action": "status", "cleanup_timeout": 7}, runtime)
    finally:
        await parent.aclose()


@pytest.mark.parametrize("budget", [None, 20_000])
async def test_authored_source_uses_host_model_without_receiving_host_credentials(
    tmp_path, local_environment_factory, budget,
):
    environment = local_environment_factory(str(tmp_path))
    (tmp_path / "authored.py").write_text('''import inspect
configs = [frame.frame.f_locals["request"]["config"] for frame in inspect.stack()
           if isinstance(frame.frame.f_locals.get("request"), dict)
           and "config" in frame.frame.f_locals["request"]]

async def main(ctx, args):
    answer = await ctx.agent("answer through the host bridge", tools=[])
    return {"config": configs[0], "answer": answer}
''')
    parent, tool, runtime = _manager(environment, budget=budget)
    tool._config.model_routes = {"unused": {"model": "unused", "api_key": "route-secret"}}  # pragma: allowlist secret
    tool._config.request_pool = str(tmp_path / "host-request-slots")
    tool._config.request_capacity = 1

    def respond(body):
        return {"role": "assistant", "content": "host served"}, {"prompt_tokens": 3, "completion_tokens": 2}

    with _serve_model(respond) as (endpoint, requests):
        parent.agent.base_url = endpoint
        parent.agent.api_key = "host-secret"  # pragma: allowlist secret
        try:
            launched = json.loads(await tool.execute_with_runtime({
                "action": "launch", "source": "authored.py", "python": sys.executable,
            }, runtime))
            child = tool._children[launched["id"]]
            assert (child.budget is None) is (budget is None)
            result = json.loads(await tool.execute_with_runtime({"action": "wait", "id": child.id}, runtime))
            assert result["status"] == "completed", result
            output = result["result"]["output"]
            assert output["answer"] == "host served"
            assert output["config"]["model"] == "gpt-4o"
            assert not {"api_key", "auth_token", "base_url", "model_routes", "request_pool"} & output["config"].keys()
            assert len(requests) == 1
            assert requests[0]["model"] == "gpt-4o"
            assert parent.used_tokens == result["tokens"] == 5
        finally:
            await parent.aclose()
