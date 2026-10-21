from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from opencollab.adapters._env_local import LocalEnvironment
from opencollab.adapters.candidate_workspace import EnvCandidateWorkspace
from opencollab.adapters.worktree_pool import WorktreePool
from opencollab.application.workflow import WorkflowContext
from opencollab.application.workflow_candidates import CandidateCaptureError, CandidateWorkspaceTrackingError
from tests.runtime.test_team_candidate_delivery import _adoption_scheduler
from tests.workflows.test_candidate_physical_submodules import _SCRIPT, _index, _source
from tests.workflows.test_workflow_candidate_workspace import _git


@pytest.mark.parametrize("backend", ["workflow", "team"])
@pytest.mark.parametrize("location", ["source", "candidate"])
@pytest.mark.parametrize("initial_conversion", [False, True], ids=["plain", "converted"])
@pytest.mark.parametrize("physical_change", [False, True], ids=["stable", "changed"])
@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
async def test_submodule_conversion_settings_compare_contents(
    tmp_path, backend, location, initial_conversion, physical_change, nested,
):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    source_module = source / module_path
    _git(source_module, "config", "core.autocrlf", str(initial_conversion).lower())
    source_index, module_index = _index(source), _index(source_module)
    environment = LocalEnvironment(str(source))
    workspace = EnvCandidateWorkspace(environment)
    pool = WorktreePool(str(source), use_worktrees=True, base_environment=environment, enable_adoption=True)
    leases = []
    tested = []
    application = f'import subprocess\nsubprocess.run(["./{module_path}/helper.sh"], check=True)\n'

    async def edit(candidate):
        await candidate.write_file("app.py", application)
        executed = await candidate.exec_cmd(f"{shlex.quote(sys.executable)} -B app.py")
        tested.append((executed.returncode, executed.stdout))
        assert tested[-1] == (0, "42\n")
        module = source_module if location == "source" else Path(candidate.workspace, module_path)
        _git(module, "config", "core.autocrlf", "true")
        if physical_change:
            (module / "helper.sh").write_bytes(_SCRIPT.replace(b"\n", b"\r\n"))
            _git(module, "add", "helper.sh")
        assert _git(module, "--no-optional-locks", "status", "--porcelain") == ""

    acquire = workspace.acquire

    async def remember(label):
        lease = await acquire(label)
        leases.append(lease)
        return lease

    workspace.acquire = remember
    candidate = None
    try:
        if backend == "workflow":
            context = WorkflowContext(object(), candidate_workspace=workspace)

            async def flow(child, _arguments):
                await edit(leases[-1].environment)
                return "tested"

            if physical_change:
                failure = CandidateWorkspaceTrackingError if location == "source" else CandidateCaptureError
                with pytest.raises(failure, match="worktree preserved"):
                    await context.candidate_workflow(flow, {}, label="module-settings")
                candidate = leases[-1].environment
            else:
                selected = await context.candidate_workflow(flow, {}, label="module-settings")
                await context.adopt_candidate(selected)
        else:
            candidate = await pool.acquire("coder")
            await edit(candidate)
            if location == "source":
                assert await candidate._lease.physical_source_changed() is physical_change
            if physical_change:
                if location == "candidate":
                    with pytest.raises(RuntimeError, match="submodule physical changes"):
                        await candidate.capture_changes()
                else:
                    candidate.retain_changes()
                with pytest.raises(OSError, match="retained"):
                    await pool.release()
            else:
                scheduler = _adoption_scheduler(pool, candidate)
                await scheduler._append_worktree_diff(candidate, "tested", aid=7, role="coder")
                await scheduler.adopt_agent(0, 7)

        assert tested == [(0, "42\n")]
        if physical_change:
            assert candidate is not None
            assert Path(candidate.workspace, "app.py").read_text() == application
            assert not (source / "app.py").exists()
            changed_module = source_module if location == "source" else Path(candidate.workspace, module_path)
            assert (changed_module / "helper.sh").read_bytes() == _SCRIPT.replace(b"\n", b"\r\n")
        else:
            delivered = await environment.exec_cmd(f"{shlex.quote(sys.executable)} -B app.py")
            assert (delivered.returncode, delivered.stdout) == (0, "42\n")
            assert _index(source_module) == module_index
            assert (source_module / "helper.sh").read_bytes() == _SCRIPT
        assert _index(source) == source_index
    finally:
        if backend == "workflow":
            for lease in leases:
                await lease.cleanup()
        elif candidate is not None:
            if location == "candidate":
                _git(Path(candidate.workspace, module_path), "config", "core.autocrlf", "false")
                if physical_change:
                    Path(candidate.workspace, module_path, "helper.sh").write_bytes(_SCRIPT)
                    _git(Path(candidate.workspace, module_path), "add", "helper.sh")
            await candidate.capture_changes()
        await pool.release()
        await environment.cleanup()
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.parametrize("backend", ["workflow", "team"])
@pytest.mark.parametrize("location", ["source", "candidate"], ids=["source_side", "candidate_side"])
@pytest.mark.parametrize("conversion", ["attributes", "config", "plain"])
@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
async def test_new_submodule_initialization_keeps_candidate(
    tmp_path, backend, location, conversion, nested,
):
    source, module_path = _source(tmp_path, nested=nested, converted=conversion == "attributes")
    module_root = source / "vendor/dependency" if nested else source
    initialize_path = "vendor/leaf" if nested else module_path
    _git(module_root, "submodule", "deinit", "-f", "--", initialize_path)
    (source / "source.py").write_text("value = 17\n")
    _git(source, "add", "source.py")
    source_contents = "value = 17\n# user edit\n"
    (source / "source.py").write_text(source_contents)
    source_index = _index(source)
    environment = LocalEnvironment(str(source))
    workspace = EnvCandidateWorkspace(environment)
    pool = WorktreePool(str(source), use_worktrees=True, base_environment=environment, enable_adoption=True)
    leases = []
    tested = []
    candidate = None
    application = (
        f'import subprocess\nsubprocess.run(["./{module_path}/helper.sh"], check=True)\n'
        if location == "candidate" else "print(42)\n"
    )

    async def edit(candidate_environment):
        target = source if location == "source" else Path(candidate_environment.workspace)
        root = target / "vendor/dependency" if nested else target
        _git(root, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--checkout",
             "--", initialize_path)
        if conversion == "config":
            _git(target / module_path, "config", "core.autocrlf", "true")
        await candidate_environment.write_file("app.py", application)
        executed = await candidate_environment.exec_cmd(f"{shlex.quote(sys.executable)} -B app.py")
        tested.append((executed.returncode, executed.stdout))
        assert tested[-1] == (0, "42\n")
        assert _git(target / module_path, "status", "--porcelain") == ""

    acquire = workspace.acquire

    async def remember(label):
        lease = await acquire(label)
        leases.append(lease)
        return lease

    workspace.acquire = remember
    try:
        if backend == "workflow":
            context = WorkflowContext(object(), candidate_workspace=workspace)

            async def flow(child, _arguments):
                await edit(leases[-1].environment)
                return "tested"

            failure = CandidateWorkspaceTrackingError if location == "source" else CandidateCaptureError
            with pytest.raises(failure, match="worktree preserved"):
                await context.candidate_workflow(flow, {}, label="initialize-module")
            candidate = leases[-1].environment
        else:
            candidate = await pool.acquire("coder")
            await edit(candidate)
            if location == "source":
                assert await candidate._lease.physical_source_changed()
                candidate.retain_changes()
            else:
                with pytest.raises(RuntimeError, match="submodule physical changes"):
                    await candidate.capture_changes()
            with pytest.raises(OSError, match="retained"):
                await pool.release()

        assert tested == [(0, "42\n")]
        assert candidate is not None
        assert Path(candidate.workspace, "app.py").read_text() == application
        assert not (source / "app.py").exists()
        assert (source / module_path / "helper.sh").exists() is (location == "source")
        assert (source / "source.py").read_text() == source_contents
        assert _index(source) == source_index
    finally:
        if backend == "workflow":
            for lease in leases:
                await lease.cleanup()
        elif candidate is not None:
            await candidate._lease.cleanup()
            candidate._delivery_pending = False
        await pool.release()
        await environment.cleanup()
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1
