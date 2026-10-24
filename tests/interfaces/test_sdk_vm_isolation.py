from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shlex
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import pytest

from opencollab import OpenCollab
from opencollab.adapters.vm_isolation import VMIsolationPool
from opencollab.bootstrap import _workflow_runtime_session as wiring
from opencollab.bootstrap.authored_workflow import AuthoredWorkflowTool
from opencollab.environments import VMConnection, VMEnvironment
from opencollab.tools import builtin_tools
from tests.interfaces.test_member_authored_workflow import _call, _serve_model
from tests.interfaces.test_session_isolation import _repo
from tests.support.paths import REPO_ROOT


@pytest.fixture
async def vm_profile(tmp_path, monkeypatch):
    source = os.environ.get("OPENCOLLAB_TEST_VM_PROFILE")
    if not source:
        pytest.skip("OPENCOLLAB_TEST_VM_PROFILE enables real VM SDK and CLI probes")
    profile = json.loads(Path(source).read_text())
    if len(profile["resources"]) < 2:
        pytest.fail("the VM SDK probe requires two existing guests")
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", os.devnull)
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
    identity = f"sdk-{uuid.uuid4().hex}"
    controls = []
    cleanup_operations = []
    for method_name in ("_export", "_cleanup_owner"):
        method = getattr(VMIsolationPool, method_name)

        async def measured(pool, *args, _method=method, _name=method_name):
            started = time.monotonic()
            completed = False
            try:
                result = await _method(pool, *args)
                completed = True
                return result
            finally:
                cleanup_operations.append({"operation": _name, "resource_id": args[-1].resource.resource_id,
                                           "seconds": time.monotonic() - started, "completed": completed})

        monkeypatch.setattr(VMIsolationPool, method_name, measured)
    for resource in profile["resources"]:
        control = VMEnvironment(VMConnection(**resource["connection"]), resource["workspace_root"],
                                state_directory=resource["state_directory"])
        await control.setup()
        root = resource["workspace_root"] + "/" + identity
        state = resource["state_directory"] + "/" + identity
        result = await control.exec_cmd(f"mkdir -p -- {shlex.quote(root)} {shlex.quote(state)}")
        assert result.returncode == 0, result.stderr
        resource.update(workspace_root=root, state_directory=state, reset_command=(
            "find . -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && "
            f"printf reset >> {shlex.quote(state + '/reset-receipt')}"
        ))
        controls.append((control, root, state))
    profile["lease_database"] = str(tmp_path / "vm-leases.sqlite3")
    profile["wait_timeout"] = 0
    try:
        yield profile
        with sqlite3.connect(profile["lease_database"]) as database:
            assert database.execute("SELECT count(*) FROM vm_leases").fetchone()[0] == 0
    finally:
        retained = []
        if Path(profile["lease_database"]).exists():
            with sqlite3.connect(profile["lease_database"]) as database:
                retained = database.execute("SELECT resource_id, owner, workspace FROM vm_leases").fetchall()
        guests = []
        for control, root, state in controls:
            script = '''import json, pathlib, socket, sys
root, state = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
receipt = state / 'reset-receipt'
print(json.dumps({'hostname': socket.gethostname(),
                  'boot_id': pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                  'workspace': str(root), 'state_directory': str(state),
                  'reset_receipt': receipt.read_text() if receipt.exists() else '',
                  'remaining_workspace_entries': sorted(path.name for path in root.iterdir())}))
'''
            try:
                evidence = await control.exec_cmd("python3 - " + shlex.quote(root) + " " + shlex.quote(state),
                                                  input_bytes=script.encode())
                guests.append(json.loads(evidence.stdout))
            except Exception as exc:
                guests.append({"workspace": root, "receipt_error": str(exc)})
            if not retained:
                await control.exec_cmd(f"rm -rf -- {shlex.quote(root)} {shlex.quote(state)}")
            await control.cleanup()
        (tmp_path / "vm-receipt.json").write_text(json.dumps({"guests": guests, "retained_leases": retained,
                                                          "cleanup_operations": cleanup_operations}, indent=2))


