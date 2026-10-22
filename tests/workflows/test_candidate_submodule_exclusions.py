from __future__ import annotations

from pathlib import Path

import pytest

from opencollab import OpenCollab
from opencollab.adapters._env_local import LocalEnvironment
from opencollab.adapters.candidate_workspace import EnvCandidateWorkspace
from opencollab.application.workflow import WorkflowContext
from opencollab.application.workflow_candidates import CandidateCaptureError
from opencollab.patches import patch_paths
from tests.workflows.test_candidate_physical_submodules import _SCRIPT, _index, _source
from tests.workflows.test_workflow_candidate_workspace import _git


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("subdirectory", [False, True], ids=["repository-root", "exposed-directory"])
@pytest.mark.parametrize("mutation", ["untracked", "tracked", "physical"])
async def test_public_candidate_exclusions_filter_submodule_observations(
    tmp_path, nested, subdirectory, mutation,
):
    source, module_path = _source(tmp_path, nested=nested, converted=mutation == "physical")
    workspace = source
    if subdirectory:
        workspace = source / "package"
        (workspace / "vendor").mkdir(parents=True)
        _git(source, "mv", "vendor/dependency", "package/vendor/dependency")
        _git(source, "commit", "-am", "Move submodule into exposed workspace")
        module_path = "package/" + module_path
    relative_module = (source / module_path).relative_to(workspace).as_posix()
    source_index, module_index = _index(source), _index(source / module_path)

    async def inspect(child, _inputs):
        exposed = Path(child.workspace_root)
        root = exposed.parent if subdirectory else exposed
        module = exposed / relative_module
        filename = "test [1]*? 'é'.py" if mutation == "untracked" else "helper.sh"
        injected = module / filename
        if mutation == "untracked":
            injected.write_text("def test_injected():\n    assert True\n")
        else:
            injected.write_bytes(_SCRIPT.replace(b"42", b"84") if mutation == "tracked"
                                 else _SCRIPT.replace(b"\n", b"\r\n"))
        excluded = (f"{relative_module}/{filename}",)
        staged = tuple(_git(path, "diff", "--cached", "--binary", "HEAD") for path in (root, module))
        try:
            observed = {
                "excluded_changed": await child.source_changed(excluded),
                "excluded_patch": await child.diff(excluded),
                "module_changed": await child.source_changed((relative_module,)),
                "module_patch": await child.diff((relative_module,)),
                "directory_changed": await child.source_changed(("vendor",)),
                "directory_patch": await child.diff(("vendor",)),
                "workspace_changed": await child.source_changed((".",)),
                "workspace_patch": await child.diff((".",)),
            }
            (root / "source.py").write_text("value = 2\n")
            observed.update({
                "root_changed": await child.source_changed(excluded),
                "root_patch": await child.diff(excluded),
                "unfiltered_changed": await child.source_changed(),
                "unfiltered_patch": await child.diff(),
                "staged_changes_unchanged": tuple(
                    _git(path, "diff", "--cached", "--binary", "HEAD") for path in (root, module)
                ) == staged,
            })
            return observed
        finally:
            if mutation == "untracked":
                injected.unlink()
            else:
                injected.write_bytes(_SCRIPT)

    async def flow(context, _inputs):
        candidate = await context.candidate_workflow(inspect, {}, label="excluded-module-tests")
        return {"observed": candidate.output, "final_patch": candidate.diff}

    result = await OpenCollab(workspace).workflow(flow, trace=False)
    assert result.ok, result.reason
    observed = result.output["observed"]
    assert observed["excluded_changed"] is False
    assert observed["excluded_patch"] == ""
    assert observed["module_changed"] is False
    assert observed["module_patch"] == ""
    assert observed["directory_changed"] is False
    assert observed["directory_patch"] == ""
    assert observed["workspace_changed"] is False
    assert observed["workspace_patch"] == ""
    assert observed["root_changed"] is True
    assert patch_paths(observed["root_patch"]) == ["source.py"]
    assert "+value = 2" in observed["root_patch"]
    assert observed["unfiltered_changed"] is None
    assert observed["unfiltered_patch"] is None
    assert observed["staged_changes_unchanged"]
    assert result.output["final_patch"] == observed["root_patch"]
    assert (source / "source.py").read_text() == "value = 1\n"
    assert (source / module_path / "helper.sh").read_bytes() == _SCRIPT
    assert _index(source) == source_index
    assert _index(source / module_path) == module_index
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("physical", [False, True], ids=["tracked", "physical"])
async def test_candidate_exclusions_keep_other_submodule_edits_and_complete_delivery_checks(
    tmp_path, nested, physical,
):
    source, module_path = _source(tmp_path, nested=nested, converted=physical)
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
    changed = _SCRIPT.replace(b"\n", b"\r\n") if physical else _SCRIPT.replace(b"42", b"84")

    async def inspect(child, _inputs):
        module = Path(child.workspace_root, module_path)
        injected = module / "test [1]*? 'é'.py"
        injected.write_text("def test_injected():\n    assert True\n")
        excluded = (f"{module_path}/{injected.name}",)
        assert await child.source_changed(excluded) is False
        assert await child.diff(excluded) == ""
        (module / "helper.sh").write_bytes(changed)
        assert await child.source_changed(excluded) is None
        assert await child.diff(excluded) is None
        assert await child.source_changed((module_path,)) is False
        assert await child.diff((module_path,)) == ""
        return "checked module changes"

    try:
        with pytest.raises(CandidateCaptureError, match="worktree preserved"):
            await context.candidate_workflow(inspect, {}, label="retained-module-changes")
        assert Path(leases[0].candidate_workspace, module_path, "helper.sh").read_bytes() == changed
        assert (source / module_path / "helper.sh").read_bytes() == _SCRIPT
    finally:
        for lease in leases:
            await lease.cleanup()
        await environment.cleanup()
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1
