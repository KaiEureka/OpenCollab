from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from opencollab import OpenCollab
from opencollab.adapters.llm import client as transport
from opencollab.adapters.llm.types import LLMResponse, Usage
from opencollab.bootstrap.config import build_config
from tests.support.paths import PACKAGE_ROOT
from tests.support.provider_sdk_http import completion_http_response, install_sdk_transport


@pytest.fixture(autouse=True)
def clean_provider_environment(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith("OPENCOLLAB_") or name in {
            "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
        }:
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize("entry", ["agent", "workflow", "team", "create_model_client"])
async def test_sdk_routes_physical_model_provider_and_controls(tmp_path, monkeypatch, entry):
    requests = []

    def handler(request):
        requests.append((str(request.url), dict(request.headers), json.loads(request.content)))
        return completion_http_response(request, text="finished")

    install_sdk_transport(monkeypatch, "anthropic", handler)
    client = OpenCollab(tmp_path, model="writer",
        api_key="default-endpoint-secret", model_routes={  # pragma: allowlist secret
        "writer": {
            "model": "claude-sonnet-4-6", "provider": "anthropic", "base_url": "https://writer.example",
            "auth_token": "route-token", "context_window": 12345,
        },
    }, config={"max_output_tokens": 4096, "thinking_params": {}, "temperature": 0.4})
    if entry == "create_model_client":
        async with client.create_model_client() as llm:
            assert llm.model == "claude-sonnet-4-6"
            assert llm.provider == "anthropic"
            assert llm.context_window() == 12345
            assert llm.estimate_request_tokens([{"role": "user", "content": "finish"}]) > 0
            assert llm.estimate_context_tokens([{"role": "user", "content": "finish"}]) > 0
            response = await llm.complete([{"role": "user", "content": "finish"}], temperature=0.4)
        assert response.content == "finished"
    else:
        if entry == "agent":
            result = await client.agent("finish", tools=[], trace=False)
        elif entry == "workflow":
            async def flow(ctx, _inputs):
                return await ctx.agent("finish", tools=[])

            result = await client.workflow(flow, trace=False)
        else:
            path = tmp_path / "team.yaml"
            path.write_text("entry: writer\nroles:\n  writer:\n    prompt: Finish.\n    tools: []\n")
            result = await client.team("finish", config=path, use_worktrees=False, trace=False)
        assert result.ok
        assert result.output == "finished"
    assert len(requests) == 1
    url, headers, body = requests[0]
    assert url == "https://writer.example/v1/messages"
    assert headers["authorization"] == "Bearer route-token"
    assert "x-api-key" not in headers
    assert body["model"] == "claude-sonnet-4-6"
    assert body["temperature"] == 0.4
    metadata = json.dumps(dict(client.configuration))
    assert "claude-sonnet-4-6" in metadata
    assert "route-token" not in metadata
    assert "default-endpoint-secret" not in metadata
    assert "writer.example" not in metadata


async def test_routed_responses_preserve_identity_and_optional_session(tmp_path, monkeypatch):
    response = LLMResponse(
        content="answer", provider_model="gpt-5", usage=Usage(4, 2),
        provider_items=[{"id": "item-1", "type": "message"}],
        provider_state={"response_id": "resp-1"}, reasoning="reasoning",
    )
    captured = []

    async def complete(*args, **kwargs):
        captured.append((args, kwargs))
        return response

    monkeypatch.setattr(transport, "complete_responses", complete)
    client = OpenCollab(tmp_path, model="reasoner", model_routes={
        "reasoner": {
            "model": "gpt-5", "api_key": "local", "wire_protocol": "responses",  # pragma: allowlist secret
            "base_url": "https://gateway.example/v1", "context_window": 10000,
        },
    })
    async with client.create_model_client() as llm:
        assert llm.supports_response_session_identity
        assert llm.context_window() == 10000
        actual = await llm.complete(
            [{"role": "user", "content": "answer"}], reasoning_effort="high", response_session_id="session-1",
        )
    assert actual is response
    assert response.provider_items == [{"id": "item-1", "type": "message"}]
    assert response.provider_state == {"response_id": "resp-1"}
    args, kwargs = captured[0]
    assert args[1] == "gpt-5"
    assert kwargs["reasoning_effort"] == "high"
    assert kwargs["response_session_id"] == "session-1"


def test_routes_resolve_env_file_secrets_once(tmp_path, monkeypatch):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    routes = {"worker": {"model": "local-model", "base_url": "http://localhost:9999/v1",
        "api_key_env": "WORKER_KEY"}}  # pragma: allowlist secret
    (config_dir / ".env").write_text(f"WORKER_KEY=initial-key\nOPENCOLLAB_MODEL_ROUTES='{json.dumps(routes)}'\n")
    client = OpenCollab(tmp_path, model="worker")
    monkeypatch.setenv("WORKER_KEY", "later-key")
    assert client._config["model_routes"]["worker"]["api_key"] == "initial-key"  # pragma: allowlist secret
    monkeypatch.setenv("OPENCOLLAB_MODEL_ROUTES", json.dumps({"worker": {"model": "other",
        "api_key_env": "MISSING"}}))  # pragma: allowlist secret
    with pytest.raises(ValueError, match="MISSING"):
        build_config(str(tmp_path))


@pytest.mark.parametrize("bad", [
    {"request_pool": "relative", "request_capacity": 2},
    {"request_pool": "/tmp/pool"}, {"request_capacity": 2},
    {"request_pool": "/tmp/pool", "request_capacity": True},
    {"model_routes": {"worker": {"model": "physical", "request_capacity": 2}}},
    {"model_routes": {"worker": {"model": "physical", "unexpected": True}}},
])
def test_invalid_pool_and_route_configuration_fails_explicitly(tmp_path, bad):
    with pytest.raises(ValueError):
        OpenCollab(tmp_path, config=bad)


_WORKER = """
import asyncio, json, sys, time
from pathlib import Path
from opencollab import OpenCollab
root, mode, index = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
client = OpenCollab(root / index, model='workflow', config=json.loads((root / 'config.json').read_text()))
(root / ('ready-' + index)).touch()
while not (root / 'start').exists():
    time.sleep(.01)
async def main():
    if mode == 'team':
        result = await client.team('finish', config=root / 'team.yaml', use_worktrees=False, trace=False)
    else:
        async def flow(ctx, inputs):
            return await ctx.agent('finish', tools=[])
        result = await client.workflow(flow, trace=False)
    print(json.dumps({'ok': result.ok, 'output': result.output}))
asyncio.run(main())
"""


@contextmanager
def _backend(state, expected_model):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with state["lock"]:
                state["requests"].append((expected_model, body["model"]))
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            try:
                time.sleep(0.2)
                payload = json.dumps({
                    "id": "chat-test", "object": "chat.completion", "created": 1, "model": body["model"],
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": body["model"]},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            finally:
                with state["lock"]:
                    state["active"] -= 1

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_independent_team_and_workflow_processes_share_actual_http_capacity(tmp_path):
    state = {"lock": threading.Lock(), "requests": [], "active": 0, "peak": 0}
    pool = tmp_path / "request-pool"
    pool.mkdir()
    processes = []
    with ExitStack() as stack:
        team_url = stack.enter_context(_backend(state, "team-physical"))
        workflow_url = stack.enter_context(_backend(state, "workflow-physical"))
        (tmp_path / "config.json").write_text(json.dumps({
            "thinking_params": {}, "request_pool": str(pool), "request_capacity": 2,
            "model_routes": {
                "team": {"model": "team-physical", "base_url": team_url,
                    "api_key": "local"},  # pragma: allowlist secret
                "workflow": {"model": "workflow-physical", "base_url": workflow_url,
                    "api_key": "local"},  # pragma: allowlist secret
            },
        }))
        (tmp_path / "team.yaml").write_text(
            "entry: reader\nroles:\n  reader:\n    prompt: Finish.\n    model: team\n    tools: []\n",
        )
        env = {**os.environ, "PYTHONPATH": str(PACKAGE_ROOT)}
        try:
            for index, mode in enumerate(["team", "workflow", "team", "workflow"]):
                (tmp_path / str(index)).mkdir()
                processes.append(subprocess.Popen(
                    [sys.executable, "-c", _WORKER, str(tmp_path), mode, str(index)], env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                ))
            deadline = time.monotonic() + 15
            while len(list(tmp_path.glob("ready-*"))) < 4 and time.monotonic() < deadline:
                for process in processes:
                    if process.poll() is not None:
                        stdout, stderr = process.communicate()
                        pytest.fail(f"worker exited before starting requests\n{stdout}\n{stderr}")
                time.sleep(0.01)
            assert len(list(tmp_path.glob("ready-*"))) == 4
            (tmp_path / "start").touch()
            results = []
            for process in processes:
                stdout, stderr = process.communicate(timeout=20)
                assert process.returncode == 0, stderr
                results.append(json.loads(stdout))
            assert results == [
                {"ok": True, "output": "team-physical"}, {"ok": True, "output": "workflow-physical"},
                {"ok": True, "output": "team-physical"}, {"ok": True, "output": "workflow-physical"},
            ]
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)
    assert sorted(state["requests"]) == [("team-physical", "team-physical")] * 2 + [
        ("workflow-physical", "workflow-physical")
    ] * 2
    assert state["peak"] == 2
    assert state["active"] == 0


async def test_parent_tool_waiting_for_child_workflow_releases_request_slot(tmp_path, monkeypatch):
    from opencollab.adapters.tools.base import Tool
    from tests.support.provider_sdk_http import http_response

    calls = []

    def handler(request):
        body = json.loads(request.content)
        parent = bool(body.get("tools"))
        calls.append("parent" if parent else "child")
        if parent and not any(message["role"] == "tool" for message in body["messages"]):
            return http_response(request, 200, json={
                "id": "parent-call", "object": "chat.completion", "created": 1, "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "child-1", "type": "function", "function": {
                        "name": "run_child", "arguments": "{}",
                    }}],
                }}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
            })
        return completion_http_response(request, text="parent finished" if parent else "child finished")

    install_sdk_transport(monkeypatch, "openai", handler)
    client = OpenCollab(tmp_path, model="worker", model_routes={
        "worker": {
            "model": "physical-worker",
                "api_key": "local", "request_pool": str(tmp_path), "request_capacity": 1,  # pragma: allowlist secret
        },
    }, config={"thinking_params": {}})

    class ChildTool(Tool):
        name = "run_child"
        description = "Run the child task."

        async def execute_with_runtime(self, params, runtime):
            async def flow(ctx, inputs):
                return await ctx.agent("child", tools=[])

            result = await client.workflow(flow, trace=False)
            assert result.ok
            return result.output

    result = await asyncio.wait_for(client.agent("parent", tools=[ChildTool()], trace=False), 5)
    assert result.ok
    assert result.output == "parent finished"
    assert calls == ["parent", "child", "parent"]


