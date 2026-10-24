from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from opencollab import OpenCollab
from opencollab.bootstrap import _workflow_runtime_session as wiring
from opencollab.environments import DockerOptions, docker_isolation_provider, local_environment
from opencollab.tools import builtin_tools


def _repo(path: Path) -> None:
    path.mkdir()
    (path / "seed.txt").write_text("input\n")
    for args in (("init", "-q"), ("add", "."), (
        "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial",
    )):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


@pytest.fixture
def loopback_provider():
    requests = []
    reply = None

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            message = reply(body)
            payload = json.dumps({
                "id": "chatcmpl_local", "object": "chat.completion", "created": 1, "model": body["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", **message},
                             "finish_reason": "tool_calls" if "tool_calls" in message else "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def install(handler):
        nonlocal reply
        reply = handler
        return f"http://127.0.0.1:{server.server_port}/v1", requests

    try:
        yield install
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _call(name, arguments, call_id):
    return {"content": None, "tool_calls": [{
        "id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)},
    }]}


def _image():
    image = os.environ.get("OPENCOLLAB_DOCKER_TEST_IMAGE")
    if not image:
        pytest.skip("OPENCOLLAB_DOCKER_TEST_IMAGE selects a local image with bash and git")
    return image


@pytest.mark.parametrize("scope", ["group", "session"])
async def test_workflow_sdk_uses_docker_tools_through_loopback_api(tmp_path, monkeypatch, loopback_provider, scope):
    image = _image()
    workspace = tmp_path / "workspace"
    _repo(workspace)
    sessions = []
    build = wiring.build_session

    def remember(**kwargs):
        session = build(**kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(wiring, "build_session", remember)

    def respond(body):
        if any(message["role"] == "tool" for message in body["messages"]):
            return {"content": "finished"}
        return _call("bash", {"command": (
            "test ! -e marker.txt && printf private > marker.txt && cat /etc/hostname && pwd && cat seed.txt"
        )}, "write")

    endpoint, requests = loopback_provider(respond)
    name = f"docker-{scope}"
    borrowed = local_environment(workspace)
    client = OpenCollab(
        workspace, model="offline", provider="openai", base_url=endpoint,
        api_key="offline-unused",  # pragma: allowlist secret
        environment=borrowed,
        environment_providers={name: docker_isolation_provider(DockerOptions(image=image), scope=scope)},
        config={"llm_max_retries": 0},
    )

    async def flow(ctx, inputs):
        first = await ctx.agent("first", tools=list(builtin_tools("bash")))
        second = await ctx.agent("second", tools=list(builtin_tools("bash")))
        assert all(await asyncio.gather(*(session.env.read_file("marker.txt") for session in sessions)))
        ids = [session.env.container_reference for session in sessions]
        assert (ids[0] == ids[1]) is (scope == "group")
        assert sessions[0].env.workspace != sessions[1].env.workspace
        return first, second

    result = await client.workflow(flow, isolation=name, trace=False)
    assert result.ok and result.output == ("finished", "finished")
    assert len(requests) == 4
    assert all("input" in str(body["messages"]) for body in requests if any(
        message["role"] == "tool" for message in body["messages"]
    ))
    assert not (workspace / "marker.txt").exists()
    assert not borrowed.revoked


@pytest.mark.parametrize("scope", ["group", "session"])
async def test_team_entry_and_child_use_docker_and_save_entry_patch(tmp_path, loopback_provider, scope):
    image = _image()
    workspace = tmp_path / "workspace"
    _repo(workspace)
    team = tmp_path / "team.yaml"
    team.write_text("""entry: manager
roles:
  manager:
    prompt: Manager
    tools: [bash, spawn_agent]
  coder:
    prompt: Coder
    tools: [bash]
topology:
  manager: [coder]
""")

    def respond(body):
        messages = body["messages"]
        tools = [message for message in messages if message["role"] == "tool"]
        manager = messages[0]["content"].startswith("Manager")
        if not tools:
            command = "printf manager > entry.txt; cat /etc/hostname; pwd" if manager else (
                "cat entry.txt; printf '\\n'; printf child > child.txt; cat /etc/hostname; pwd"
            )
            return _call("bash", {"command": command}, "probe")
        if manager and len(tools) == 1:
            return _call("spawn_agent", {"role": "coder", "task": "edit"}, "spawn")
        return {"content": "finished"}

    endpoint, requests = loopback_provider(respond)
    name = f"docker-{scope}"
    result = await OpenCollab(
        workspace, model="offline", provider="openai", base_url=endpoint,
        api_key="offline-unused",  # pragma: allowlist secret
        environment_providers={name: docker_isolation_provider(DockerOptions(image=image), scope=scope)},
        config={"llm_max_retries": 0},
    ).team("delegate", config=team, isolation=name, artifacts=tmp_path / "artifacts", trace=False)
    assert result.ok
    entry_patch = Path(result.metrics["entry_patch"]).read_text()
    assert "+manager" in entry_patch
    assert "child.txt" not in entry_patch
    assert not (workspace / "entry.txt").exists()
    assert not (workspace / "child.txt").exists()
    completed = [body for body in requests if any(message["role"] == "tool" for message in body["messages"])]
    outputs = {}
    for body in completed:
        role = "manager" if body["messages"][0]["content"].startswith("Manager") else "coder"
        outputs.setdefault(role, next(message["content"] for message in body["messages"] if message["role"] == "tool"))
    hostnames = {role: re.search(r"\b[0-9a-f]{12}\b", text).group() for role, text in outputs.items()}
    assert (hostnames["manager"] == hostnames["coder"]) is (scope == "group")
    assert "manager\n" in outputs["coder"]
