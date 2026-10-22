from __future__ import annotations

import asyncio
import shlex
import sys
from pathlib import Path

import pytest

from opencollab.adapters import candidate_workspace
from opencollab.adapters._env_local import LocalEnvironment
from opencollab.adapters.candidate_workspace import EnvCandidateWorkspace
from opencollab.application.workflow import WorkflowContext
from opencollab.application.workflow_candidates import CandidateWorkspaceTrackingError
from tests.workflows.test_workflow_candidate_workspace import (
    _EditingSession,
    _Factory,
    _git,
    _repository,
)

_HELPER = b'#!/bin/sh\nprintf "42\\n"\n'
_APPLICATION = 'import subprocess\nsubprocess.run(["./helper.sh"], check=True)\n'


@pytest.mark.parametrize("mode", ["agent", "workflow"])
@pytest.mark.parametrize("mutate", [False, True])
async def test_candidate_tracks_physical_source_changes_hidden_by_git(tmp_path, monkeypatch, mode, mutate):
    repo = _repository(tmp_path)
    (repo / ".gitattributes").write_text("*.sh text eol=crlf\n")
    (repo / ".gitignore").write_text("__pycache__/\n")
    (repo / "helper.sh").write_bytes(_HELPER)
    (repo / "helper.sh").chmod(0o755)
    (repo / "app.py").write_text("print(1)\n")
    (repo / "notes.txt").write_text("original\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "Prepare physical source tracking")
    (repo / "notes.txt").write_text("staged\n")
    _git(repo, "add", "notes.txt")
    (repo / "notes.txt").write_text("source user edit\n")
    (repo / "user.py").write_text("VALUE = 7\n")
    index = (repo / ".git/index").read_bytes()
    environment = LocalEnvironment(str(repo))
    workspace = EnvCandidateWorkspace(environment)
    leases = []
    checked = []
    acquire = workspace.acquire

    async def remember(label):
        lease = await acquire(label)
        leases.append(lease)
        return lease

    class CheckedSession(_EditingSession):
        async def run_loop(self, _cancel_event=None):
            assert await self.environment.read_file("notes.txt") == "source user edit\n"
            assert await self.environment.read_file("user.py") == "VALUE = 7\n"
            await self.environment.write_file("app.py", _APPLICATION)
            result = await self.environment.exec_cmd(f"{shlex.quote(sys.executable)} -B app.py")
            checked.append((result.returncode, result.stdout))
            assert checked[-1] == (0, "42\n")
            if mutate:
                (repo / "helper.sh").write_bytes(_HELPER.replace(b"\n", b"\r\n"))
            return "tested"

    class CheckedFactory(_Factory):
        def build_workflow_session(self, **kwargs):
            return CheckedSession(kwargs["env"])

    monkeypatch.setattr(workspace, "acquire", remember)
    context = WorkflowContext(CheckedFactory(environment), candidate_workspace=workspace)
    source_before = await workspace.source_diff()

    async def nested(child, _args):
        return await child.agent("edit and check")

    async def generate():
        if mode == "agent":
            return await context.candidate_agent("edit and check", label="physical-source")
        return await context.candidate_workflow(nested, {}, label="physical-source")

    try:
        if mutate:
            with pytest.raises(CandidateWorkspaceTrackingError, match="worktree preserved"):
                await generate()
            assert Path(leases[0].candidate_workspace).exists()
            assert Path(leases[0].candidate_workspace, "helper.sh").read_bytes() == _HELPER
            assert (repo / "helper.sh").read_bytes() == _HELPER.replace(b"\n", b"\r\n")
            assert (repo / "app.py").read_text() == "print(1)\n"
        else:
            candidate = await generate()
            (repo / "notes.txt").write_text("later user edit\n")
            await context.adopt_candidate(candidate)
            final = await environment.exec_cmd(f"{shlex.quote(sys.executable)} -B app.py")
            assert (final.returncode, final.stdout) == (0, "42\n")
            assert (repo / "notes.txt").read_text() == "later user edit\n"
        if mutate:
            assert await workspace.source_diff() == source_before
        assert checked == [(0, "42\n")]
        assert (repo / ".git/index").read_bytes() == index
    finally:
        for lease in leases:
            await lease.cleanup()
        await environment.cleanup()


async def test_physical_source_tracking_keeps_owned_outputs_and_workspace_exclusions(tmp_path):
    repo = _repository(tmp_path)
    project = repo / "project"
    project.mkdir()
    outputs = repo / "records"
    outputs.mkdir()
    (outputs / "tracked.txt").write_text("tracked output\n")
    (repo / ".gitattributes").write_text("*.sh text eol=crlf\n")
    (repo / ".gitignore").write_text("cache/\n")
    (project / "helper.sh").write_bytes(_HELPER)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "Prepare source exclusion tracking")
    (project / "user.py").write_text("VALUE = 7\n")
    (outputs / "current.txt").write_text("owned output\n")
    (repo / "cache").mkdir()
    (repo / "cache/local.txt").write_text("local cache\n")
    index = (repo / ".git/index").read_bytes()
    environment = LocalEnvironment(str(project))
    workspace = EnvCandidateWorkspace(environment, owned_save_dir=str(outputs))
    lease = await workspace.acquire("physical-exclusions")
    try:
        assert not await lease.physical_source_changed()
        (outputs / "current.txt").write_text("new output\n")
        (repo / "cache/local.txt").write_text("new cache\n")
        assert not await lease.physical_source_changed()
        (outputs / "tracked.txt").write_text("changed tracked output\n")
        assert await lease.physical_source_changed()
        (outputs / "tracked.txt").write_text("tracked output\n")
        (project / "helper.sh").write_bytes(_HELPER.replace(b"\n", b"\r\n"))
        assert await lease.physical_source_changed()
        assert not await lease.physical_source_changed(exclude_paths=("helper.sh",))
        (project / "helper.sh").write_bytes(_HELPER)
        (project / "user.py").unlink()
        assert await lease.physical_source_changed()
        before = await workspace.physical_source_revision(exclude_paths=("project/evidence",))
        evidence = project / "evidence"
        evidence.mkdir()
        (evidence / "candidate.patch").write_text("retained evidence\n")
        assert await workspace.physical_source_revision(exclude_paths=("project/evidence",)) == before
        assert await workspace.physical_source_revision() != before
        assert (repo / ".git/index").read_bytes() == index
        _git(repo, "add", "project/evidence/candidate.patch")
        staged_index = (repo / ".git/index").read_bytes()
        assert await workspace.physical_source_revision(exclude_paths=("project/evidence",)) == before
        assert await workspace.physical_source_revision() != before
        assert (repo / ".git/index").read_bytes() == staged_index
    finally:
        await lease.cleanup()
        await environment.cleanup()


