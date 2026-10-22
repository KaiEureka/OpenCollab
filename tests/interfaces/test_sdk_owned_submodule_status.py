from __future__ import annotations

import pytest

from opencollab import OpenCollab
from tests.workflows.test_candidate_physical_submodules import _SCRIPT, _index, _source
from tests.workflows.test_workflow_candidate_workspace import _git


def _fixture(tmp_path, nested, layout, special=False):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    top = "vendor/dependency"
    if special:
        top = "vendor/shared module"
        _git(source, "mv", "vendor/dependency", top)
        _git(source, "commit", "-qm", "Move dependency path")
        module_path = top + module_path.removeprefix("vendor/dependency")
    records = "records [current] '\n" if special else "records"
    artifacts = (
        tmp_path / records if layout == "external" else
        source / records if layout == "root" else source / module_path / records
    )
    repositories = (source, source / top, *((source / module_path,) if nested else ()))
    return source, module_path, artifacts, repositories


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("layout", ["external", "root", "module"])
async def test_public_source_status_ignores_owned_submodule_records(tmp_path, nested, layout, special=False):
    source, _module_path, artifacts, repositories = _fixture(tmp_path, nested, layout, special)
    indexes = tuple(_index(repository) for repository in repositories)
    worktrees = _git(source, "worktree", "list", "--porcelain")

    async def flow(context, _inputs):
        return {
            "source_changed": await context.source_changed(),
            "tree_changed": await context.tree_changed(),
            "diff": await context.diff(),
        }

    result = await OpenCollab(source).workflow(flow, artifacts=artifacts, trace=False)
    assert result.ok, (result.reason, result.error)
    assert result.output == {"source_changed": False, "tree_changed": False, "diff": ""}
    assert (artifacts / "workflow.json").is_file()
    assert tuple(_index(repository) for repository in repositories) == indexes
    assert _git(source, "worktree", "list", "--porcelain") == worktrees


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
async def test_public_source_status_handles_special_owned_submodule_paths(tmp_path, nested):
    await test_public_source_status_ignores_owned_submodule_records(tmp_path, nested, "module", special=True)


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("mutation", ["root-tracked", "module-tracked", "module-untracked", "owned-tracked"])
async def test_public_owned_submodule_status_keeps_source_edits_visible(tmp_path, nested, mutation):
    source, module_path, artifacts, repositories = _fixture(tmp_path, nested, "module")
    module = source / module_path
    if mutation == "owned-tracked":
        changed = artifacts / "pinned.py"
    else:
        changed = source / "source.py" if mutation == "root-tracked" else module / (
            "helper.sh" if mutation == "module-tracked" else "business.py"
        )
    indexes = tuple(_index(repository) for repository in repositories)
    worktrees = _git(source, "worktree", "list", "--porcelain")
    content = _SCRIPT.replace(b"42", b"84") if mutation == "module-tracked" else b"value = 2\n"
    if mutation != "owned-tracked":
        changed.write_bytes(content)

    async def flow(context, _inputs):
        nonlocal indexes
        if mutation == "owned-tracked":
            changed.write_text("value = 1\n")
            _git(module, "add", "records/pinned.py")
            indexes = tuple(_index(repository) for repository in repositories)
            changed.write_bytes(content)
        return {
            "source_changed": await context.source_changed(),
            "tree_changed": await context.tree_changed(),
            "diff": await context.diff(),
            "root_excluded": await context.source_changed(["source.py"])
            if mutation == "root-tracked" else None,
        }

    result = await OpenCollab(source).workflow(flow, artifacts=artifacts, trace=False)
    assert result.ok, (result.reason, result.error)
    assert result.output["source_changed"] is True
    assert result.output["tree_changed"] is True
    if mutation == "root-tracked":
        assert "+value = 2" in result.output["diff"]
        assert result.output["root_excluded"] is False
    assert changed.read_bytes() == content
    assert (artifacts / "workflow.json").is_file()
    assert tuple(_index(repository) for repository in repositories) == indexes
    assert _git(source, "worktree", "list", "--porcelain") == worktrees