@pytest.mark.parametrize("entry", ["workflow", "team"])
async def test_route_capability_checks_use_physical_model(tmp_path, monkeypatch, entry):
    client = OpenCollab(tmp_path, model="reasoner", model_routes={
        "reasoner": {"model": "o3", "wire_protocol": "responses", "api_key": "local"},  # pragma: allowlist secret
    }, config={"top_p": 0.5})
    requests = []

    def handler(request):
        requests.append(request)
        return completion_http_response(request, text="finished")

    install_sdk_transport(monkeypatch, "openai", handler)
    if entry == "workflow":
        async def flow(ctx, _inputs):
            return await ctx.agent("finish", tools=[])

        result = await client.workflow(flow, trace=False)
        assert result.output is None
        assert result.tokens == 0
        assert result.agent_failures[0]["exception_type"] == "ValueError"
    else:
        path = tmp_path / "team.yaml"
        path.write_text("entry: reader\nroles:\n  reader:\n    prompt: Finish.\n    tools: []\n")
        with pytest.raises(ValueError, match="Responses sampling"):
            await client.team("finish", config=path, use_worktrees=False, trace=False)
    assert requests == []


def test_cli_keeps_role_defaults_while_resolving_default_credentials(tmp_path, monkeypatch):
    from opencollab.adapters.cli.config_resolve import missing_api_key_for, resolve_config, resolve_model_route

    monkeypatch.setenv("OPENCOLLAB_MODEL", "worker")
    monkeypatch.setenv("OPENCOLLAB_MODEL_ROUTES", json.dumps({
        "worker": {"model": "local-physical",
            "api_key": "local", "base_url": "http://localhost:9999/v1"},  # pragma: allowlist secret
    }))
    config = resolve_config(str(tmp_path), None, None, None, None, None)
    assert config["model"] == "worker"
    assert config["logical_model"] is None
    selected = resolve_model_route(config)
    assert selected["model"] == "local-physical"
    assert selected["logical_model"] == "worker"
    assert not missing_api_key_for(selected["provider"], selected["api_key"], selected["base_url"])


