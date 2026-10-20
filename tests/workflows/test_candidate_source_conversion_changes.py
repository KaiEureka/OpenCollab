from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from opencollab.adapters import candidate_workspace
from opencollab.adapters._env_local import LocalEnvironment
from opencollab.adapters.candidate_workspace import EnvCandidateWorkspace
from opencollab.adapters.worktree_pool import WorktreePool
from opencollab.application.workflow import WorkflowContext
from opencollab.application.workflow_candidates import CandidateWorkspaceTrackingError
from tests.workflows.test_workflow_candidate_workspace import _git, _repository

_HELPER = b'#!/bin/sh\nprintf "42\\n"\n'
_APPLICATION = 'import subprocess\nsubprocess.run(["./helper.sh"], check=True)\n'


def _source(tmp_path, *, dirty=True):
    repo = _repository(tmp_path)
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "helper.sh").write_bytes(_HELPER)
    (repo / "helper.sh").chmod(0o755)
    (repo / "notes.txt").write_text("original\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "Prepare source conversion changes")
    if dirty:
        (repo / "notes.txt").write_text("staged\n")
        _git(repo, "add", "notes.txt")
        (repo / "notes.txt").write_text("source user edit\n")
        (repo / "user.py").write_text("VALUE = 7\n")
    return repo


async def test_new_source_conversion_retains_the_tested_workflow(tmp_path, monkeypatch):
    repo = _source(tmp_path)
    index = (repo / ".git/index").read_bytes()
    environment = LocalEnvironment(str(repo))
    workspace = EnvCandidateWorkspace(environment)
    context = WorkflowContext(object(), candidate_workspace=workspace)
    source_before = await workspace.source_diff()
    leases = []
    checked = []
    acquire = workspace.acquire

    async def remember(label):
        lease = await acquire(label)
        leases.append(lease)
        return lease

    monkeypatch.setattr(workspace, "acquire", remember)

    async def edit(child, _arguments):
        candidate = LocalEnvironment(child.workspace_root)
        try:
            await candidate.write_file("app.py", _APPLICATION)
            tested = await candidate.exec_cmd(f"{shlex.quote(sys.executable)} -B app.py")
            checked.append((tested.returncode, tested.stdout))
            assert checked[-1] == (0, "42\n")
        finally:
            await candidate.cleanup()
        _git(repo, "config", "core.autocrlf", "true")
        (repo / "helper.sh").unlink()
        _git(repo, "checkout-index", "--force", "--", "helper.sh")
        assert (repo / "helper.sh").read_bytes() == _HELPER.replace(b"\n", b"\r\n")
        return "tested"

    try:
        with pytest.raises(CandidateWorkspaceTrackingError, match="worktree preserved"):
            await context.candidate_workflow(edit, {}, label="new-source-conversion")
        assert checked == [(0, "42\n")]
        assert Path(leases[0].candidate_workspace, "helper.sh").read_bytes() == _HELPER
        assert Path(leases[0].candidate_workspace, "app.py").read_text() == _APPLICATION
        assert await workspace.source_diff() == source_before
        assert not (repo / "app.py").exists()
        assert (repo / ".git/index").read_bytes() == index
    finally:
        for lease in leases:
            await lease.cleanup()
        await environment.cleanup()


@pytest.mark.parametrize("backend", ["workflow", "team"])
@pytest.mark.parametrize("initial_conversion", [False, True])
@pytest.mark.parametrize("final_conversion", [False, True])
@pytest.mark.parametrize("mutate", [False, True])
@pytest.mark.parametrize("dirty_source", [False, True])
async def test_source_conversion_settings_track_only_physical_changes(
    tmp_path, backend, initial_conversion, final_conversion, mutate, dirty_source,
):
    repo = _source(tmp_path, dirty=dirty_source)
    _git(repo, "config", "core.autocrlf", str(initial_conversion).lower())
    index = (repo / ".git/index").read_bytes()
    environment = LocalEnvironment(str(repo))
    workspace = EnvCandidateWorkspace(environment)
    source_before = await workspace.source_diff()
    pool = WorktreePool(str(repo), use_worktrees=True, base_environment=environment, enable_adoption=True)
    lease = None
    try:
        if backend == "team":
            candidate = await pool.acquire("coder")
            lease = candidate._lease
        else:
            lease = await workspace.acquire("conversion-settings")
            candidate = lease.environment
        assert Path(candidate.workspace, "helper.sh").read_bytes() == _HELPER
        assert await candidate.read_file("notes.txt") == ("source user edit\n" if dirty_source else "original\n")
        if dirty_source:
            assert await candidate.read_file("user.py") == "VALUE = 7\n"
        await candidate.write_file("app.py", _APPLICATION)
        tested = await candidate.exec_cmd(f"{shlex.quote(sys.executable)} -B app.py")
        assert (tested.returncode, tested.stdout) == (0, "42\n")
        _git(repo, "config", "core.autocrlf", str(final_conversion).lower())
        if mutate:
            if final_conversion:
                (repo / "helper.sh").unlink()
                _git(repo, "checkout-index", "--force", "--", "helper.sh")
            else:
                (repo / "helper.sh").write_bytes(_HELPER.replace(b"\n", b"\r\n"))
        source_changed = await workspace.source_diff() != source_before or await lease.physical_source_changed()
        assert source_changed is mutate
        assert (repo / ".git/index").read_bytes() == index
        assert Path(candidate.workspace, "helper.sh").read_bytes() == _HELPER
        if not mutate:
            patch = await candidate.capture_changes() if backend == "team" else await lease.capture_changes()
            if backend == "team":
                await pool.adopt_captured(candidate.delivery_diff or patch, candidate.delivery_paths)
            else:
                await workspace.adopt(patch)
            delivered = await environment.exec_cmd(f"{shlex.quote(sys.executable)} -B app.py")
            assert (delivered.returncode, delivered.stdout) == (0, "42\n")
    finally:
        if backend == "workflow" and lease is not None:
            await lease.cleanup()
        await pool.release()
        await environment.cleanup()


@pytest.mark.parametrize("backend", ["workflow", "team"])
async def test_plain_source_keeps_the_physical_scan_fast_path_when_candidate_adds_conversions(
    tmp_path, monkeypatch, backend,
):
    repo = _source(tmp_path)
    environment = LocalEnvironment(str(repo))
    workspace = EnvCandidateWorkspace(environment)
    pool = WorktreePool(str(repo), use_worktrees=True, base_environment=environment, enable_adoption=True)
    lease = None
    try:
        if backend == "team":
            candidate = await pool.acquire("coder")
            lease = candidate._lease
        else:
            lease = await workspace.acquire("candidate-conversions")
            candidate = lease.environment
        await candidate.write_file(".gitattributes", "*.sh text eol=crlf\n")
        await candidate.write_file("app.py", _APPLICATION)
        source_scan = candidate_workspace._physical_tree

        async def reject_source_scan(environment, folder, *args, **kwargs):
            assert folder != str(repo), "plain source scanned a physical tree"
            return await source_scan(environment, folder, *args, **kwargs)

        monkeypatch.setattr(candidate_workspace, "_physical_tree", reject_source_scan)
        assert not await lease.physical_source_changed()
        assert not (repo / ".gitattributes").exists()
        patch = await candidate.capture_changes() if backend == "team" else await lease.capture_changes()
        if backend == "team":
            await pool.adopt_captured(candidate.delivery_diff or patch, candidate.delivery_paths)
        else:
            await workspace.adopt(patch)
        delivered = await environment.exec_cmd(f"{shlex.quote(sys.executable)} -B app.py")
        assert (delivered.returncode, delivered.stdout) == (0, "42\n")
    finally:
        if backend == "workflow" and lease is not None:
            await lease.cleanup()
        await pool.release()
        await environment.cleanup()
