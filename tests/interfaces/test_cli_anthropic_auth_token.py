"""Native bearer credentials reach both CLI runtimes unchanged."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from typer.testing import CliRunner

from opencollab import OpenCollab
from opencollab.adapters.cli.main import app
from opencollab.bootstrap.config import accepted_api_key_envs, build_config, missing_api_key
from tests.support.paths import PACKAGE_ROOT

CONTROLLED_CREDENTIAL = "controlled-native-credential"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith(("OPENCOLLAB_", "OPENAI_", "ANTHROPIC_", "DASHSCOPE_")):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("OPENCOLLAB_BUDGET_NUDGE_MODE", "off")
    monkeypatch.setenv("OPENCOLLAB_WRITE_NUDGE_MODE", "off")
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "native.py").write_text(
        "from opencollab.application.workflow_registry import workflow\n"
        "@workflow(name='native-auth')\n"
        "async def run(ctx, args):\n"
        "    return await ctx.agent('Answer briefly', tools=[])\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCOLLAB_WORKFLOWS_DIR", "workflows")


def _arguments(tmp_path, entry, provider, base_url):
    common = ["--workspace", str(tmp_path), "--provider", provider,
              "--model", "fixture-model", "--base-url", base_url]
    if entry == "workflow":
        return ["workflow", "run", "native-auth", *common, "--no-save", "--no-trace"]
    team = tmp_path / "team.yaml"
    team.write_text("entry: reader\nroles:\n  reader:\n    prompt: Answer briefly.\n    tools: []\n", encoding="utf-8")
    return [*common, "--team-config", str(team), "--no-worktrees", "--prompt", "Answer briefly"]


@pytest.fixture
def native_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                             "api_key": self.headers.get("X-Api-Key")})
            payload = json.dumps({
                "id": "msg_native_auth", "type": "message", "role": "assistant", "model": body["model"],
                "content": [{"type": "text", "text": "Native credential accepted."}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 3, "output_tokens": 2},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("entry", ["team", "workflow"])
@pytest.mark.parametrize("credential_env", ["ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"])
async def test_native_credentials_complete_sdk_and_cli_requests(
    monkeypatch, tmp_path, native_server, entry, credential_env,
):
    monkeypatch.setenv(credential_env, CONTROLLED_CREDENTIAL)
    base_url, requests = native_server
    config = build_config(str(tmp_path), overrides={"provider": "anthropic"})
    assert config.api_key == (CONTROLLED_CREDENTIAL if credential_env == "ANTHROPIC_API_KEY" else None)
    oc = OpenCollab(tmp_path, provider="anthropic", model="fixture-model", base_url=base_url)
    result = await oc.agent("Answer briefly", tools=(), trace=False)
    assert result.ok and result.output == "Native credential accepted." and result.tokens == 5

    env = {**os.environ, "PYTHONPATH": str(PACKAGE_ROOT), "NO_COLOR": "1"}
    cli = subprocess.run(
        [sys.executable, "-m", "opencollab", *_arguments(tmp_path, entry, "anthropic", base_url)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert cli.returncode == 0, cli.stdout + cli.stderr
    assert "Native credential accepted." in cli.stdout
    expected = {"path": "/v1/messages", "authorization": None, "api_key": None}
    if credential_env == "ANTHROPIC_AUTH_TOKEN":
        expected["authorization"] = f"Bearer {CONTROLLED_CREDENTIAL}"
    else:
        expected["api_key"] = CONTROLLED_CREDENTIAL
    assert requests == [expected, expected]


@pytest.mark.parametrize("entry", ["team", "workflow"])
@pytest.mark.parametrize("token", ["", " \t "])
def test_blank_native_token_keeps_missing_credential_diagnostic(monkeypatch, tmp_path, entry, token):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", token)
    assert missing_api_key("anthropic", None)
    result = CliRunner().invoke(app, _arguments(tmp_path, entry, "anthropic", "http://127.0.0.1:1"))
    assert result.exit_code == 1
    assert "Missing API key" in result.stdout
    assert "ANTHROPIC_AUTH_TOKEN" in result.stdout


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
@pytest.mark.parametrize("entry", ["team", "workflow"])
def test_native_token_does_not_satisfy_other_providers(monkeypatch, tmp_path, provider, entry):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", CONTROLLED_CREDENTIAL)
    assert missing_api_key(provider, None)
    assert "ANTHROPIC_AUTH_TOKEN" not in accepted_api_key_envs(provider)
    result = CliRunner().invoke(app, _arguments(tmp_path, entry, provider, "http://127.0.0.1:1"))
    assert result.exit_code == 1
    assert "Missing API key" in result.stdout
    assert "ANTHROPIC_AUTH_TOKEN" not in result.stdout
