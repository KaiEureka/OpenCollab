from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from opencollab.adapters.worktree_pool import WorktreePool
from tests.runtime.test_worktree_root_physical_delivery import _replay, _source
from tests.runtime.test_worktree_submodule_delivery import _remove_retained
from tests.workflows.test_candidate_physical_submodules import _index
from tests.workflows.test_workflow_candidate_workspace import _git


@pytest.fixture
def ignore_file(tmp_path, monkeypatch):
    ignored = tmp_path / "global-ignore"
    ignored.write_text("")
    configuration = tmp_path / "gitconfig"
    _git(tmp_path, "config", "--file", str(configuration), "core.excludesFile", str(ignored))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(configuration))
    return ignored


@pytest.mark.parametrize(
    ("ignore_kind", "name"),
    [("none", "extra.dat"), ("global", "extra.dat"), ("global", ".env"),
     ("repository", "extra.dat"), ("repository-info", "extra.dat")],
)
async def test_default_pool_retains_ignored_candidate_until_recoverable_delivery(
    tmp_path, ignore_file, ignore_kind, name,
):
    source = _source(tmp_path, "clean")
    if ignore_kind == "global":
        ignore_file.write_text(f"{name}\n")
    elif ignore_kind == "repository":
        (source / ".gitignore").write_text(f"{name}\n")
        _git(source, "add", ".gitignore")
        _git(source, "commit", "-qm", "Track repository ignore rules")
    elif ignore_kind == "repository-info":
        (source / ".git/info/exclude").write_text(f"{name}\n")
    source_index = _index(source)
    pool = WorktreePool(str(source), use_worktrees=True)
    candidate = await pool.acquire("coder")
    folder = Path(candidate.workspace)
    try:
        await candidate.write_file(name, "42\n")
        await candidate.write_file(
            "app/entry.py", "from pathlib import Path\n"
            f"assert Path({name!r}).read_bytes() == b'42\\n'\nprint(42)\n",
        )
        tested = await candidate.exec_cmd(shlex.join((sys.executable, "-B", "app/entry.py")))
        assert (tested.returncode, tested.stdout) == (0, "42\n")
        index = _index(folder)
        if ignore_kind != "none":
            for _ in range(2):
                with pytest.raises(RuntimeError, match="ignored untracked files"):
                    await candidate.get_diff()
                assert Path(candidate.recovery_location) == folder
                with pytest.raises(OSError, match="retained"):
                    await pool.release()
                assert (folder / name).read_bytes() == b"42\n"
                assert _index(folder) == index
                assert _index(source) == source_index
                assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 2
            if ignore_kind == "global":
                ignore_file.write_text("")
            elif ignore_kind == "repository":
                await candidate.write_file(".gitignore", "")
            else:
                (source / ".git/info/exclude").write_text("")
        patch = await candidate.get_diff()
        assert f"diff --git a/{name} b/{name}" in patch
        assert _index(folder) == index
        assert _index(source) == source_index
        assert candidate.recovery_location is None
        assert _replay(source, patch) == tested.stdout
        assert (source / name).read_bytes() == b"42\n"
    finally:
        if candidate.recovery_location is not None:
            _remove_retained(source, candidate)
        else:
            await pool.release()
    assert not folder.exists()
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1


async def test_default_pool_preserves_globally_ignored_source_file(tmp_path, ignore_file):
    source = _source(tmp_path, "clean")
    ignore_file.write_text(".env\n")
    secret = source / ".env"
    secret.write_text("retained source secret\n")
    index = _index(source)
    pool = WorktreePool(str(source), use_worktrees=True)
    candidate = await pool.acquire("coder")
    folder = Path(candidate.workspace)
    try:
        assert not (folder / ".env").exists()
        await candidate.write_file("app/entry.py", "print(42)\n")
        patch = await candidate.get_diff()
        assert ".env" not in patch
        assert _replay(source, patch) == "42\n"
        assert secret.read_text() == "retained source secret\n"
        assert _index(source) == index
    finally:
        await pool.release()
    assert not folder.exists()
    assert secret.read_text() == "retained source secret\n"


async def test_default_pool_delivers_tracked_file_matching_global_ignore(tmp_path, ignore_file):
    source = _source(tmp_path, "clean")
    (source / "extra.dat").write_text("0\n")
    _git(source, "add", "extra.dat")
    _git(source, "commit", "-qm", "Add tracked data")
    ignore_file.write_text("extra.dat\n")
    index = _index(source)
    pool = WorktreePool(str(source), use_worktrees=True)
    candidate = await pool.acquire("coder")
    folder = Path(candidate.workspace)
    try:
        await candidate.write_file("extra.dat", "42\n")
        await candidate.write_file(
            "app/entry.py", "from pathlib import Path\nprint(Path('extra.dat').read_text().strip())\n",
        )
        patch = await candidate.get_diff()
        assert "diff --git a/extra.dat b/extra.dat" in patch
        assert _replay(source, patch) == "42\n"
        assert (source / "extra.dat").read_bytes() == b"42\n"
        assert _index(source) == index
    finally:
        await pool.release()
    assert not folder.exists()
