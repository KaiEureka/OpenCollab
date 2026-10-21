from __future__ import annotations

import pytest

from opencollab import OpenCollab
from opencollab.patches import patch_paths
from tests.workflows.test_candidate_physical_submodules import _SCRIPT, _index, _source
from tests.workflows.test_workflow_candidate_workspace import _git


def _workspace(tmp_path, nested, special):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    if special:
        top = "vendor/dependency [1]*? 'é'"
        _git(source, "mv", "vendor/dependency", top)
        _git(source, "commit", "-am", "Rename module with literal path characters")
        module_path = top + ("/vendor/leaf" if nested else "")
    return source, module_path


def _state(source, module_path):
    repositories = [source, source / module_path]
    if module_path.endswith("/vendor/leaf"):
        repositories.append(source / module_path.removesuffix("/vendor/leaf"))
    return (
        tuple((_index(path), _git(path, "rev-parse", "HEAD")) for path in repositories),
        _git(source, "worktree", "list", "--porcelain"),
    )


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("special", [False, True], ids=["ordinary-path", "literal-path"])
@pytest.mark.parametrize("tracked", [False, True], ids=["untracked", "tracked"])
@pytest.mark.parametrize("whole_module", [False, True], ids=["file", "module"])
@pytest.mark.parametrize("root_edit", [False, True], ids=["excluded-only", "root-change"])
@pytest.mark.parametrize("artifacts", [False, True], ids=["no-artifacts", "module-artifacts"])
async def test_public_source_exclusions_filter_submodule_contents(
    tmp_path, nested, special, tracked, whole_module, root_edit, artifacts,
):
    source, module_path = _workspace(tmp_path, nested, special)
    filename = "helper.sh" if tracked else "test [1]*? 'é'.py"
    injected = source / module_path / filename
    contents = _SCRIPT.replace(b"42", b"84") if tracked else b"def test_injected():\n    assert True\n"
    injected.write_bytes(contents)
    if root_edit:
        (source / "source.py").write_text("value = 0\n")
        _git(source, "add", "source.py")
        (source / "source.py").write_text("value = 2\n")
    excluded = (module_path if whole_module else f"{module_path}/{filename}",)
    expected_patch = _git(
        source, "--no-optional-locks", "diff", "HEAD", "--binary", "--full-index", "--no-ext-diff",
        "--no-textconv", "--no-color", "--unified=3", "--src-prefix=a/", "--dst-prefix=b/", "--", "source.py",
    )
    original = _state(source, module_path)

    async def flow(context, _inputs):
        observation = {
            "changed": await context.source_changed(excluded),
            "patch": await context.diff(excluded),
        }
        if artifacts:
            observation["unfiltered_patch"] = await context.diff()
        return observation

    result = await OpenCollab(source).workflow(
        flow, trace=False, artifacts=source / module_path / "records" if artifacts else None,
    )
    assert result.ok, result.reason
    assert result.output["changed"] is root_edit
    assert result.output["patch"] == expected_patch
    assert patch_paths(result.output["patch"]) == (["source.py"] if root_edit else [])
    if root_edit:
        assert "+value = 2" in result.output["patch"]
    else:
        assert result.output["patch"] == ""
    if artifacts:
        assert result.output["unfiltered_patch"] is None
    if root_edit:
        assert _git(source, "show", ":source.py") == "value = 0\n"
    assert injected.read_bytes() == contents
    assert _state(source, module_path) == original


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("artifacts", [False, True], ids=["no-artifacts", "module-artifacts"])
async def test_public_submodule_exclusions_use_the_exposed_workspace(tmp_path, nested, artifacts):
    source, module_path = _workspace(tmp_path, nested, True)
    (source / "package/vendor").mkdir(parents=True)
    top = module_path.removesuffix("/vendor/leaf") if nested else module_path
    _git(source, "mv", top, "package/" + top)
    _git(source, "commit", "-am", "Move the module into the exposed workspace")
    module_path = "package/" + module_path
    module = source / module_path
    injected = module / "test [1]*? 'é'.py"
    injected.write_text("def test_injected():\n    assert True\n")
    original = _state(source, module_path)

    async def flow(context, _inputs):
        excluded = (injected.relative_to(source / "package").as_posix(),)
        assert await context.source_changed(excluded) is False
        assert await context.diff(excluded) == ""
        (source / "source.py").write_text("value = 2\n")
        return {"changed": await context.source_changed(excluded), "patch": await context.diff(excluded)}

    result = await OpenCollab(source / "package").workflow(
        flow, trace=False, artifacts=module / "records" if artifacts else None,
    )
    assert result.ok, result.reason
    assert result.output["changed"] is True
    assert patch_paths(result.output["patch"]) == ["source.py"]
    assert "+value = 2" in result.output["patch"]
    assert _state(source, module_path) == original


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("mutation", ["tracked", "untracked", "commit"])
@pytest.mark.parametrize("artifacts", [False, True], ids=["no-artifacts", "module-artifacts"])
async def test_public_file_exclusions_keep_other_submodule_changes_visible(tmp_path, nested, mutation, artifacts):
    source, module_path = _workspace(tmp_path, nested, True)
    module = source / module_path
    injected = module / "test [1]*? 'é'.py"
    injected.write_text("def test_injected():\n    assert True\n")
    if mutation == "tracked":
        (module / "helper.sh").write_bytes(_SCRIPT.replace(b"42", b"84"))
    else:
        (module / "test 1x q.py").write_text("value = 2\n")
        if mutation == "commit":
            _git(module, "add", "test 1x q.py")
            _git(module, "-c", "user.name=Test User", "-c", "user.email=test@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "Change module revision")
    original = _state(source, module_path)

    async def flow(context, _inputs):
        excluded = (injected.relative_to(source).as_posix(),)
        return {"changed": await context.source_changed(excluded), "patch": await context.diff(excluded)}

    result = await OpenCollab(source).workflow(flow, trace=False, artifacts=module / "records" if artifacts else None)
    assert result.ok, result.reason
    assert result.output["changed"] is True
    assert result.output["patch"] is None
    assert injected.read_text() == "def test_injected():\n    assert True\n"
    assert _state(source, module_path) == original


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("artifacts", [False, True], ids=["no-artifacts", "module-artifacts"])
@pytest.mark.parametrize("subdirectory", [False, True], ids=["repository-root", "exposed-directory"])
@pytest.mark.parametrize("tracked", [False, True], ids=["untracked", "tracked"])
async def test_public_directory_exclusions_keep_git_root_semantics(
    tmp_path, nested, artifacts, subdirectory, tracked,
):
    source, module_path = _workspace(tmp_path, nested, True)
    workspace = source
    if subdirectory:
        workspace = source / "package"
        (workspace / "vendor").mkdir(parents=True)
        top = module_path.removesuffix("/vendor/leaf") if nested else module_path
        _git(source, "mv", top, "package/" + top)
        _git(source, "commit", "-am", "Move the module into the exposed workspace")
        module_path = "package/" + module_path
    module = source / module_path
    relative_module = module.relative_to(workspace).as_posix()
    injected = module / ("helper.sh" if tracked else "test [1]*? 'é'.py")
    contents = _SCRIPT.replace(b"42", b"84") if tracked else b"def test_injected():\n    assert True\n"
    injected.write_bytes(contents)
    original = _state(source, module_path)

    async def flow(context, _inputs):
        before = []
        for path in (".", "./", "vendor", relative_module):
            before.append({"changed": await context.source_changed((path,)), "patch": await context.diff((path,))})
        (source / "source.py").write_text("value = 2\n")
        return {"before": before, "changed": await context.source_changed((".",)),
                "patch": await context.diff((".",))}

    result = await OpenCollab(workspace).workflow(
        flow, trace=False, artifacts=module / "records" if artifacts else None,
    )
    assert result.ok, result.reason
    assert result.output["before"] == [{"changed": False, "patch": ""}] * 4
    assert result.output["changed"] is subdirectory
    assert patch_paths(result.output["patch"]) == (["source.py"] if subdirectory else [])
    if subdirectory:
        assert "+value = 2" in result.output["patch"]
    else:
        assert result.output["patch"] == ""
    assert injected.read_bytes() == contents
    assert _state(source, module_path) == original
