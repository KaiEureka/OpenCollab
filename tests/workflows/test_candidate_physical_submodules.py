from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from opencollab.adapters import _candidate_physical, candidate_workspace
from opencollab.adapters._env_local import LocalEnvironment
from opencollab.adapters.candidate_workspace import EnvCandidateWorkspace
from opencollab.adapters.worktree_pool import WorktreePool
from opencollab.application.workflow import WorkflowContext
from opencollab.application.workflow_candidates import CandidateCaptureError, CandidateWorkspaceTrackingError
from opencollab.environments import attach_container
from tests.support.worktree_delivery_support import CONTAINER_ID
from tests.support.worktree_delivery_support import worktree_transport as worktree_transport
from tests.workflows.test_candidate_workspace_submodules import _dependency
from tests.workflows.test_workflow_candidate_workspace import _git, _repository

_SCRIPT = b'#!/bin/sh\nprintf "42\\n"\n'


def _source(tmp_path, nested=False, converted=True):
    dependency = _dependency(tmp_path, "leaf")
    (dependency / "helper.sh").write_bytes(_SCRIPT)
    (dependency / "helper.sh").chmod(0o755)
    if converted:
        (dependency / ".gitattributes").write_text("*.sh text eol=lf\n")
    _git(dependency, "add", ".")
    _git(dependency, "commit", "-qm", "Prepare module script")
    module_path = "vendor/dependency"
    if nested:
        middle = _dependency(tmp_path, "middle")
        _git(middle, "-c", "protocol.file.allow=always", "submodule", "add", str(dependency), "vendor/leaf")
        _git(middle, "commit", "-am", "Prepare nested module")
        dependency = middle
        module_path += "/vendor/leaf"
    source = _repository(tmp_path)
    _git(source, "-c", "protocol.file.allow=always", "submodule", "add", str(dependency), "vendor/dependency")
    if nested:
        _git(source, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive")
    _git(source, "commit", "-am", "Prepare source module")
    return source, module_path


def _index(repository):
    return Path(_git(repository, "rev-parse", "--absolute-git-dir").removesuffix("\n"), "index").read_bytes()


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("mutation", ["stable", "dirty", "staged-physical", "untracked"])
async def test_source_submodule_physical_changes_keep_the_tested_candidate(tmp_path, nested, mutation):
    source, module_path = _source(tmp_path, nested)
    module = source / module_path
    source_index = _index(source)
    module_index = _index(module)
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
    application = f'import subprocess\nsubprocess.run(["./{module_path}/helper.sh"], check=True)\n'

    async def edit(child, _args):
        nonlocal module_index
        candidate = Path(child.workspace_root)
        (candidate / "app.py").write_text(application)
        tested = subprocess.run([sys.executable, "-B", "app.py"], cwd=candidate, capture_output=True, check=True)
        assert tested.stdout == b"42\n"
        if mutation in {"dirty", "staged-physical"}:
            (module / "helper.sh").write_bytes(_SCRIPT.replace(b"\n", b"\r\n"))
            if mutation == "staged-physical":
                _git(module, "add", "helper.sh")
                assert _git(source, "status", "--porcelain") == ""
                module_index = _index(module)
        elif mutation == "untracked":
            (module / "new.py").write_text("VALUE = 7\n")
        return "tested"

    try:
        revision = await workspace.physical_source_revision()
        assert revision is not None
        if mutation == "stable":
            candidate = await context.candidate_workflow(edit, {}, label="physical-module")
            await context.adopt_candidate(candidate)
            final = subprocess.run([sys.executable, "-B", "app.py"], cwd=source, capture_output=True, check=True)
            assert final.stdout == b"42\n"
            assert await workspace.physical_source_revision() == revision
        else:
            with pytest.raises(CandidateWorkspaceTrackingError, match="worktree preserved"):
                await context.candidate_workflow(edit, {}, label="physical-module")
            assert Path(leases[0].candidate_workspace, "app.py").read_text() == application
            assert Path(leases[0].candidate_workspace, module_path, "helper.sh").read_bytes() == _SCRIPT
            assert not (source / "app.py").exists()
            assert await workspace.physical_source_revision() != revision
        assert _index(source) == source_index
        assert _index(module) == module_index
    finally:
        for lease in leases:
            await lease.cleanup()
        await environment.cleanup()
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("backend", ["workflow", "team"])
async def test_initial_git_clean_submodule_bytes_must_match_the_candidate(tmp_path, nested, backend):
    source, module_path = _source(tmp_path, nested)
    module = source / module_path
    physical = _SCRIPT.replace(b"\n", b"\r\n")
    (module / "helper.sh").write_bytes(physical)
    _git(module, "add", "helper.sh")
    assert _git(source, "status", "--porcelain") == ""
    source_index, module_index = _index(source), _index(module)
    environment = LocalEnvironment(str(source))
    pool = WorktreePool(str(source), use_worktrees=True, base_environment=environment, enable_adoption=True)
    try:
        with pytest.raises(RuntimeError, match="source submodule physical contents cannot be reproduced"):
            if backend == "workflow":
                await EnvCandidateWorkspace(environment).acquire("initial-module")
            else:
                await pool.acquire("coder")
        assert (module / "helper.sh").read_bytes() == physical
        assert _index(source) == source_index
        assert _index(module) == module_index
    finally:
        await pool.release()
        await environment.cleanup()
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.parametrize("backend", ["workflow", "team"])
async def test_candidate_hidden_submodule_edits_are_retained_before_delivery(tmp_path, backend):
    source, module_path = _source(tmp_path)
    environment = LocalEnvironment(str(source))
    source_index, module_index = _index(source), _index(source / module_path)
    pool = WorktreePool(str(source), use_worktrees=True, base_environment=environment, enable_adoption=True)
    lease = None
    candidate = None
    try:
        if backend == "workflow":
            lease = await EnvCandidateWorkspace(environment).acquire("candidate-module")
            candidate = lease.environment
        else:
            candidate = await pool.acquire("coder")
        module = Path(candidate.workspace, module_path)
        (module / "helper.sh").write_bytes(_SCRIPT.replace(b"\n", b"\r\n"))
        _git(module, "add", "helper.sh")
        assert _git(module, "status", "--porcelain") == ""
        with pytest.raises(RuntimeError, match="submodule physical changes"):
            await (lease.capture_changes() if lease is not None else candidate.capture_changes())
        assert (module / "helper.sh").read_bytes() == _SCRIPT.replace(b"\n", b"\r\n")
        assert (source / module_path / "helper.sh").read_bytes() == _SCRIPT
        assert _index(source) == source_index
        assert _index(source / module_path) == module_index
        if backend == "team":
            with pytest.raises(OSError, match="retained"):
                await pool.release()
            assert Path(candidate.workspace).exists()
            (module / "helper.sh").write_bytes(_SCRIPT)
            _git(module, "add", "helper.sh")
            await candidate.capture_changes()
    finally:
        if lease is not None:
            await lease.cleanup()
        await pool.release()
        await environment.cleanup()


async def test_workflow_keeps_a_hidden_physical_module_edit_when_capture_fails(tmp_path):
    source, module_path = _source(tmp_path)
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

    async def edit(child, _args):
        folder = Path(child.workspace_root)
        (folder / "app.py").write_text("print(42)\n")
        module = folder / module_path
        (module / "helper.sh").write_bytes(_SCRIPT.replace(b"\n", b"\r\n"))
        _git(module, "add", "helper.sh")
        return "changed module"

    try:
        with pytest.raises(CandidateCaptureError, match="worktree preserved"):
            await context.candidate_workflow(edit, {}, label="candidate-module")
        assert Path(leases[0].candidate_workspace, "app.py").read_text() == "print(42)\n"
        assert Path(leases[0].candidate_workspace, module_path, "helper.sh").read_bytes() == (
            _SCRIPT.replace(b"\n", b"\r\n")
        )
        assert not (source / "app.py").exists()
        assert (source / module_path / "helper.sh").read_bytes() == _SCRIPT
    finally:
        for lease in leases:
            await lease.cleanup()
        await environment.cleanup()


async def test_plain_submodules_keep_the_physical_capture_fast_path(tmp_path, monkeypatch):
    source, _module_path = _source(tmp_path, nested=True, converted=False)
    environment = LocalEnvironment(str(source))
    workspace = EnvCandidateWorkspace(environment)

    async def unexpected_scan(*args, **kwargs):
        raise AssertionError("plain submodule scanned a physical tree")

    monkeypatch.setattr(_candidate_physical, "_physical_tree", unexpected_scan)
    monkeypatch.setattr(candidate_workspace, "_physical_tree", unexpected_scan)
    lease = None
    try:
        assert await workspace.physical_source_revision() is None
        lease = await workspace.acquire("plain-module")
        assert not await lease.physical_source_changed()
        assert await lease.capture_changes() == ""
    finally:
        if lease is not None:
            await lease.cleanup()
        await environment.cleanup()


@pytest.mark.parametrize("backend", ["workflow", "team"])
@pytest.mark.parametrize("initial_mismatch", [False, True])
async def test_nested_physical_submodules_keep_container_path_identity(
    worktree_transport, tmp_path, backend, initial_mismatch,
):
    source, module_path = _source(tmp_path, nested=True)
    module = source / module_path
    if initial_mismatch:
        (module / "helper.sh").write_bytes(_SCRIPT.replace(b"\n", b"\r\n"))
        _git(module, "add", "helper.sh")
        assert _git(source, "status", "--porcelain") == ""
    source_index, module_index = _index(source), _index(module)
    environment = attach_container(container_id=CONTAINER_ID, workspace=str(source))
    pool = WorktreePool(str(source), use_worktrees=True, base_environment=environment, enable_adoption=True)
    lease = None

    async def acquire():
        nonlocal lease
        if backend == "workflow":
            lease = await EnvCandidateWorkspace(environment).acquire("container-module")
            return lease.environment
        candidate = await pool.acquire("coder")
        lease = candidate._lease
        return candidate

    try:
        if initial_mismatch:
            with pytest.raises(RuntimeError, match="source submodule physical contents cannot be reproduced"):
                await acquire()
        else:
            candidate = await acquire()
            assert Path(candidate.workspace, module_path, "helper.sh").read_bytes() == _SCRIPT
            assert not await lease.physical_source_changed()
            executed = await candidate.exec_cmd(f"./{module_path}/helper.sh")
            assert (executed.returncode, executed.stdout) == (0, "42\n")
            assert await (lease.capture_changes() if backend == "workflow" else candidate.capture_changes()) == ""
        assert _index(source) == source_index
        assert _index(module) == module_index
    finally:
        if backend == "workflow" and lease is not None:
            await lease.cleanup()
        await pool.release()
        await environment.cleanup()
        if Path("/tmp").resolve() != Path("/tmp"):
            _git(source, "worktree", "prune")
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1
