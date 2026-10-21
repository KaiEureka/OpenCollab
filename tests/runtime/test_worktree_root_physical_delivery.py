from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from opencollab.adapters.worktree_pool import WorktreePool
from tests.workflows.test_candidate_physical_submodules import _index
from tests.workflows.test_workflow_candidate_workspace import _git


def _source(tmp_path, setting):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.name", "Test User")
    _git(source, "config", "user.email", "test@example.invalid")
    (source / "app").mkdir()
    (source / "app/entry.py").write_text("pass\n")
    (source / "data.txt").write_bytes(b"one\ntwo\n")
    if setting in {"lf", "crlf"}:
        (source / ".gitattributes").write_text(f"*.txt text eol={setting}\n")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "准备源文件")
    return source


def _replay(source, patch):
    subprocess.run(
        ["git", "-C", str(source), "apply", "--binary", "-"],
        input=patch, text=True, capture_output=True, check=True,
    )
    return subprocess.run(
        [sys.executable, "-B", "app/entry.py"], cwd=source,
        text=True, capture_output=True, check=True,
    ).stdout


@pytest.mark.parametrize("subdirectory", [False, True])
@pytest.mark.parametrize("setting", ["clean", "assume-unchanged", "skip-worktree", "crlf"])
async def test_default_pool_starts_with_source_physical_bytes(tmp_path, subdirectory, setting):
    source = _source(tmp_path, setting)
    if setting in {"assume-unchanged", "skip-worktree"}:
        _git(source, "update-index", f"--{setting}", "data.txt")
        (source / "data.txt").write_bytes(b"source\ncontents\n")
    original = (source / "data.txt").read_bytes()
    assert _git(source, "--no-optional-locks", "status", "--porcelain") == ""
    index = _index(source)
    pool = WorktreePool(str(source / "app" if subdirectory else source), use_worktrees=True)
    candidate = await pool.acquire("coder")
    folder = Path((await candidate.exec_cmd("git rev-parse --show-toplevel")).stdout.strip())
    try:
        assert (folder / "data.txt").read_bytes() == original
        assert await candidate.get_diff() == ""
        updated = original + b"candidate\n"
        if setting == "crlf":
            updated = updated.replace(b"\n", b"\r\n")
        (folder / "data.txt").write_bytes(updated)
        (folder / "app/entry.py").write_text(
            "from pathlib import Path\n"
            f"assert Path('data.txt').read_bytes() == {updated!r}\nprint('passed')\n",
        )
        tested = await candidate.exec_cmd(
            f"cd {shlex.quote(str(folder))} && {shlex.join((sys.executable, '-B', 'app/entry.py'))}",
        )
        assert (tested.returncode, tested.stdout) == (0, "passed\n")
        if setting == "assume-unchanged":
            _git(folder, "add", ".")
            _git(folder, "commit", "-qm", "保存候选修改")
        candidate_index = _index(folder)
        patch = await candidate.get_diff()
        assert "diff --git a/data.txt" in patch
        assert _index(folder) == candidate_index
        assert _index(source) == index
        assert (source / "data.txt").read_bytes() == original
        assert _replay(source, patch) == tested.stdout
        assert (source / "data.txt").read_bytes() == updated
        assert candidate.recovery_location is None
    finally:
        await pool.release()
    assert not folder.exists()