def test_resolved_config_keeps_physical_identity_across_bootstrap_paths(tmp_path):
    from opencollab.bootstrap._workflow_runtime_session import WorkflowSessionFactory
    from opencollab.bootstrap.config import resolve_model_route
    from opencollab.bootstrap.context_builder import ContextBuilder, SpawnConfig
    from opencollab.bootstrap.team_config import RoleConfig, TeamConfig

    client = OpenCollab(tmp_path, model="worker", model_routes={
        "worker": {"model": "physical-worker",
            "api_key": "local", "base_url": "https://worker.example/v1"},  # pragma: allowlist secret
        "physical-worker": {"model": "unrelated-model",
            "api_key": "other", "base_url": "https://other.example/v1"},  # pragma: allowlist secret
    })
    config = resolve_model_route(client._config)
    assert resolve_model_route(config) == config
    clone = OpenCollab(tmp_path, config=config)
    assert clone._config["logical_model"] == "worker"
    team = TeamConfig(entry="reader", roles={"reader": RoleConfig(prompt="Finish.", tools=[])})
    spawn = SpawnConfig(
        model=config["model"], provider=config["provider"], api_key=config["api_key"],
        base_url=config["base_url"], llm_timeout=10, tracer=None, event_bus=None, permission_policy=None,
        model_routes=config["model_routes"], logical_model=config["logical_model"],
    )
    agent = ContextBuilder(team, spawn).build_agent("reader")
    assert agent.model == "physical-worker"
    assert agent.base_url == "https://worker.example/v1"
    captured = []
    factory = WorkflowSessionFactory(
        model=config["model"], provider=config["provider"], api_key=config["api_key"],
        base_url=config["base_url"], model_routes=config["model_routes"], logical_model=config["logical_model"],
        session_builder=lambda **kwargs: captured.append(kwargs),
    )
    factory.build_workflow_session(prompt="finish", budget=100, tools=[])
    assert captured[0]["agent"].model == "physical-worker"
    assert captured[0]["agent"].base_url == "https://worker.example/v1"
    explicit = OpenCollab(tmp_path, config=config, model="physical-worker")
    assert resolve_model_route(explicit._config)["model"] == "unrelated-model"


