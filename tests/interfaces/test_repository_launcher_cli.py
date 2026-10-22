from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.support.paths import PACKAGE_ROOT, REPO_ROOT


@pytest.fixture
def launcher_project(tmp_path):
    root = tmp_path / "physical checkout"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "start_opencollab.sh").write_bytes(
        (REPO_ROOT / "scripts" / "start_opencollab.sh").read_bytes()
    )
    configs = root / "configs"
    configs.mkdir()
    (configs / ".env").write_text("OPENCOLLAB_API_KEY=fixture-key\n", encoding="utf-8")  # pragma: allowlist secret
    (configs / ".env.example").write_text("OPENCOLLAB_API_KEY=\n", encoding="utf-8")
    cli = root / ".venv" / "bin" / "opencollab"
    cli.parent.mkdir(parents=True)
    cli.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -m opencollab "$@"\n',
        encoding="utf-8",
    )
    cli.chmod(0o755)
    return root


def _cli(
    root: Path, arguments: list[str], *, launcher: bool = True,
    input_text: str | None = None, env: dict[str, str] | None = None,
    cwd: Path | None = None,
):
    command = (
        ["bash", str(root / "scripts" / "start_opencollab.sh")]
        if launcher else [sys.executable, "-m", "opencollab"]
    )
    return subprocess.run(
        [*command, *arguments],
        cwd=cwd or root,
        env={
            "PATH": os.environ["PATH"],
            "PYTHONPATH": str(PACKAGE_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "NO_COLOR": "1",
            "TERM": "dumb",
            "COLUMNS": "240",
            "TERMINAL_WIDTH": "240",
            "NO_PROXY": "127.0.0.1,localhost",
            "OPENCOLLAB_API_USAGE_LOG": str(root / "usage.jsonl"),
        } | (env or {}),
        input=input_text,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture
def completion_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers.get("Authorization"), body["model"]))
            response = json.dumps({
                "id": "chat-launcher", "object": "chat.completion", "created": 1,
                "model": body["model"],
                "choices": [{"index": 0, "message": {
                    "role": "assistant", "content": "Launcher completion succeeded",
                }, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("configured_checkout", [False, True], ids=["no-dotenv", "with-dotenv"])
@pytest.mark.parametrize("source", [
    "cli", "cli-equals", "config-file", "environment", "missing", "empty-cli",
])
def test_repository_launcher_uses_cli_credential_resolution(
    launcher_project, tmp_path, completion_server, source, configured_checkout,
):
    root = launcher_project
    dotenv = root / "configs" / ".env"
    defaults = "OPENCOLLAB_MODEL=checkout-default\n"  # pragma: allowlist secret
    if configured_checkout:
        dotenv.write_text(defaults, encoding="utf-8")
    else:
        dotenv.unlink()
    team = root / "team.yaml"
    team.write_text(
        "entry: reader\nroles:\n  reader:\n    prompt: Reply briefly.\n    tools: []\n",
        encoding="utf-8",
    )
    endpoint, requests = completion_server
    arguments = [
        "--provider", "openai", "--model", "launcher-model", "--base-url", endpoint,
        "--team-config", str(team), "--no-worktrees", "--prompt", "Reply briefly.",
    ]
    env = {}
    if source == "cli":
        arguments.extend(["--api-key", "fixture-key"])
    elif source == "cli-equals":
        arguments.append("--api-key=fixture-key")
    elif source == "empty-cli":
        arguments.extend(["--api-key", ""])
    elif source == "config-file":
        config = tmp_path / "selected.env"
        config.write_text("OPENCOLLAB_API_KEY=fixture-key\n", encoding="utf-8")  # pragma: allowlist secret
        env["OPENCOLLAB_CONFIG_FILE"] = str(config)
    elif source == "environment":
        env["OPENCOLLAB_API_KEY"] = "fixture-key"  # pragma: allowlist secret

    for launcher in [False, True]:
        before = len(requests)
        result = _cli(root, arguments, launcher=launcher, env=env)
        output = result.stdout + result.stderr
        assert "fixture-key" not in output
        initializes = launcher and not configured_checkout and source in {"environment", "missing"}
        if initializes:
            assert result.returncode == 1, output
            assert "Created" in output
            assert "Edit it and configure provider credentials" in output
            assert len(requests) == before
        elif source in {"missing", "empty-cli"}:
            assert result.returncode == 1, output
            assert "Missing API key" in output
            assert len(requests) == before
        else:
            assert result.returncode == 0, output
            assert "Launcher completion succeeded" in output
            assert requests[before:] == [
                ("/v1/chat/completions", "Bearer fixture-key", "launcher-model"),
            ]
        if initializes:
            assert dotenv.read_bytes() == (root / "configs" / ".env.example").read_bytes()
        elif configured_checkout:
            assert dotenv.read_text(encoding="utf-8") == defaults
        else:
            assert not dotenv.exists()


@pytest.mark.parametrize("arguments", [
    [], ["--trace"], ["--no-worktrees"], ["--team-config", "team.yaml"],
], ids=["default", "trace", "no-worktrees", "team-config"])
def test_repository_launcher_initializes_default_configuration(launcher_project, arguments):
    root = launcher_project
    dotenv = root / "configs" / ".env"
    dotenv.unlink()
    result = _cli(root, arguments, input_text="exit\n")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "Created" in result.stdout
    assert "Edit it and configure provider credentials before starting OpenCollab." in result.stdout
    assert dotenv.read_bytes() == (root / "configs" / ".env.example").read_bytes()
    assert not (root / ".opencollab").exists()


def test_repository_launcher_reports_explicit_configuration_errors(launcher_project, tmp_path):
    root = launcher_project
    (root / "configs" / ".env").unlink()
    result = _cli(root, [], env={"OPENCOLLAB_CONFIG_FILE": str(tmp_path / "missing.env")})

    assert result.returncode == 2, result.stdout + result.stderr
    assert "explicit config env path does not exist" in result.stderr
    assert "missing.env" in result.stderr
    assert not (root / "configs" / ".env").exists()


def test_repository_launcher_validates_existing_configuration(launcher_project):
    root = launcher_project
    config = root / "configs" / ".env"
    config.write_bytes(b"OPENCOLLAB_API_KEY=fixture-key\n\xff")  # pragma: allowlist secret
    result = _cli(root, ["--api-key", "fixture-key", "--prompt", "Reply briefly."])

    assert result.returncode == 2, result.stdout + result.stderr
    assert "encoding error" in result.stderr
    assert "expected UTF-8" in result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert "fixture-key" not in result.stdout + result.stderr


def test_repository_launcher_resolves_relative_config_from_checkout(launcher_project, tmp_path):
    root = launcher_project
    (root / "relative.env").write_text(
        "OPENCOLLAB_API_KEY=fixture-key\nOPENCOLLAB_MODEL=relative-model\n",  # pragma: allowlist secret
        encoding="utf-8",
    )
    (tmp_path / "relative.env").write_bytes(b"invalid\xff")
    result = _cli(
        root, ["--no-worktrees"], cwd=tmp_path, input_text="exit\n",
        env={"OPENCOLLAB_CONFIG_FILE": "relative.env"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "relative-model" in result.stdout
    assert str(root) in result.stdout
    assert "fixture-key" not in result.stdout + result.stderr


@pytest.mark.parametrize("arguments", [[], ["--no-worktrees"]], ids=["default", "root-option"])
def test_repository_launcher_interactive_physical_root(launcher_project, tmp_path, arguments):
    logical_root = tmp_path / "logical checkout"
    logical_root.symlink_to(launcher_project, target_is_directory=True)
    result = _cli(logical_root, arguments, input_text="exit\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert str(launcher_project) in result.stdout
    assert str(logical_root) not in result.stdout


def _workflow(root: Path, name: str):
    directory = root / "workflows"
    directory.mkdir(parents=True)
    configs = root / "configs"
    configs.mkdir(exist_ok=True)
    (configs / ".env").write_text("OPENCOLLAB_API_KEY=fixture-key\n", encoding="utf-8")  # pragma: allowlist secret
    (directory / "probe.py").write_text(
        "from pathlib import Path\n"
        "from opencollab.workflows import workflow\n"
        "root = Path(__file__).parent.parent\n"
        f"@workflow(name={name!r})\n"
        "async def probe(ctx, args):\n"
        "    (root / 'executed').write_text('ran', encoding='utf-8')\n"
        "    return {'workspace': str(root), 'budget': ctx.budget.total, 'args': args}\n",
        encoding="utf-8",
    )


def test_repository_launcher_runs_team_commands_and_child_options(launcher_project):
    root = launcher_project
    direct = _cli(root, ["team", "show"], launcher=False)
    launched = _cli(root, ["team", "show"])
    assert direct.returncode == launched.returncode == 0, launched.stdout + launched.stderr
    assert launched.stdout == direct.stdout
    assert "Configured roles" in launched.stdout

    for launcher, name in [(False, "direct team.yaml"), (True, "launched team.yaml")]:
        result = _cli(root, ["team", "init", name], launcher=launcher)
        assert result.returncode == 0, result.stdout + result.stderr
        assert (root / name).is_file()
    assert (root / "direct team.yaml").read_bytes() == (root / "launched team.yaml").read_bytes()

    arguments = ["team", "show", "--team-config", "launched team.yaml"]
    direct = _cli(root, arguments, launcher=False)
    launched = _cli(root, arguments)
    assert direct.returncode == launched.returncode == 0, launched.stdout + launched.stderr
    assert launched.stdout == direct.stdout
    assert "launched team.yaml" in launched.stdout


@pytest.mark.parametrize("explicit_workspace", [False, True], ids=["default", "child-option"])
def test_repository_launcher_lists_requested_workflows(launcher_project, tmp_path, explicit_workspace):
    root = launcher_project
    other = tmp_path / "other workspace"
    _workflow(root, "checkout-probe")
    _workflow(other, "requested-probe")
    options = ["--workspace", str(other)] if explicit_workspace else []
    expected, absent = (
        ("requested-probe", "checkout-probe")
        if explicit_workspace else ("checkout-probe", "requested-probe")
    )
    arguments = ["workflow", "list", *options]
    direct = _cli(root, arguments, launcher=False)
    launched = _cli(root, arguments)

    assert direct.returncode == launched.returncode == 0, launched.stdout + launched.stderr
    assert launched.stdout == direct.stdout
    assert expected in launched.stdout and absent not in launched.stdout
    assert not (root / "executed").exists()
    assert not (other / "executed").exists()


@pytest.mark.parametrize("explicit_workspace", [False, True], ids=["default", "child-option"])
def test_repository_launcher_executes_workflow_and_preserves_arguments(
    launcher_project, tmp_path, explicit_workspace,
):
    root = launcher_project
    other = tmp_path / "other workspace"
    _workflow(root, "launcher-probe")
    _workflow(other, "launcher-probe")
    selected, untouched = (other, root) if explicit_workspace else (root, other)
    options = ["--workspace", str(other)] if explicit_workspace else []
    arguments = [
        "workflow", "run", "launcher-probe", *options,
        "--budget", "15000", "--args", '{"message": "two words"}', "--no-save",
    ]
    expected = {
        "workspace": str(selected), "budget": 15000, "args": {"message": "two words"},
    }
    for launcher in [False, True]:
        result = _cli(root, arguments, launcher=launcher)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout) == expected
        assert (selected / "executed").read_text(encoding="utf-8") == "ran"
        assert not (untouched / "executed").exists()
        (selected / "executed").unlink()
