from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from opencollab.adapters._env_local import LocalEnvironment
from opencollab.adapters.candidate_workspace import EnvCandidateWorkspace
from opencollab.adapters.worktree_pool import WorktreePool
from opencollab.application.workflow import WorkflowContext
from opencollab.application.workflow_candidates import CandidateWorkspaceTrackingError
from tests.workflows.test_workflow_candidate_workspace import _git, _repository


def _source(tmp_path, converted, dirty):
    source = _repository(tmp_path)
    _git(source, "config", "core.autocrlf", str(converted).lower())
    (source / "data.txt").write_bytes(b"original\r\n")
    (source / "notes.txt").write_bytes(b"original note\r\n")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "Prepare existing CRLF files")
    if dirty:
        (source / "notes.txt").write_bytes(b"staged note\r\n")
        _git(source, "add", "notes.txt")
        (source / "notes.txt").write_bytes(b"user note\r\n")
        (source / "user.txt").write_bytes(b"untracked user file\r\n")
    return source


@pytest.mark.parametrize("backend", ["workflow", "team"])
@pytest.mark.parametrize("initial", [False, True])
@pytest.mark.parametrize("final", [False, True])
@pytest.mark.parametrize("dirty", [False, True])
async def test_source_normalization_settings_preserve_existing_crlf_files(tmp_path, backend, initial, final, dirty):
    source = _source(tmp_path, initial, dirty)
    index = (source / ".git/index").read_bytes()
    contents = {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}
    environment = LocalEnvironment(str(source))
    workspace = EnvCandidateWorkspace(environment)
    context = WorkflowContext(object(), candidate_workspace=workspace)
    pool = WorktreePool(str(source), use_worktrees=True, base_environment=environment, enable_adoption=True)
    leases = []
    acquire = workspace.acquire

    async def remember(label):
        lease = await acquire(label)
        leases.append(lease)
        return lease

    workspace.acquire = remember
    candidate = None

    async def edit(candidate_environment):
        for name, value in contents.items():
            assert Path(candidate_environment.workspace, name).read_bytes() == value
        await candidate_environment.write_file("app.py", "print(42)\n")
        tested = await candidate_environment.exec_cmd(shlex.join((sys.executable, "-B", "app.py")))
        assert (tested.returncode, tested.stdout) == (0, "42\n")
        _git(Path(candidate_environment.workspace), "config", "core.autocrlf", str(final).lower())

    try:
        if backend == "workflow":
            async def flow(child, arguments):
                await edit(leases[-1].environment)
                return "tested"

            selected = await context.candidate_workflow(flow, {}, label="crlf-settings")
            await context.adopt_candidate(selected)
        else:
            candidate = await pool.acquire("coder")
            await edit(candidate)
            assert not await candidate._lease.physical_source_changed()
            patch = await candidate.capture_changes()
            await pool.adopt_captured(candidate.delivery_diff or patch, candidate.delivery_paths)
        tested = await environment.exec_cmd(shlex.join((sys.executable, "-B", "app.py")))
        assert (tested.returncode, tested.stdout) == (0, "42\n")
        for name, value in contents.items():
            assert (source / name).read_bytes() == value
        assert (source / ".git/index").read_bytes() == index
    finally:
        for lease in leases:
            await lease.cleanup()
        if candidate is not None:
            await candidate.capture_changes()
        await pool.release()
        await environment.cleanup()
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.parametrize("mutation", ["bytes", "staged", "unstaged", "untracked-add", "untracked-remove", "head"])
async def test_normalization_changes_keep_real_source_changes_visible(tmp_path, mutation):
    source = _source(tmp_path, True, True)
    environment = LocalEnvironment(str(source))
    workspace = EnvCandidateWorkspace(environment)
    context = WorkflowContext(object(), candidate_workspace=workspace)
    leases = []
    acquire = workspace.acquire

    async def remember(label):
        lease = await acquire(label)
        leases.append(lease)
        return lease

    workspace.acquire = remember

    async def flow(child, arguments):
        await leases[-1].environment.write_file("app.py", "print(42)\n")
        _git(source, "config", "core.autocrlf", "false")
        if mutation == "bytes":
            (source / "data.txt").write_bytes(b"changed data\r\n")
        elif mutation == "staged":
            _git(source, "add", "notes.txt")
        elif mutation == "unstaged":
            _git(source, "reset", "--", "notes.txt")
        elif mutation == "untracked-add":
            (source / "later.txt").write_bytes(b"later user file\r\n")
        elif mutation == "untracked-remove":
            (source / "user.txt").unlink()
        else:
            _git(source, "commit", "--allow-empty", "-qm", "Move source HEAD")
        return "tested"

    try:
        with pytest.raises(CandidateWorkspaceTrackingError, match="worktree preserved"):
            await context.candidate_workflow(flow, {}, label="changed-source")
        assert Path(leases[-1].candidate_workspace, "app.py").read_text() == "print(42)\n"
        assert not (source / "app.py").exists()
    finally:
        for lease in leases:
            await lease.cleanup()
        await environment.cleanup()


