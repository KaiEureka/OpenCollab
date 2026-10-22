from __future__ import annotations

from pathlib import Path

import pytest

from opencollab.adapters._env_local import LocalEnvironment
from opencollab.adapters.candidate_workspace import EnvCandidateWorkspace
from opencollab.adapters.worktree_pool import WorktreePool
from opencollab.application.workflow import WorkflowContext
from opencollab.application.workflow_candidates import CandidateCaptureError
from tests.workflows.test_candidate_physical_submodules import _SCRIPT, _index, _source
from tests.workflows.test_workflow_candidate_workspace import _git

_CHANGES = [
    ("assume-unchanged", "bytes"),
    ("skip-worktree", "bytes"),
    ("assume-unchanged", "mode"),
    ("skip-worktree", "mode"),
    ("filemode", "mode"),
]


def _configure(module, setting):
    if setting == "filemode":
        _git(module, "config", "core.filemode", "false")
    else:
        _git(module, "update-index", f"--{setting}", "helper.sh")


def _change(module, mutation):
    script = module / "helper.sh"
    if mutation == "bytes":
        script.write_bytes(_SCRIPT.replace(b"42", b"84"))
    else:
        script.chmod(0o644)


def _indexed_contents(module):
    return _git(module, "ls-files", "--stage", "-v", "-z")


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("setting", ["assume-unchanged", "skip-worktree", "filemode"])
@pytest.mark.parametrize("backend", ["workflow", "team"])
async def test_submodule_skip_settings_preserve_unchanged_contents(tmp_path, nested, setting, backend):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    module = source / module_path
    _configure(module, setting)
    source_index, module_index = _index(source), _index(module)
    environment = LocalEnvironment(str(source))
    workspace = EnvCandidateWorkspace(environment)
    pool = WorktreePool(str(source), use_worktrees=True, base_environment=environment, enable_adoption=True)
    lease = None
    try:
        snapshot = await workspace.track_source(await workspace.source_diff())
        assert not await snapshot.changed(await workspace.source_diff())
        if backend == "workflow":
            lease = await workspace.acquire("unchanged-module")
            candidate = lease.environment
        else:
            candidate = await pool.acquire("coder")
            lease = candidate._lease
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


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize(("setting", "mutation"), _CHANGES)
@pytest.mark.parametrize("initial_setting", [False, True], ids=["later-setting", "initial-setting"])
async def test_source_submodule_skip_settings_detect_physical_changes(
    tmp_path, nested, setting, mutation, initial_setting,
):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    module = source / module_path
    if initial_setting:
        _configure(module, setting)
    environment = LocalEnvironment(str(source))
    workspace = EnvCandidateWorkspace(environment)
    lease = None
    try:
        snapshot = await workspace.track_source(await workspace.source_diff())
        lease = await workspace.acquire("source-module")
        if not initial_setting:
            _configure(module, setting)
        source_index, module_entries = _index(source), _indexed_contents(module)
        _change(module, mutation)
        assert _git(module, "--no-optional-locks", "status", "--porcelain") == ""
        assert await snapshot.changed(await workspace.source_diff())
        assert await lease.physical_source_changed()
        with pytest.raises(RuntimeError, match="source submodule physical contents cannot be reproduced"):
            await workspace.acquire("changed-source-module")
        assert (Path(lease.candidate_workspace) / module_path / "helper.sh").read_bytes() == _SCRIPT
        assert (Path(lease.candidate_workspace) / module_path / "helper.sh").stat().st_mode & 0o111
        assert _index(source) == source_index
        assert _indexed_contents(module) == module_entries
    finally:
        if lease is not None:
            await lease.cleanup()
        await environment.cleanup()


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize(("setting", "mutation"), _CHANGES)
@pytest.mark.parametrize("backend", ["workflow", "team"])
async def test_candidate_submodule_skip_settings_retain_physical_changes(
    tmp_path, nested, setting, mutation, backend,
):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    source_index, module_index = _index(source), _index(source / module_path)
    environment = LocalEnvironment(str(source))
    workspace = EnvCandidateWorkspace(environment)
    pool = WorktreePool(str(source), use_worktrees=True, base_environment=environment, enable_adoption=True)
    leases = []
    acquire = workspace.acquire

    async def remember(label):
        lease = await acquire(label)
        leases.append(lease)
        return lease

    workspace.acquire = remember

    async def edit(candidate):
        await candidate.write_file("app.py", "print(42)\n")
        module = Path(candidate.workspace) / module_path
        _configure(module, setting)
        _change(module, mutation)
        assert _git(module, "--no-optional-locks", "status", "--porcelain") == ""
        executed = await candidate.exec_cmd(f"./{module_path}/helper.sh")
        if mutation == "bytes":
            assert (executed.returncode, executed.stdout) == (0, "84\n")
        else:
            assert executed.returncode == 126
        return module, _indexed_contents(module)

    candidate = None
    try:
        if backend == "workflow":
            context = WorkflowContext(object(), candidate_workspace=workspace)

            async def flow(child, _arguments):
                await edit(leases[-1].environment)
                return "tested"

            with pytest.raises(CandidateCaptureError, match="worktree preserved"):
                await context.candidate_workflow(flow, {}, label="hidden-module")
            candidate = leases[-1].environment
            module = Path(candidate.workspace) / module_path
        else:
            candidate = await pool.acquire("coder")
            module, candidate_entries = await edit(candidate)
            with pytest.raises(RuntimeError, match="submodule (?:physical )?changes"):
                await candidate.capture_changes()
            assert _indexed_contents(module) == candidate_entries
            with pytest.raises(OSError, match="retained"):
                await pool.release()
        assert Path(candidate.workspace, "app.py").read_text() == "print(42)\n"
        assert not (source / "app.py").exists()
        assert (module / "helper.sh").read_bytes() == (
            _SCRIPT.replace(b"42", b"84") if mutation == "bytes" else _SCRIPT
        )
        assert bool((module / "helper.sh").stat().st_mode & 0o111) is (mutation == "bytes")
        assert _index(source) == source_index
        assert _index(source / module_path) == module_index
    finally:
        for lease in leases:
            await lease.cleanup()
        if backend == "team" and candidate is not None:
            module = Path(candidate.workspace) / module_path
            (module / "helper.sh").write_bytes(_SCRIPT)
            (module / "helper.sh").chmod(0o755)
            await candidate.capture_changes()
        await pool.release()
        await environment.cleanup()