@pytest.mark.parametrize("setting", ["clean", "assume-unchanged", "skip-worktree", "lf", "crlf"])
async def test_default_pool_delivers_hidden_root_physical_changes(tmp_path, setting):
    source = _source(tmp_path, setting)
    source_index = _index(source)
    pool = WorktreePool(str(source), use_worktrees=True)
    candidate = await pool.acquire("coder")
    folder = Path(candidate.workspace)
    updated = b"one\r\ntwo\r\n" if setting in {"lf", "crlf"} else b"candidate\ncontents\n"
    try:
        if setting in {"assume-unchanged", "skip-worktree"}:
            _git(folder, "update-index", f"--{setting}", "data.txt")
        (folder / "data.txt").write_bytes(updated)
        (folder / "app/entry.py").write_text(
            "from pathlib import Path\n"
            f"assert Path('data.txt').read_bytes() == {updated!r}\nprint('passed')\n",
        )
        tested = await candidate.exec_cmd(shlex.join((sys.executable, "-B", "app/entry.py")))
        assert (tested.returncode, tested.stdout) == (0, "passed\n")
        index = _index(folder)
        patch = await candidate.get_diff()
        assert "diff --git a/data.txt" in patch
        assert _index(folder) == index
        assert _index(source) == source_index
        assert (source / "data.txt").read_bytes() == b"one\ntwo\n"
        assert _replay(source, patch) == tested.stdout
        assert (source / "data.txt").read_bytes() == updated
        assert candidate.recovery_location is None
    finally:
        await pool.release()
    assert not folder.exists()


async def test_default_pool_delivers_own_commit_after_checkout_of_new_source_commit(tmp_path):
    source = _source(tmp_path, "crlf")
    pool = WorktreePool(str(source), use_worktrees=True)
    candidate = await pool.acquire("coder")
    folder = Path(candidate.workspace)
    metadata = Path(_git(folder, "rev-parse", "--absolute-git-dir").strip())
    try:
        (source / "data.txt").write_bytes(b"new\nparent\n")
        _git(source, "add", "data.txt")
        _git(source, "commit", "-qm", "更新源提交")
        parent = _git(source, "rev-parse", "HEAD").strip()
        _git(folder, "checkout", "-q", "--force", parent)
        updated = b"new\r\nparent\r\ncandidate\r\n"
        (folder / "data.txt").write_bytes(updated)
        (folder / "app/entry.py").write_text(
            "from pathlib import Path\n"
            f"assert Path('data.txt').read_bytes() == {updated!r}\nprint('passed')\n",
        )
        _git(folder, "add", ".")
        _git(folder, "commit", "-qm", "保存候选修改")
        own_commit = _git(folder, "rev-parse", "HEAD").strip()
        tested = await candidate.exec_cmd(shlex.join((sys.executable, "-B", "app/entry.py")))
        assert (tested.returncode, tested.stdout) == (0, "passed\n")
        index = _index(folder)
        patch = await candidate.get_diff()
        assert candidate.diff_base == parent
        assert candidate.own_commits == (own_commit,)
        assert candidate.own_commit_count == 1
        assert _index(folder) == index
        _git(folder, "gc", "--prune=now")
        assert await candidate.get_diff() == patch
        assert _replay(source, patch) == tested.stdout
        assert (source / "data.txt").read_bytes() == updated
    finally:
        await pool.release()
    assert not folder.exists()
    assert not metadata.exists()
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert _git(source, "for-each-ref", "--format=%(refname)", "refs/heads/opencollab-") == ""


async def test_default_pool_retains_bytes_that_plain_patch_replay_converts(tmp_path):
    source = _source(tmp_path, "crlf")
    source_index = _index(source)
    pool = WorktreePool(str(source), use_worktrees=True)
    candidate = await pool.acquire("coder")
    folder = Path(candidate.workspace)
    updated = b"candidate\ncontents\n"
    try:
        (folder / "data.txt").write_bytes(updated)
        (folder / "app/entry.py").write_text(
            "from pathlib import Path\n"
            f"assert Path('data.txt').read_bytes() == {updated!r}\nprint('passed')\n",
        )
        tested = await candidate.exec_cmd(shlex.join((sys.executable, "-B", "app/entry.py")))
        assert (tested.returncode, tested.stdout) == (0, "passed\n")
        index = _index(folder)
        with pytest.raises(RuntimeError, match="physical changes cannot be delivered"):
            await candidate.get_diff()
        assert Path(candidate.recovery_location) == folder
        with pytest.raises(OSError, match="retained"):
            await pool.release()
        assert (folder / "data.txt").read_bytes() == updated
        assert (source / "data.txt").read_bytes() == b"one\ntwo\n"
        assert _index(source) == source_index
        assert _index(folder) == index
    finally:
        updated = updated.replace(b"\n", b"\r\n")
        (folder / "data.txt").write_bytes(updated)
        (folder / "app/entry.py").write_text(
            "from pathlib import Path\n"
            f"assert Path('data.txt').read_bytes() == {updated!r}\nprint('passed')\n",
        )
        patch = await candidate.get_diff()
        assert _replay(source, patch) == "passed\n"
        await pool.release()
    assert not folder.exists()


