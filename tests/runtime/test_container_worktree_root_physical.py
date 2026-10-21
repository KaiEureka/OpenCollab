from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from opencollab.adapters import worktree_pool
from opencollab.adapters.worktree_pool import WorktreePool
from opencollab.environments import attach_container
from tests.support.worktree_delivery_support import CONTAINER_ID
from tests.support.worktree_delivery_support import worktree_transport as worktree_transport
from tests.workflows.test_candidate_physical_submodules import _index
from tests.workflows.test_workflow_candidate_workspace import _git


def _source(tmp_path, ending="lf", *, data_subdirectory=False):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.name", "Test User")
    _git(source, "config", "user.email", "test@example.invalid")
    (source / "app").mkdir()
    (source / "app/entry.py").write_text("print(0)\n")
    data = source / ("app/data.txt" if data_subdirectory else "data.txt")
    data.write_bytes(b"value\n")
    (source / ".gitattributes").write_text(f"*.txt text eol={ending}\n")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "Prepare converted root files")
    if ending == "crlf":
        data.unlink()
        _git(source, "checkout-index", "--all", "--force")
        assert data.read_bytes() == b"value\r\n"
    return source


async def _candidate(source, workspace, tmp_path, monkeypatch, backend):
    monkeypatch.setattr(worktree_pool, "CONTAINER_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    base = attach_container(container_id=CONTAINER_ID, workspace=str(workspace)) if backend == "container" else None
    pool = WorktreePool(str(workspace), use_worktrees=True, base_environment=base)
    candidate = await pool.acquire("coder")
    result = await candidate.exec_cmd("git rev-parse --show-toplevel")
    assert result.returncode == 0
    return base, pool, candidate, Path(result.stdout.removesuffix("\n"))


def _command(folder):
    return f"cd -- {shlex.quote(str(folder))} && {shlex.join((sys.executable, '-B', 'app/entry.py'))}"


def _replay(source, patch):
    subprocess.run(
        ["git", "-C", str(source), "apply", "--binary", "-"], input=patch,
        text=True, capture_output=True, check=True,
    )
    return subprocess.run(
        [sys.executable, "-B", "app/entry.py"], cwd=source,
        text=True, capture_output=True, check=True,
    ).stdout


async def _release(source, base, pool, candidate, folder):
    if candidate.recovery_location is not None:
        _git(source, "worktree", "remove", "--force", "--force", str(folder))
        _git(source, "update-ref", "-d", f"refs/heads/{candidate._branch}", candidate._base_commit)
    else:
        await pool.release()
    if base is not None:
        await base.cleanup()


@pytest.mark.parametrize("subdirectory", [False, True], ids=["root", "subdirectory"])
@pytest.mark.parametrize(
    ("backend", "changed"), [("host", True), ("container", False), ("container", True)],
    ids=["host-crlf", "container-lf", "container-crlf"],
)
async def test_root_physical_patch_replays_the_executed_bytes(
    worktree_transport, tmp_path, monkeypatch, subdirectory, backend, changed,
):
    source = _source(tmp_path, data_subdirectory=subdirectory)
    relative_data = "app/data.txt" if subdirectory else "data.txt"
    source_index = _index(source)
    workspace = source / "app" if subdirectory else source
    base, pool, candidate, folder = await _candidate(source, workspace, tmp_path, monkeypatch, backend)
    metadata = Path(_git(folder, "rev-parse", "--absolute-git-dir").strip())
    try:
        if changed:
            await candidate.write_file("data.txt", "value\r\n")
        await candidate.write_file(
            "entry.py" if subdirectory else "app/entry.py", "from pathlib import Path\n"
            f"print(Path({relative_data!r}).read_bytes().count(b'\\r\\n'))\n",
        )
        tested = await candidate.exec_cmd(_command(folder))
        assert (tested.returncode, tested.stdout) == (0, "1\n" if changed else "0\n")
        candidate_index = _index(folder)
        patch = await candidate.get_diff()
        assert _index(source) == source_index
        assert _index(folder) == candidate_index
        assert (source / relative_data).read_bytes() == b"value\n"
        assert _replay(source, patch) == tested.stdout
        assert (f"diff --git a/{relative_data}" in patch) is changed
        assert candidate.recovery_location is None
        assert _index(source) == source_index
    finally:
        await _release(source, base, pool, candidate, folder)
    assert not folder.exists()
    assert not metadata.exists()
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1


async def test_container_keeps_root_bytes_that_plain_patch_replay_converts(
    worktree_transport, tmp_path, monkeypatch,
):
    source = _source(tmp_path, "crlf")
    source_index = _index(source)
    base, pool, candidate, folder = await _candidate(source, source, tmp_path, monkeypatch, "container")
    try:
        await candidate.write_file("data.txt", "value\n")
        await candidate.write_file(
            "app/entry.py", "from pathlib import Path\n"
            "print(Path('data.txt').read_bytes().count(b'\\r\\n'))\n",
        )
        tested = await candidate.exec_cmd(_command(folder))
        assert (tested.returncode, tested.stdout) == (0, "0\n")
        candidate_index = _index(folder)
        try:
            patch = await candidate.get_diff()
        except RuntimeError as failure:
            assert "physical changes cannot be delivered" in str(failure)
        else:
            assert _replay(source, patch) == tested.stdout
            pytest.fail("undeliverable root bytes must retain the executed container workspace")
        assert candidate.recovery_location == f"{CONTAINER_ID}:{folder}"
        with pytest.raises(OSError, match="retained"):
            await pool.release()
        assert (folder / "data.txt").read_bytes() == b"value\n"
        assert (source / "data.txt").read_bytes() == b"value\r\n"
        assert _index(source) == source_index
        assert _index(folder) == candidate_index
    finally:
        await _release(source, base, pool, candidate, folder)
    assert not folder.exists()


@pytest.mark.parametrize("source_current", [False, True], ids=["source-old", "source-current"])
async def test_container_root_patch_keeps_own_commit_after_a_new_diff_base(
    worktree_transport, tmp_path, monkeypatch, source_current,
):
    source = _source(tmp_path)
    old = _git(source, "rev-parse", "HEAD").strip()
    (source / "data.txt").write_bytes(b"updated\n")
    _git(source, "add", "data.txt")
    _git(source, "commit", "-qm", "Update root input")
    new = _git(source, "rev-parse", "HEAD").strip()
    _git(source, "reset", "--hard", old)
    base, pool, candidate, folder = await _candidate(source, source, tmp_path, monkeypatch, "container")
    metadata = Path(_git(folder, "rev-parse", "--absolute-git-dir").strip())
    try:
        _git(folder, "checkout", "-q", new)
        if source_current:
            _git(source, "checkout", "-q", new)
        await candidate.write_file("data.txt", "updated\r\n")
        await candidate.write_file(
            "app/entry.py", "from pathlib import Path\n"
            "data = Path('data.txt').read_bytes()\n"
            "assert data == b'updated\\r\\n'\nprint(data.count(b'\\r\\n'))\n",
        )
        _git(folder, "add", ".")
        _git(folder, "commit", "-qm", "Save physical root output")
        own_commit = _git(folder, "rev-parse", "HEAD").strip()
        tested = await candidate.exec_cmd(_command(folder))
        assert (tested.returncode, tested.stdout) == (0, "1\n")
        candidate_index, source_index = _index(folder), _index(source)
        patch = await candidate.get_diff()
        assert candidate.diff_base == new
        assert candidate.head_commit == own_commit
        assert candidate.own_commits == (own_commit,)
        assert candidate.own_commit_count == 1
        assert "diff --git a/data.txt" in patch
        assert (source / "data.txt").read_bytes() == (b"updated\n" if source_current else b"value\n")
        assert _index(source) == source_index
        assert _index(folder) == candidate_index
        _git(folder, "gc", "--prune=now")
        assert await candidate.get_diff() == patch
        _git(source, "checkout", "-q", new)
        assert _replay(source, patch) == tested.stdout
    finally:
        await _release(source, base, pool, candidate, folder)
    assert not folder.exists()
    assert not metadata.exists()


async def test_container_root_patch_keeps_globally_ignored_candidate_files(
    worktree_transport, tmp_path, monkeypatch,
):
    ignored = tmp_path / "global-ignore"
    ignored.write_text("extra.dat\n")
    configuration = tmp_path / "gitconfig"
    configuration.write_text(f"[core]\nexcludesFile = {ignored}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(configuration))
    source = _source(tmp_path)
    source_index = _index(source)
    base, pool, candidate, folder = await _candidate(source, source, tmp_path, monkeypatch, "container")
    try:
        await candidate.write_file("extra.dat", "42\n")
        await candidate.write_file(
            "app/entry.py", "from pathlib import Path\nprint(Path('extra.dat').read_text().strip())\n",
        )
        tested = await candidate.exec_cmd(_command(folder))
        assert (tested.returncode, tested.stdout) == (0, "42\n")
        candidate_index = _index(folder)
        patch = await candidate.get_diff()
        assert _replay(source, patch) == tested.stdout
        assert "diff --git a/extra.dat" in patch
        assert _index(source) == source_index
        assert _index(folder) == candidate_index
    finally:
        await _release(source, base, pool, candidate, folder)
    assert not folder.exists()
