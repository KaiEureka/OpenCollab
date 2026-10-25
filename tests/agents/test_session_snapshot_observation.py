from __future__ import annotations

import errno
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

import httpx
import openai
import pytest

from opencollab.adapters.env import LocalEnvironment
from opencollab.adapters.trace import Tracer
from opencollab.application.event_bus import EventBus
from opencollab.application.tool_execution import ToolRuntime
from opencollab.bootstrap import DefaultSessionFactory, SnapshotSessionError, session_factory, snapshot_session
from opencollab.bootstrap.context_builder import SpawnConfig
from opencollab.bootstrap.team_config import RoleConfig, TeamConfig


class _ForkableEnvironment(LocalEnvironment):
    def __init__(self, workspace):
        super().__init__(str(workspace))
        self.forks = []

    def fork_snapshot(self):
        directory = tempfile.mkdtemp(prefix="snapshot-", dir=Path(self.workspace).parent)
        shutil.copytree(self.workspace, directory, dirs_exist_ok=True)
        branch = type(self)(directory)
        self.forks.append(branch)
        return branch


@pytest.fixture
def native_requests(monkeypatch):
    requests, clients = [], []
    sdk_type = openai.AsyncOpenAI

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        messages = payload["messages"]
        if messages[-1]["role"] == "tool":
            message = {"role": "assistant", "content": "RESULT " + messages[-1]["content"]}
            reason = "stop"
        elif "CHILD_READ" in str(messages):
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"read-{len(requests)}", "type": "function", "function": {
                    "name": "file_read", "arguments": json.dumps({"path": "marker.txt"}),
                },
            }]}
            reason = "tool_calls"
        else:
            message, reason = {"role": "assistant", "content": "PARENT_READY"}, "stop"
        return httpx.Response(200, json={
            "id": f"chatcmpl-{len(requests)}", "object": "chat.completion", "created": 1,
            "model": "gpt-4o", "choices": [{"index": 0, "message": message, "finish_reason": reason}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        })

    def native_client(**kwargs):
        client = sdk_type(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        clients.append(client)
        return client

    monkeypatch.setattr(openai, "AsyncOpenAI", native_client)
    monkeypatch.setenv("PYTHONPATH", str(Path(session_factory.__file__).resolve().parents[2]))
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", os.devnull)
    monkeypatch.setenv("OPENCOLLAB_BUDGET_NUDGE_MODE", "off")
    monkeypatch.setenv("OPENCOLLAB_WRITE_NUDGE_MODE", "off")
    monkeypatch.setenv("OPENCOLLAB_WORKFLOWS_DIR", "workflows")
    return requests, clients


def _factory(tmp_path, source_events):
    workspace = tmp_path / "source"
    workspace.mkdir()
    (workspace / "marker.txt").write_text("SOURCE_MARKER")
    (workspace / "authored.py").write_text('''import os
from pathlib import Path
from opencollab.adapters.tools.fs import FileReadTool

async def main(ctx, args):
    Path("child-pid.txt").write_text(str(os.getpid()))
    answer = await ctx.agent("CHILD_READ", tools=[FileReadTool()])
    Path("child-result.txt").write_text(answer)
    if args.get("fail"):
        raise RuntimeError("authored failure after native child")
    return answer
''')
    workflows = workspace / "workflows"
    workflows.mkdir()
    (workflows / "read_marker.py").write_text('''from opencollab import workflow
from opencollab.adapters.tools.fs import FileReadTool

@workflow(name="read-marker", description="Read the local marker")
async def main(ctx, args):
    return await ctx.agent("CHILD_READ", tools=[FileReadTool()])
''')
    tracer = Tracer("source", output_dir=str(tmp_path / "trace"))
    bus = EventBus(source_events.append)
    config = SpawnConfig(
        model="gpt-4o", provider="openai", api_key="offline-test",  # pragma: allowlist secret
        base_url="http://127.0.0.1:9/v1", llm_timeout=3, llm_max_retries=0,
        tracer=tracer, event_bus=bus, permission_policy=None, thinking=False,
        temperature=0.35, top_p=0.8, max_output_tokens=128, workflow_cleanup_timeout=0.75,
        thinking_params={"budget_tokens": 512, "metadata": {"labels": ["source"]}},
        model_routes={"reader": {"model": "gpt-4o", "provider": "openai"}},
    )
    environment = _ForkableEnvironment(workspace)
    save_dir = tmp_path / "saved"
    save_dir.mkdir()
    factory = DefaultSessionFactory(
        config, team_cfg=TeamConfig(roles={"worker": RoleConfig(
            prompt="Read the local marker when requested.",
            tools=["bash", "file_read", "manage_workflow", "run_workflow"],
        )}), lead_workspace=str(workspace), lead_environment=environment,
        save_dir=str(save_dir), allow_unisolated_child_shell=True,
    )
    source = factory.build_spawn_session(role="worker", env=environment, budget=100_000, aid=0)
    return source, config


def _trace_lines(tracer):
    tracer.flush()
    return Path(tracer.path).read_bytes().splitlines()


def _saved_files(session):
    return {suffix: Path(str(session.auto_save_path) + suffix).read_bytes() for suffix in ("", ".journal")}


async def _cleanup(source, branch, tracer, clients):
    for session in (branch, source):
        if session is not None:
            await session.aclose()
            descriptor = session.env._workspace_fd
            await session.env.cleanup()
            with pytest.raises(OSError) as caught:
                os.fstat(descriptor)
            assert caught.value.errno == errno.EBADF
    for client in clients:
        await client.close()
        assert client.is_closed()
    tracer.close()
    assert tracer._file.closed
    assert not tracer._writer.is_alive()


@pytest.mark.parametrize("workflow", ["authored", "authored-error", "registered"])
@pytest.mark.parametrize("observed", [False, True])
async def test_factory_traced_snapshot_runs_workflows_with_independent_observation(
    tmp_path, native_requests, workflow, observed,
):
    requests, clients = native_requests
    source_events, branch_events = [], []
    source, config = _factory(tmp_path, source_events)
    tracer, bus = config.tracer, config.event_bus
    branch = None
    try:
        await source.add_user_message("Prepare the source")
        assert await source.run_loop() == "PARENT_READY"
        await source.add_user_message("Resume this turn after snapshotting")
        await source.enqueue_auto_save()
        source_trace = _trace_lines(tracer)
        source_saved = _saved_files(source)
        source_event_count = len(source_events)
        source_tokens = source.used_tokens
        branch = snapshot_session(source, **({"event_sink": branch_events.append} if observed else {}))
        assert branch.tracer is None
        assert branch.auto_save_path is None
        authored = branch.agent.find_tool("manage_workflow")
        registered = branch.agent.find_tool("run_workflow")
        assert authored._config is registered._config
        assert authored is branch.runner._initial_agent_tools[-2]
        assert registered is branch.runner._initial_agent_tools[-1]
        copied = authored._config
        assert copied is not config
        assert copied.tracer is None
        assert copied.event_bus.subscribers == ()
        assert copied.event_bus is not bus
        assert copied.model_settings() == config.model_settings()
        assert copied.model_settings("reader") == config.model_settings("reader")
        for name in vars(config).keys() - {"tracer", "event_bus"}:
            assert getattr(copied, name) == getattr(config, name)
        copied.thinking_params["metadata"]["labels"].append("branch")
        copied.model_routes["reader"]["model"] = "gpt-4o-mini"
        assert config.thinking_params["metadata"]["labels"] == ["source"]
        assert config.model_routes["reader"]["model"] == "gpt-4o"
        assert config.tracer is tracer
        assert config.event_bus is bus

        await branch.env.write_file("marker.txt", "BRANCH_MARKER")
        assert await branch.run_loop() == "PARENT_READY"
        await branch.add_user_message("Run the workflow")
        runtime = ToolRuntime(branch.env, None, None)
        if workflow.startswith("authored"):
            launched = json.loads(await authored.execute_with_runtime({
                "action": "launch", "source": "authored.py", "python": sys.executable, "budget": 20_000,
                "args": {"fail": workflow == "authored-error"},
            }, runtime))
            finished = json.loads(await authored.execute_with_runtime({
                "action": "wait", "id": launched["id"], "timeout": 15,
            }, runtime))
            if workflow == "authored-error":
                assert finished["status"] == "failed", finished
                assert "authored failure after native child" in finished["result"]["reason"]
            else:
                assert finished["status"] == "completed", finished
                assert "BRANCH_MARKER" in finished["result"]["output"]
            assert "BRANCH_MARKER" in await branch.env.read_file("child-result.txt")
            pid = int(await branch.env.read_file("child-pid.txt"))
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
            child = authored._children[launched["id"]]
            assert child.execution.done()
            assert authored._reserved_tokens() == 0
        else:
            finished = json.loads(await registered.execute_with_runtime({
                "name": "read-marker", "args": {}, "budget": 20_000,
            }, runtime))
            assert "BRANCH_MARKER" in finished["output"]
            assert finished["tokens"] == 36
        assert len(requests) == 4
        assert all(request["model"] == "gpt-4o" for request in requests)
        assert all(request["temperature"] == 0.35 and request["top_p"] == 0.8 for request in requests)
        assert source.used_tokens == source_tokens == 18
        assert branch.used_tokens == 72
        assert await source.env.read_file("marker.txt") == "SOURCE_MARKER"
        assert len(source_events) == source_event_count
        assert bool(branch_events) is observed
        assert _trace_lines(tracer) == source_trace
        assert _saved_files(source) == source_saved
        await branch.aclose()
        assert not tracer._closed
        assert tracer._writer.is_alive()
        assert await source.run_loop() == "PARENT_READY"
        await source.enqueue_auto_save()
        assert source.used_tokens == 36
        assert len(_trace_lines(tracer)) > len(source_trace)
        assert _saved_files(source) != source_saved
        assert len(source_events) > source_event_count
    finally:
        await _cleanup(source, branch, tracer, clients)


async def test_uncopyable_workflow_settings_fail_before_allocating_a_snapshot(tmp_path, native_requests):
    requests, clients = native_requests
    source, config = _factory(tmp_path, [])
    tracer = config.tracer
    try:
        config.model_routes["uncloneable"] = threading.Lock()
        with pytest.raises(SnapshotSessionError, match="workflow configuration") as caught:
            snapshot_session(source)
        assert isinstance(caught.value.__cause__, TypeError)
        assert source.env.forks == []
        assert config.tracer is tracer
        assert tracer._writer.is_alive()
        await source.add_user_message("Continue after the rejected snapshot")
        assert await source.run_loop() == "PARENT_READY"
        assert len(requests) == 1
        assert _trace_lines(tracer)
    finally:
        await _cleanup(source, None, tracer, clients)