@pytest.mark.parametrize("converted", [False, True], ids=["plain", "crlf-checkout"])
@pytest.mark.parametrize("line", [b"x\n", b"caf\xe9\n"], ids=["utf8", "legacy"])
async def test_default_pool_copies_large_source_without_capturing_patch_stdout(tmp_path, converted, line):
    source = _source(tmp_path, "crlf" if converted else "clean")
    original = line * (400_000 // len(line))
    (source / "large.txt").write_bytes(original)
    _git(source, "add", "large.txt")
    _git(source, "commit", "-qm", "准备中等大小的源文件")
    assert _git(source, "--no-optional-locks", "status", "--porcelain") == ""
    index = _index(source)
    pool = WorktreePool(str(source), use_worktrees=True)
    try:
        candidate = await pool.acquire("coder")
        folder = Path(candidate.workspace)
        assert (folder / "large.txt").read_bytes() == original
        await candidate.write_file(
            "app/entry.py", "from pathlib import Path\n"
            f"assert Path('large.txt').read_bytes() == {line!r} * {len(original) // len(line)}\n"
            "print('passed')\n",
        )
        tested = await candidate.exec_cmd(shlex.join((sys.executable, "-B", "app/entry.py")))
        assert (tested.returncode, tested.stdout) == (0, "passed\n")
        patch = await candidate.get_diff()
        assert "diff --git a/large.txt" not in patch
        assert len(patch.encode("utf-8")) < 1024
        assert _index(source) == index
        assert (source / "large.txt").read_bytes() == original
        assert _replay(source, patch) == tested.stdout
        assert (source / "large.txt").read_bytes() == original
    finally:
        await pool.release()
    assert not folder.exists()


@pytest.mark.parametrize("hidden", [False, True], ids=["plain", "assume-unchanged"])
async def test_default_pool_exports_app_only_patch_from_large_hidden_source(tmp_path, hidden):
    from tests.runtime.test_worktree_submodule_delivery import _remove_retained

    source = _source(tmp_path, "clean")
    original = b"y\n" * 200_000
    (source / "large.txt").write_bytes(b"x\n" * 200_000 if hidden else original)
    _git(source, "add", "large.txt")
    _git(source, "commit", "-qm", "准备大文件来源对照")
    if hidden:
        _git(source, "update-index", "--assume-unchanged", "large.txt")
        (source / "large.txt").write_bytes(original)
    assert _git(source, "--no-optional-locks", "status", "--porcelain") == ""
    index = _index(source)
    pool = WorktreePool(str(source), use_worktrees=True)
    candidate = await pool.acquire("coder")
    folder = Path(candidate.workspace)
    try:
        assert (folder / "large.txt").read_bytes() == original
        await candidate.write_file(
            "app/entry.py", "from pathlib import Path\n"
            "assert Path('large.txt').read_bytes() == b'y\\n' * 200000\n"
            "print('passed')\n",
        )
        tested = await candidate.exec_cmd(shlex.join((sys.executable, "-B", "app/entry.py")))
        assert (tested.returncode, tested.stdout) == (0, "passed\n")
        candidate_index = _index(folder)
        patch = await candidate.get_diff()
        assert "diff --git a/large.txt" not in patch
        assert len(patch.encode("utf-8")) < 1024
        assert await candidate.get_diff() == patch
        assert _index(source) == index
        assert _index(folder) == candidate_index
        assert (source / "large.txt").read_bytes() == original
        assert _replay(source, patch) == tested.stdout
        assert (source / "large.txt").read_bytes() == original
    finally:
        if candidate.recovery_location is not None:
            _remove_retained(source, candidate)
        else:
            await pool.release()
    assert not folder.exists()
