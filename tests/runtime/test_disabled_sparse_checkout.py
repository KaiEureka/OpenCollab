from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from opencollab.adapters.env import LocalEnvironment, WorktreeEnvironment
from opencollab.adapters.tools.git_diff import GitDiffTool
from opencollab.adapters.worktree_pool import WorktreePool
from opencollab.application.tool_execution import ToolRuntime


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True, capture_output=True,
    ).stdout


@pytest.mark.parametrize("disabled_sparse", [False, True])
async def test_full_checkout_exports_after_sparse_checkout_disable(tmp_path, monkeypatch, disabled_sparse):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    source = tmp_path / "source"
    source.mkdir()
    for name, content in (("src/app.py", "value = 1\n"), ("docs/guide.txt", "guide\n")):
        path = source / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(source, "init", "-q")
    _git(source, "add", ".")
    _git(source, "-c", "user.name=Test User", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    if disabled_sparse:
        _git(source, "sparse-checkout", "init", "--cone")
        _git(source, "sparse-checkout", "set", "src")
        assert not (source / "docs/guide.txt").exists()
        _git(source, "sparse-checkout", "disable")
        for key in ("core.sparsecheckout", "core.sparsecheckoutcone"):
            assert _git(source, "config", "--type=bool", "--get", key) == "false\n"
    assert (source / "docs/guide.txt").read_text(encoding="utf-8") == "guide\n"
    assert _git(source, "status", "--porcelain") == ""
    assert all(line.startswith("H ") for line in _git(source, "ls-files", "-t").splitlines())

    local = LocalEnvironment(str(source))
    try:
        result = await GitDiffTool().execute_with_runtime({}, ToolRuntime(local, None, None))
        assert result == "Status: working tree clean.\n\ndiff vs HEAD: (no changes)"
    finally:
        await local.cleanup()

    env = WorktreeEnvironment(str(source))
    candidate = Path(await env.setup())
    try:
        await env.write_file("src/app.py", "value = 2\n")
        expected = _git(candidate, "diff", "--binary", "--full-index", "--no-color",
                        "--unified=3", "--src-prefix=a/", "--dst-prefix=b/", "HEAD")
        assert "+value = 2\n" in expected
        assert await env.get_diff() == expected
        assert _git(candidate, "diff", "--cached") == ""
    finally:
        await env.cleanup()
    assert not candidate.exists()

    pool = WorktreePool(str(source), use_worktrees=True, enable_adoption=True)
    try:
        worker = await pool.acquire("coder")
        pooled_candidate = Path(worker.workspace)
        await worker.write_file("src/app.py", "value = 2\n")
        assert await worker.get_diff() == expected
    finally:
        await pool.release()
    assert not pooled_candidate.exists()
    assert _git(source, "status", "--porcelain") == ""
    assert (source / "src/app.py").read_text(encoding="utf-8") == "value = 1\n"