async def test_source_index_identity_keeps_split_index_dependencies(tmp_path):
    from tests.workflows.test_workflow_candidate_workspace import _Factory

    source = _source(tmp_path, True, True)
    _git(source, "config", "core.splitIndex", "true")
    _git(source, "update-index", "--split-index")
    assert list((source / ".git").glob("sharedindex.*"))
    index = (source / ".git/index").read_bytes()
    environment = LocalEnvironment(str(source))
    workspace = EnvCandidateWorkspace(environment)
    context = WorkflowContext(_Factory(environment), candidate_workspace=workspace)
    try:
        candidate = await context.candidate_agent("edit source", label="split-index")
        await context.adopt_candidate(candidate)
        assert (source / "source.py").read_text() == "value = 2\n"
        assert (source / ".git/index").read_bytes() == index
    finally:
        await environment.cleanup()
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.parametrize("resolve_source_index", [False, True])
async def test_candidate_can_repair_merge_conflicts_without_losing_unmerged_stages(tmp_path, resolve_source_index):
    import subprocess

    source = _source(tmp_path, False, False)
    original = _git(source, "rev-parse", "HEAD").strip()
    _git(source, "checkout", "-qb", "other")
    (source / "source.py").write_text("print(2)\n")
    _git(source, "commit", "-qam", "Change another branch")
    _git(source, "checkout", "-q", "--detach", original)
    (source / "source.py").write_text("print(3)\n")
    _git(source, "commit", "-qam", "Change current branch")
    merged = subprocess.run(["git", "-C", str(source), "merge", "other"], capture_output=True)
    assert merged.returncode == 1
    assert len(_git(source, "ls-files", "--unmerged").splitlines()) == 3
    index = (source / ".git/index").read_bytes()
    contents = (source / "source.py").read_bytes()
    environment = LocalEnvironment(str(source))
    workspace = EnvCandidateWorkspace(environment)
    context = WorkflowContext(object(), candidate_workspace=workspace)
    leases = []
    acquire = workspace.acquire
    executions = []

    async def remember(label):
        lease = await acquire(label)
        leases.append(lease)
        return lease

    workspace.acquire = remember

    async def flow(child, arguments):
        candidate = leases[-1].environment
        await candidate.write_file("source.py", "print(42)\n")
        tested = await candidate.exec_cmd(shlex.join((sys.executable, "-B", "source.py")))
        executions.append((tested.returncode, tested.stdout))
        if resolve_source_index:
            _git(source, "add", "source.py")
        return "repaired conflict"

    try:
        if resolve_source_index:
            with pytest.raises(CandidateWorkspaceTrackingError, match="worktree preserved"):
                await context.candidate_workflow(flow, {}, label="repair-conflict")
            assert (source / "source.py").read_bytes() == contents
            assert not _git(source, "ls-files", "--unmerged")
        else:
            selected = await context.candidate_workflow(flow, {}, label="repair-conflict")
            await context.adopt_candidate(selected)
            tested = await environment.exec_cmd(shlex.join((sys.executable, "-B", "source.py")))
            assert (tested.returncode, tested.stdout) == (0, "42\n")
            assert (source / ".git/index").read_bytes() == index
            assert len(_git(source, "ls-files", "--unmerged").splitlines()) == 3
        assert executions == [(0, "42\n")]
    finally:
        for lease in leases:
            await lease.cleanup()
        await environment.cleanup()
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1


async def test_source_index_identity_streams_entries_beyond_process_capture(tmp_path):
    source = _source(tmp_path, False, True)
    for number in range(6_500):
        (source / f"module{number:05d}_{'x' * 100}.py").touch()
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "Prepare index beyond process capture")
    environment = LocalEnvironment(str(source))
    workspace = EnvCandidateWorkspace(environment)
    try:
        truncated = await environment.exec_cmd("git ls-files --stage -z")
        assert truncated.stdout_truncated
        index = (source / ".git/index").read_bytes()
        original = await workspace.source_index_revision()
        assert await workspace.source_index_revision() == original
        assert (source / ".git/index").read_bytes() == index
        _git(source, "update-index", "--chmod=+x", "source.py")
        changed_index = (source / ".git/index").read_bytes()
        assert await workspace.source_index_revision() != original
        assert (source / ".git/index").read_bytes() == changed_index
    finally:
        await environment.cleanup()
