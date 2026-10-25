from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from opencollab.adapters._env_process import ProcessCleanupError
from opencollab.adapters._team_candidate_environment import _TeamCandidateEnvironment
from opencollab.adapters.env import LocalEnvironment, WorktreeEnvironment
from tests.interfaces.test_member_authored_workflow import _launch, _manager
from tests.support.paths import REPO_ROOT


@pytest.fixture(autouse=True)
def _child_import_path(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", os.devnull)


@pytest.mark.parametrize("kind", ["local", "copy-worktree", "git-worktree", "team-candidate"])
@pytest.mark.parametrize("size", [1024, 4 * 1024 * 1024 + 1024])
async def test_authored_result_and_native_artifacts_cross_file_read_limit(tmp_path, kind, size):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "authored.py").write_text(
        "async def main(ctx, args):\n    return {'payload': args['payload']}\n"
    )
    (workspace / ".gitignore").write_text(".opencollab/\n")
    if kind == "git-worktree":
        for args in (("init", "-q"), ("add", "."), (
            "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "-qm", "test: prepare workflow fixture",
        )):
            subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True)
    environment = (LocalEnvironment if kind in {"local", "team-candidate"} else WorktreeEnvironment)(str(workspace))
    if kind == "team-candidate":
        environment = _TeamCandidateEnvironment(
            SimpleNamespace(environment=environment, cleanup=environment.cleanup), str(workspace),
        )
    await environment.setup()
    parent, tool, runtime = _manager(environment)
    saved = tmp_path / "saved"
    saved.mkdir()
    tool._save_dir = str(saved)
    payload = "x" * size
    try:
        child = await _launch(tool, runtime, args={"payload": payload})
        result = json.loads(await tool.execute_with_runtime({"action": "wait", "id": child["id"]}, runtime))
        native_path = Path(child["artifacts"]) / "result.json"
        assert json.loads(native_path.read_text())["status"] == "completed"
        assert result["status"] == "completed", result
        assert result["result"]["output"] == {"payload": payload}
        assert tool._reserved_tokens() == 0
        persisted = Path(result["artifacts"])
        assert json.loads((persisted / "result.json").read_text())["result"]["output"] == {"payload": payload}
        assert json.loads((persisted / "workflow.json").read_text())["args"] == {"payload": payload}
        if size > 4 * 1024 * 1024:
            assert native_path.stat().st_size > 4 * 1024 * 1024
            assert (persisted / "workflow.json").stat().st_size > 4 * 1024 * 1024
            with pytest.raises(ValueError, match="read target exceeds"):
                await environment.read_file(str(native_path))
    finally:
        try:
            await parent.aclose()
        finally:
            if isinstance(environment, WorktreeEnvironment):
                await environment.get_diff()
            await environment.cleanup()


@pytest.mark.parametrize("failure", ["signature", "async-start"])
async def test_executor_start_failure_finishes_child_and_releases_budget(tmp_path, monkeypatch, failure):
    (tmp_path / "authored.py").write_text("async def main(ctx, args):\n    return 1\n")
    environment = LocalEnvironment(str(tmp_path))
    parent, tool, runtime = _manager(environment)
    native_exec = environment.exec_cmd

    if failure == "signature":
        def fail_to_start(command, timeout=120):
            raise AssertionError("the input_bytes argument must fail during binding")
    else:
        async def fail_to_start(command, timeout=120, *, input_bytes=None):
            raise OSError("executor startup failed")

    monkeypatch.setattr(environment, "exec_cmd", fail_to_start)
    try:
        child = await _launch(tool, runtime)
        assert tool._reserved_tokens() == 500
        result = json.loads(await tool.execute_with_runtime({"action": "wait", "id": child["id"]}, runtime))
        assert result["status"] == "failed"
        assert ("TypeError" if failure == "signature" else "executor startup failed") in result["result"]["reason"]
        assert tool._reserved_tokens() == 0
        owned = tool._children[child["id"]]
        assert owned.task.done() and owned.task.exception() is None
        assert owned.bridge._pool._closed
        assert owned.cleanup_error is None
        assert not environment._processes._processes
    finally:
        monkeypatch.setattr(environment, "exec_cmd", native_exec)
        try:
            await parent.aclose()
        finally:
            await environment.cleanup()


async def test_executor_cleanup_failure_retains_recovery_error(tmp_path, monkeypatch):
    (tmp_path / "authored.py").write_text("async def main(ctx, args):\n    return 1\n")
    environment = LocalEnvironment(str(tmp_path))
    parent, tool, runtime = _manager(environment)
    native_exec = environment.exec_cmd

    async def cleanup_failed(command, timeout=120, *, input_bytes=None):
        raise ProcessCleanupError("process group still alive")

    monkeypatch.setattr(environment, "exec_cmd", cleanup_failed)
    try:
        child = await _launch(tool, runtime)
        result = json.loads(await tool.execute_with_runtime({"action": "wait", "id": child["id"]}, runtime))
        assert result["status"] == "failed"
        assert "process group still alive" in result["result"]["reason"]
        owned = tool._children[child["id"]]
        assert isinstance(owned.cleanup_error, ProcessCleanupError)
        assert tool._reserved_tokens() == 0
        with pytest.raises(RuntimeError, match="process group still alive"):
            await parent.aclose()
        assert not owned.artifacts_removed
    finally:
        monkeypatch.setattr(environment, "exec_cmd", native_exec)
        await environment.cleanup()