@pytest.mark.parametrize("entry", ["sdk_team", "cli_team", "sdk_workflow", "cli_workflow"])
@pytest.mark.parametrize("physical_model", [None, "gpt-4o"])
def test_cli_and_sdk_keep_unrouted_models_on_the_default_backend(
    tmp_path, monkeypatch, entry, physical_model,
):
    from typer.testing import CliRunner

    from opencollab.adapters.cli.main import app

    requests = []

    def respond(request):
        requests.append((str(request.url), dict(request.headers), json.loads(request.content)))
        return completion_http_response(request, text="finished")

    install_sdk_transport(monkeypatch, "openai", respond)
    install_sdk_transport(monkeypatch, "anthropic", respond)
    routes = {"writer": {
        "model": "claude-sonnet-4-6", "provider": "anthropic", "base_url": "https://writer.example",
        "auth_token": "route-token",
    }}
    model = "writer" if entry.endswith("team") else physical_model or "writer"
    monkeypatch.setenv("OPENCOLLAB_MODEL", model)
    monkeypatch.setenv("OPENCOLLAB_API_KEY", "default-key")
    monkeypatch.setenv("OPENCOLLAB_BASE_URL", "https://default.example/v1")
    monkeypatch.setenv("OPENCOLLAB_MODEL_ROUTES", json.dumps(routes))
    monkeypatch.setenv("OPENCOLLAB_THINKING_PARAMS", "{}")
    monkeypatch.setenv("OPENCOLLAB_MAX_OUTPUT_TOKENS", "4096")
    team = tmp_path / "team.yaml"
    team.write_text(
        "entry: reader\nroles:\n  reader:\n    prompt: Finish.\n    tools: []\n"
        + (f"    model: {physical_model}\n" if physical_model else ""),
    )
    if entry.startswith("sdk"):
        client = OpenCollab(tmp_path)
        if entry == "sdk_team":
            result = asyncio.run(client.team("finish", config=team, use_worktrees=False, trace=False))
        else:
            async def flow(ctx, _inputs):
                return await ctx.agent("finish", tools=[])

            result = asyncio.run(client.workflow(flow, trace=False))
        assert result.ok
        assert result.output == "finished"
    else:
        if entry == "cli_team":
            arguments = ["--team-config", str(team), "--no-worktrees", "--prompt", "finish"]
        else:
            workflows = tmp_path / "workflows"
            workflows.mkdir()
            (workflows / "finish.py").write_text(
                "from opencollab.application.workflow_registry import workflow\n"
                "@workflow(name='finish')\n"
                "async def run(ctx, args):\n"
                "    return await ctx.agent('finish', tools=[])\n",
            )
            arguments = ["workflow", "run", "finish", "--no-save", "--no-trace"]
        result = CliRunner().invoke(app, [*arguments, "--workspace", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "finished" in result.output
    assert len(requests) == 1
    url, headers, body = requests[0]
    if physical_model:
        assert url == "https://default.example/v1/chat/completions"
        assert headers["authorization"] == "Bearer default-key"
        assert body["model"] == physical_model
    else:
        assert url == "https://writer.example/v1/messages"
        assert headers["authorization"] == "Bearer route-token"
        assert body["model"] == "claude-sonnet-4-6"


@pytest.mark.parametrize("command", [[], ["workflow", "run", "duo"]])
@pytest.mark.parametrize("secret_field", ["api_key", "auth_token"])
def test_cli_route_validation_keeps_credentials_out_of_diagnostics(tmp_path, monkeypatch, command, secret_field):
    from typer.testing import CliRunner

    from opencollab.adapters.cli.main import app

    secret = "DEMO"  # pragma: allowlist secret
    monkeypatch.setenv("OPENCOLLAB_MODEL_ROUTES", json.dumps({"worker": {
        secret_field: secret, "model": "physical", "request_pool": str(tmp_path),
    }}))
    result = CliRunner().invoke(app, [*command, "--workspace", str(tmp_path)])
    diagnostic = "".join(result.output.replace("\u2502", "").split())
    assert result.exit_code == 2
    assert "model_routes.worker" in diagnostic
    assert "request_poolandrequest_capacitymustbesuppliedtogether" in diagnostic
    assert secret not in result.output


@pytest.mark.parametrize("entry", ["sdk", "cli"])
def test_team_entry_route_supplies_its_own_cli_credentials(tmp_path, monkeypatch, entry):
    from typer.testing import CliRunner

    from opencollab.adapters.cli.main import app

    requests = []

    def respond(request):
        requests.append((str(request.url), dict(request.headers), json.loads(request.content)))
        return completion_http_response(request, text="finished")

    install_sdk_transport(monkeypatch, "openai", respond)
    monkeypatch.setenv("OPENCOLLAB_MODEL_ROUTES", json.dumps({"writer": {
        "model": "physical-writer", "base_url": "https://writer.example/v1",
        "api_key": "route-key",  # pragma: allowlist secret
    }}))
    team = tmp_path / "team.yaml"
    team.write_text("entry: reader\nroles:\n  reader:\n    prompt: Finish.\n    model: writer\n    tools: []\n")
    if entry == "sdk":
        result = asyncio.run(OpenCollab(tmp_path).team("finish", config=team, use_worktrees=False, trace=False))
        assert result.ok
        assert result.output == "finished"
    else:
        result = CliRunner().invoke(app, [
            "--workspace", str(tmp_path), "--team-config", str(team), "--no-worktrees", "--prompt", "finish",
        ])
        assert result.exit_code == 0, result.output
        assert "finished" in result.output
    assert len(requests) == 1
    url, headers, body = requests[0]
    assert url == "https://writer.example/v1/chat/completions"
    assert headers["authorization"] == "Bearer route-key"
    assert body["model"] == "physical-writer"