@pytest.mark.parametrize("mode", ["agent", "workflow"])
async def test_plain_candidates_keep_physical_source_scan_fast_path(tmp_path, monkeypatch, mode):
    repo = _repository(tmp_path)
    environment = LocalEnvironment(str(repo))
    workspace = EnvCandidateWorkspace(environment)
    context = WorkflowContext(_Factory(environment), candidate_workspace=workspace)

    async def unexpected_scan(*args, **kwargs):
        raise AssertionError("plain candidate scanned a physical tree")

    monkeypatch.setattr(candidate_workspace, "_physical_tree", unexpected_scan)

    async def nested(child, _args):
        return await child.agent("edit")

    try:
        assert await workspace.physical_source_revision() is None
        if mode == "agent":
            selected = await context.candidate_agent("edit", label="plain")
        else:
            selected = await context.candidate_workflow(nested, {}, label="plain")
        await context.adopt_candidate(selected)
        assert (repo / "source.py").read_text() == "value = 2\n"
    finally:
        await environment.cleanup()


@pytest.mark.parametrize("mode", ["agent", "workflow"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_physical_source_inspection_failure_keeps_candidate(tmp_path, monkeypatch, mode, cancelled):
    repo = _repository(tmp_path)
    environment = LocalEnvironment(str(repo))
    workspace = EnvCandidateWorkspace(environment)
    context = WorkflowContext(_Factory(environment), candidate_workspace=workspace)
    leases = []
    acquire = workspace.acquire

    async def failed_inspection(self):
        if cancelled:
            raise asyncio.CancelledError
        raise OSError("physical source unavailable")

    async def remember(label):
        lease = await acquire(label)
        leases.append(lease)
        monkeypatch.setattr(type(lease), "physical_source_changed", failed_inspection)
        return lease

    monkeypatch.setattr(workspace, "acquire", remember)

    async def nested(child, _args):
        return await child.agent("edit")

    try:
        error = asyncio.CancelledError if cancelled else CandidateWorkspaceTrackingError
        with pytest.raises(error):
            if mode == "agent":
                await context.candidate_agent("edit", label="inspection")
            else:
                await context.candidate_workflow(nested, {}, label="inspection")
        assert Path(leases[0].candidate_workspace, "source.py").read_text() == "value = 2\n"
        assert (repo / "source.py").read_text() == "value = 1\n"
    finally:
        for lease in leases:
            await lease.cleanup()
        await environment.cleanup()