@pytest.mark.parametrize("scope, unbounded", [("group", False), ("session", False), ("group", True)])
async def test_vm_profile_runs_public_workflow_sessions_on_real_guests(
    tmp_path, monkeypatch, vm_profile, scope, unbounded,
):
    monkeypatch.setenv("OPENCOLLAB_UNBOUNDED_LIMITS", "true" if unbounded else "false")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _repo(workspace, dirty=False)
    sessions = []
    build = wiring.build_session

    def remember(**kwargs):
        session = build(**kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(wiring, "build_session", remember)

    def respond(body):
        replies = [message for message in body["messages"] if message["role"] == "tool"]
        if not replies:
            return {"role": "assistant", "tool_calls": [_call("bash", {
                "command": "cat seed.txt && hostname && cat /proc/sys/kernel/random/boot_id && pwd && "
                           "test ! -e worker.txt && printf private > worker.txt",
            })]}, {"prompt_tokens": 3, "completion_tokens": 2}
        return {"role": "assistant", "content": replies[-1]["content"]}, {"prompt_tokens": 3, "completion_tokens": 2}

    async def flow(ctx, _args):
        answers = [await ctx.agent(label, tools=builtin_tools("bash")) for label in ("first", "second")]
        assert await sessions[0].env.read_file("worker.txt") == "private"
        assert await sessions[1].env.read_file("worker.txt") == "private"
        identities = [session.env.resource_id for session in sessions]
        assert (identities[0] == identities[1]) is (scope == "group")
        assert sessions[0].env.workspace != sessions[1].env.workspace
        assert all((session.max_budget_tokens is None) is unbounded for session in sessions)
        return answers

    with _serve_model(respond) as (endpoint, requests):
        result = await OpenCollab(
            workspace, model="gpt-4o", api_key="host-offline", base_url=endpoint,  # pragma: allowlist secret
            config={"vm": json.loads(json.dumps(vm_profile)), "llm_max_retries": 0},
        ).workflow(flow, isolation=f"vm-{scope}", artifacts=tmp_path / "artifacts", trace=False)
    assert result.ok and not result.agent_failures, result
    assert len(requests) == 4
    assert all("initial" in answer for answer in result.output)
    assert not (workspace / "worker.txt").exists()
    assert len(list((tmp_path / "artifacts" / "vm").glob("*/*/worker.txt"))) == 2


async def test_vm_member_authors_workflow_and_collects_two_guest_results(tmp_path, vm_profile):
    spec = importlib.util.spec_from_file_location(
        "vm_authored_demo", REPO_ROOT / "examples/vm-authored-workflow/demo.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = await module.run(tmp_path / "example", vm_profile)
    child = output["child"]
    assert output["parent_output"] == "Collected child result"
    assert child["status"] == "completed"
    answers = child["result"]["output"]["workers"]
    assert len(answers) == 2 and all("Source seed" in answer for answer in answers)
    boot_ids = [next(line.strip() for line in answer.splitlines() if len(line.strip()) == 36) for answer in answers]
    assert boot_ids[0] != boot_ids[1]
    saved = Path(child["artifacts"])
    assert json.loads((saved / "workflow.json").read_text())["sessions"] == 2
    assert len(list(saved.glob("[0-9]*.json"))) == 2
    assert all("offline-model" not in path.read_text() for path in saved.glob("*.json"))


async def test_vm_parent_cancellation_stops_authored_agents_and_preserves_native_records(
    tmp_path, monkeypatch, vm_profile,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _repo(workspace, dirty=False)
    team = tmp_path / "team.yaml"
    team.write_text("entry: author\nisolation: vm-group\nroles:\n  author:\n"
                    "    prompt: VM author\n    tools: [file_write, bash, manage_workflow]\n")
    source = '''import asyncio
from opencollab.tools import builtin_tools

async def main(ctx, args):
    tools = builtin_tools("bash")
    return await asyncio.gather(ctx.agent("first worker", tools=tools),
                                ctx.agent("second worker", tools=tools, isolation="vm-session"))
'''
    children = []
    launch = AuthoredWorkflowTool._launch

    async def remember(tool, params, runtime):
        child = await launch(tool, params, runtime)
        children.append(child)
        return child

    monkeypatch.setattr(AuthoredWorkflowTool, "_launch", remember)

    def respond(body):
        replies = [message for message in body["messages"] if message["role"] == "tool"]
        author = body["messages"][0]["content"].startswith("VM author")
        if author:
            if not replies:
                call = _call("file_write", {"path": "authored.py", "mode": "create", "content": source})
            elif len(replies) == 1:
                call = _call("manage_workflow", {"action": "launch", "source": "authored.py", "isolation": "worktree"})
            else:
                call = _call("manage_workflow", {"action": "wait", "id": json.loads(replies[-1]["content"])["id"]})
        elif not replies:
            call = _call("bash", {"command": "echo $$ > command.pid && printf ready > entered && sleep 600",
                                  "timeout": 600})
        else:
            return {"role": "assistant", "content": "finished"}, {"prompt_tokens": 3, "completion_tokens": 2}
        return {"role": "assistant", "tool_calls": [call]}, {"prompt_tokens": 3, "completion_tokens": 2}

    async def entered():
        while not children or len(children[0].bridge._environments) < 3:
            await asyncio.sleep(0.1)
        environments = list(children[0].bridge._environments.values())[1:]
        for environment in environments:
            while True:
                try:
                    if await environment.read_file("entered") == "ready":
                        break
                except FileNotFoundError:
                    pass
                await asyncio.sleep(1)
        processes = []
        for environment in environments:
            pid = int(await environment.read_file("command.pid"))
            assert (await environment.exec_cmd(f"kill -0 {pid}")).returncode == 0
            processes.append((environment.connection, pid))
        return processes

    with _serve_model(respond) as (endpoint, _requests):
        task = asyncio.create_task(OpenCollab(
            workspace, model="gpt-4o", api_key="host-offline", base_url=endpoint,  # pragma: allowlist secret
            config={"vm": vm_profile, "llm_max_retries": 0},
        ).team("write and run your Workflow", config=team, artifacts=tmp_path / "artifacts", trace=False))
        try:
            processes = await asyncio.wait_for(entered(), 300)
        finally:
            task.cancel()
            try:
                result = await task
            except asyncio.CancelledError:
                pass
            else:
                assert result.status == "stopped", result
    saved = list((tmp_path / "artifacts").glob("child-*/result.json"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text())["status"] == "stopped"
    assert json.loads((saved[0].parent / "workflow.json").read_text())["status"] == "stopped"
    for connection, pid in processes:
        resource = next(resource for resource in vm_profile["resources"]
                        if resource["connection"]["port"] == connection.port)
        control = VMEnvironment(connection, resource["workspace_root"], state_directory=resource["state_directory"])
        try:
            outcome = await control.exec_cmd(f"test ! -e /proc/{pid}")
            assert outcome.returncode == 0
        finally:
            await control.cleanup()


async def test_cli_reads_vm_profile_from_existing_config_source(tmp_path, monkeypatch, vm_profile):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workflows = workspace / "workflows"
    workflows.mkdir()
    (workflows / "probe.py").write_text('''from opencollab.workflows import workflow
from opencollab.tools import builtin_tools

@workflow
async def vm_probe(ctx, args):
    return await ctx.agent("probe", tools=builtin_tools("bash"))
''')
    _repo(workspace, dirty=False)
    configs = workspace / "configs"
    configs.mkdir()
    (configs / ".env").write_text(f"OPENCOLLAB_VM='{json.dumps(vm_profile)}'\n")

    def respond(body):
        replies = [message for message in body["messages"] if message["role"] == "tool"]
        if not replies:
            return {"role": "assistant", "tool_calls": [_call("bash", {"command": "hostname && cat seed.txt"})]}, {
                "prompt_tokens": 3, "completion_tokens": 2,
            }
        return {"role": "assistant", "content": replies[-1]["content"]}, {"prompt_tokens": 3, "completion_tokens": 2}

    with _serve_model(respond) as (endpoint, requests):
        monkeypatch.setenv("OPENCOLLAB_BASE_URL", endpoint)
        monkeypatch.setenv("OPENCOLLAB_API_KEY", "host-offline")
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "opencollab", "workflow", "run", "vm_probe", "--workspace", str(workspace),
            "--isolation", "vm-group", "--no-save", "--no-trace",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 180)
    assert process.returncode == 0, (stdout.decode(), stderr.decode())
    assert "initial" in stdout.decode()
    assert len(requests) == 2
