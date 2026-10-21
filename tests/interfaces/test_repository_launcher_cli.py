from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
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
    cli = root / ".venv" / "bin" / "opencollab"
    cli.parent.mkdir(parents=True)
    cli.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -m opencollab "$@"\n',
        encoding="utf-8",
    )
    cli.chmod(0o755)
    return root


def _cli(root: Path, arguments: list[str], *, launcher: bool = True, input_text: str | None = None):
    command = (
        ["bash", str(root / "scripts" / "start_opencollab.sh")]
        if launcher else [sys.executable, "-m", "opencollab"]
    )
    return subprocess.run(
        [*command, *arguments],
        cwd=root,
        env={
            "PATH": os.environ["PATH"],
            "PYTHONPATH": str(PACKAGE_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "NO_COLOR": "1",
            "TERM": "dumb",
            "COLUMNS": "240",
            "TERMINAL_WIDTH": "240",
        },
        input=input_text,
        capture_output=True,
        text=True,
        timeout=30,
    )


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
