from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from opencollab.adapters import candidate_workspace, worktree_pool
from opencollab.adapters._env_process import ProcessCleanupError
from opencollab.adapters.env import LocalEnvironment
from opencollab.adapters.worktree_pool import WorktreePool
from opencollab.environments import attach_container
from tests.support.worktree_delivery_support import CONTAINER_ID, git, make_repo
from tests.support.worktree_delivery_support import worktree_transport as worktree_transport


def _registered_count(source):
    if Path("/tmp").resolve() != Path("/tmp"):
        # The local Docker transport resolves macOS's /tmp alias during Git setup.
        git(source, "worktree", "prune")
    return git(source, "worktree", "list", "--porcelain").count("worktree ")


@pytest.fixture(params=["local", "container", "local-adoption", "container-adoption"])
def review_pool(request, tmp_path, monkeypatch, worktree_transport):
    source = make_repo(tmp_path / "source")
    (source / "source.py").write_text("value = 0\n")
    dependency = make_repo(tmp_path / "dependency")
    git(source, "-c", "protocol.file.allow=always", "submodule", "add", str(dependency), "vendor/library")
    git(source, "add", "source.py")
    git(source, "commit", "-qm", "添加源码与依赖")
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    monkeypatch.setattr(worktree_pool, "CONTAINER_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    backend = request.param
    base = (
        attach_container(container_id=CONTAINER_ID, workspace=str(source))
        if backend.startswith("container") else LocalEnvironment(str(source))
    )
    pool = WorktreePool(str(source), use_worktrees=True, base_environment=base,
                        enable_adoption=backend.endswith("adoption"))
    return source, base, pool


async def test_review_snapshot_executes_exact_coder_files_and_keeps_changes_private(review_pool):
    source, base, pool = review_pool
    coder = await pool.acquire("coder")
    await coder.write_file("source.py", "value = 42\n")
    await coder.write_file("verify_candidate.py", "import runpy\nassert runpy.run_path('source.py')['value'] == 42\n")
    original_patch = await coder.get_diff()
    reviewer = None
    try:
        reviewer = await pool.acquire("reviewer", source=coder)
        assert reviewer.workspace != coder.workspace
        assert await reviewer.get_diff() == ""
        assert await reviewer.read_file("source.py") == "value = 42\n"
        assert await reviewer.read_file("vendor/library/initial.txt") == "initial\n"
        checked = await reviewer.exec_cmd("python3 verify_candidate.py")
        assert checked.returncode == 0, checked.stderr
        await reviewer.write_file("review.txt", "reviewer only\n")
        review_patch = await reviewer.get_diff()
        assert "+reviewer only" in review_patch
        assert "source.py" not in review_patch
        assert "verify_candidate.py" not in review_patch
        assert await coder.get_diff() == original_patch
        assert not Path(coder.workspace, "review.txt").exists()
        assert (source / "source.py").read_text() == "value = 0\n"
        assert not (source / "review.txt").exists()
    finally:
        await pool.release()
        await base.cleanup()
    assert reviewer is not None and not Path(reviewer.workspace).exists()
    assert not Path(coder.workspace).exists()
    assert _registered_count(source) == 1


async def test_concurrent_review_sources_are_selected_by_environment(review_pool):
    source, base, pool = review_pool
    first = await pool.acquire("coder")
    second = await pool.acquire("coder")
    try:
        await first.write_file("source.py", "value = 11\n")
        await second.write_file("source.py", "value = 22\n")
        first_patch, second_patch = await asyncio.gather(first.get_diff(), second.get_diff())
        reviews = await asyncio.gather(pool.acquire("reviewer", source=first), pool.acquire("reviewer", source=second))
        assert [await env.read_file("source.py") for env in reviews] == ["value = 11\n", "value = 22\n"]
        assert [await env.get_diff() for env in reviews] == ["", ""]
        assert await first.get_diff() == first_patch
        assert await second.get_diff() == second_patch
    finally:
        await pool.release()
        await base.cleanup()
    assert (source / "source.py").read_text() == "value = 0\n"


async def test_failed_review_setup_releases_only_its_resources(review_pool, monkeypatch):
    source, base, pool = review_pool
    coder = await pool.acquire("coder")
    await coder.write_file("source.py", "value = 42\n")
    original_patch = await coder.get_diff()

    async def fail_environment(*_args, **_kwargs):
        raise OSError("controlled review setup failure")

    monkeypatch.setattr(candidate_workspace.EnvCandidateWorkspace, "_candidate_environment", fail_environment)
    try:
        with pytest.raises(OSError, match="controlled review setup failure"):
            await pool.acquire("reviewer", source=coder)
        assert pool._envs == [coder]
        assert pool._source_dependencies == {}
        assert await coder.get_diff() == original_patch
        assert _registered_count(source) == 2
    finally:
        await pool.release()
        await base.cleanup()


async def test_failed_review_cleanup_retains_its_source_for_retry(review_pool, monkeypatch):
    source, base, pool = review_pool
    coder = await pool.acquire("coder")
    await coder.write_file("source.py", "value = 42\n")
    await coder.get_diff()
    reviewer = await pool.acquire("reviewer", source=coder)
    await reviewer.get_diff()
    cleanup = reviewer.cleanup

    async def fail_cleanup():
        raise OSError("controlled review cleanup failure")

    monkeypatch.setattr(reviewer, "cleanup", fail_cleanup)
    try:
        with pytest.raises(OSError, match="controlled review cleanup failure"):
            await pool.release()
        assert Path(coder.workspace).exists()
        assert Path(reviewer.workspace).exists()
        assert not coder.revoked
        with pytest.raises(RuntimeError, match="before its source"):
            await pool.release_env(coder)
        monkeypatch.setattr(reviewer, "cleanup", cleanup)
        await pool.release()
        assert not Path(coder.workspace).exists()
        assert not Path(reviewer.workspace).exists()
        assert pool._source_dependencies == {}
        assert _registered_count(source) == 1
    finally:
        monkeypatch.setattr(reviewer, "cleanup", cleanup)
        await pool.release()
        await base.cleanup()


async def test_cancelled_review_setup_cleans_snapshot_before_source(review_pool, monkeypatch):
    source, base, pool = review_pool
    coder = await pool.acquire("coder")
    await coder.write_file("source.py", "value = 42\n")
    original_patch = await coder.get_diff()
    entered = asyncio.Event()

    async def wait_initialization(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(candidate_workspace, "_initialize_source_available_submodules", wait_initialization)
    active = asyncio.create_task(pool.acquire("reviewer", source=coder))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(active, 5)
        assert pool._envs == [coder]
        assert pool._source_dependencies == {}
        assert await coder.get_diff() == original_patch
        assert _registered_count(source) == 2
    finally:
        if not active.done():
            active.cancel()
        await asyncio.gather(active, return_exceptions=True)
        await pool.release()
        await base.cleanup()


async def test_revoked_coder_cannot_start_a_review_snapshot(review_pool):
    source, base, pool = review_pool
    coder = await pool.acquire("coder")
    await coder.get_diff()
    coder.revoke()
    try:
        with pytest.raises(RuntimeError, match="review source environment is unavailable"):
            await pool.acquire("reviewer", source=coder)
        assert pool._envs == [coder]
        assert pool._source_dependencies == {}
        assert _registered_count(source) == 2
    finally:
        await pool.release()
        await base.cleanup()


async def test_plain_directory_review_copies_the_completed_coder_state(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "answer.txt").write_text("base\n")
    pool = WorktreePool(str(source), use_worktrees=True)
    coder = await pool.acquire("coder")
    await coder.write_file("answer.txt", "candidate\n")
    original_patch = await coder.get_diff()
    try:
        reviewer = await pool.acquire("reviewer", source=coder)
        assert await reviewer.read_file("answer.txt") == "candidate\n"
        assert await reviewer.get_diff() == ""
        await reviewer.write_file("review.txt", "reviewer only\n")
        assert "+reviewer only" in await reviewer.get_diff()
        assert await coder.get_diff() == original_patch
        assert not Path(coder.workspace, "review.txt").exists()
        assert (source / "answer.txt").read_text() == "base\n"
    finally:
        await pool.release()


@pytest.mark.parametrize("attempts", [1, 3])
@pytest.mark.parametrize("retry", ["pool", "owner"])
async def test_early_source_cleanup_failure_retains_coder_until_native_process_finishes(
    tmp_path, monkeypatch, attempts, retry,
):
    source = make_repo(tmp_path / "source")
    (source / "answer.py").write_text("answer = 0\n")
    git(source, "add", "answer.py")
    git(source, "commit", "-qm", "建立来源故障对照")
    pool = WorktreePool(str(source), use_worktrees=True)
    coder = await pool.acquire("coder")
    await coder.write_file("answer.py", "answer = 42\n")
    await coder.get_diff()
    process = None
    owner = None
    release = tmp_path / "release"

    class BorrowedView(LocalEnvironment):
        async def exec_cmd(self, command, timeout=120):
            nonlocal process, owner
            result = await super().exec_cmd(command, timeout)
            if process is None:
                owner = self
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-c",
                    "import pathlib,sys,time\n"
                    "while not pathlib.Path(sys.argv[2]).exists(): time.sleep(0.01)\n"
                    "print(pathlib.Path(sys.argv[1]).read_text(), end='')\n",
                    str(Path(coder.workspace) / "answer.py"), str(release),
                    cwd=coder.workspace, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                raise ProcessCleanupError("controlled source command did not quiesce")
            return result

        async def cleanup(self):
            if process is not None and process.returncode is None:
                raise ProcessCleanupError("controlled source command remains active")
            await super().cleanup()

    monkeypatch.setattr(worktree_pool, "LocalEnvironment", BorrowedView)
    try:
        with pytest.raises(ProcessCleanupError, match="did not quiesce"):
            await pool.acquire("reviewer", source=coder)
        assert process is not None and owner is not None
        assert pool._envs == [coder, owner]
        assert pool._source_dependencies == {owner: coder}
        for _ in range(attempts):
            if retry == "pool":
                with pytest.raises(OSError, match="controlled source command remains active"):
                    await pool.release()
            else:
                with pytest.raises(ProcessCleanupError, match="remains active"):
                    await pool.release_env(owner)
            assert Path(coder.workspace).is_dir()
            assert pool._source_dependencies == {owner: coder}
            assert pool._envs == [coder, owner]
        release.touch()
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode == 0, stderr.decode()
        assert stdout == b"answer = 42\n"
        if retry == "owner":
            await pool.release_env(owner)
            assert pool._envs == [coder]
            assert pool._source_dependencies == {}
            assert Path(coder.workspace).is_dir()
        await pool.release()
        assert pool._envs == []
        assert pool._source_dependencies == {}
        assert not Path(coder.workspace).exists()
        assert _registered_count(source) == 1
        print(json.dumps({
            "attempts": attempts, "retry": retry, "native_returncode": process.returncode,
            "native_stdout": stdout.decode(), "native_stderr": stderr.decode(),
            "remaining_environments": len(pool._envs),
            "remaining_dependencies": len(pool._source_dependencies),
        }))
    finally:
        release.touch()
        if process is not None:
            await process.communicate()
        await pool.release()
