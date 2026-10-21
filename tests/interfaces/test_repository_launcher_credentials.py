from __future__ import annotations

import os
import shlex
import subprocess
import sys

import pytest

from tests.support.paths import PACKAGE_ROOT, REPO_ROOT


@pytest.fixture
def launcher_workspace(tmp_path):
    scripts = tmp_path / "scripts"
    configs = tmp_path / "configs"
    cli_dir = tmp_path / ".venv" / "bin"
    scripts.mkdir()
    configs.mkdir()
    cli_dir.mkdir(parents=True)
    launcher = scripts / "start_opencollab.sh"
    launcher.write_bytes((REPO_ROOT / "scripts" / "start_opencollab.sh").read_bytes())
    (configs / ".env").write_text("")
    cli = cli_dir / "opencollab"
    cli.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -m opencollab "$@"\n')
    cli.chmod(0o755)
    return tmp_path


def _run_launcher(workspace, *, args=("--no-worktrees",), env=None):
    isolated = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("OPENCOLLAB_", "OPENAI_", "ANTHROPIC_", "DASHSCOPE_"))
    }
    return subprocess.run(
        ["bash", str(workspace / "scripts" / "start_opencollab.sh"), *args],
        cwd=workspace, env=isolated | {
            "PYTHONPATH": str(PACKAGE_ROOT), "PYTHONDONTWRITEBYTECODE": "1",
            "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "240", "TERMINAL_WIDTH": "240",
        } | (env or {}), input="exit\n", capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("source", ["environment", "dotenv"])
@pytest.mark.parametrize("name", [
    "OPENCOLLAB_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "DASHSCOPE_API_KEY", "ANTHROPIC_AUTH_TOKEN",
])
def test_repository_launcher_accepts_supported_credentials(launcher_workspace, source, name):
    env = {name: "controlled-credential"} if source == "environment" else {}
    if name.startswith("ANTHROPIC_"):
        env["OPENCOLLAB_PROVIDER"] = "anthropic"
    elif name == "DASHSCOPE_API_KEY":
        env["OPENCOLLAB_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if source == "dotenv":
        (launcher_workspace / "configs" / ".env").write_text(f"{name}=controlled-credential\n")
    result = _run_launcher(launcher_workspace, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(launcher_workspace) in result.stdout
    assert "controlled-credential" not in result.stdout + result.stderr


@pytest.mark.parametrize("source", ["environment", "dotenv"])
def test_repository_launcher_rejects_empty_token(launcher_workspace, source):
    env = {"ANTHROPIC_AUTH_TOKEN": ""} if source == "environment" else {}
    if source == "dotenv":
        (launcher_workspace / "configs" / ".env").write_text("ANTHROPIC_AUTH_TOKEN=\n")
    result = _run_launcher(launcher_workspace, env=env)
    assert result.returncode == 1
    assert "Missing API key" in result.stdout
    assert str(launcher_workspace) not in result.stdout


def test_repository_launcher_help_names_native_token(launcher_workspace):
    result = _run_launcher(launcher_workspace, args=("help",))
    assert result.returncode == 0
    assert "ANTHROPIC_AUTH_TOKEN" in result.stdout
