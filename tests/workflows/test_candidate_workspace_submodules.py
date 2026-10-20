from __future__ import annotations

import shlex
import shutil
import sys
from pathlib import Path

import pytest

from opencollab.adapters import worktree_pool
from opencollab.adapters._env_local import LocalEnvironment
from opencollab.adapters._env_worktree import WorktreeEnvironment
from opencollab.adapters._env_worktree_submodules import _initialize_source_available_submodules
from opencollab.adapters.candidate_workspace import EnvCandidateWorkspace
from opencollab.adapters.worktree_pool import WorktreePool
from opencollab.application.workflow import WorkflowContext
from opencollab.environments import attach_container
from tests.support.worktree_delivery_support import CONTAINER_ID
from tests.support.worktree_delivery_support import worktree_transport as worktree_transport
from tests.workflows.test_workflow_candidate_workspace import _Factory, _git, _repository


def _dependency(tmp_path: Path, name: str) -> Path:
    parent = tmp_path / name
    parent.mkdir()
    return _repository(parent)

@pytest.mark.parametrize("backend", ["local", "candidate", "container", "container-candidate", "container-team"])
@pytest.mark.parametrize(
    ("module_path", "module_name"),
    [("vendor/dependency", None), ("vendor/Shared Library", None), ("vendor/dependency", "Shared Library")],
    ids=["plain", "default-name-with-spaces", "explicit-name-with-spaces"],
)
async def test_available_submodule_names_preserve_spaces(
    worktree_transport, tmp_path, monkeypatch, backend, module_path, module_name,
):
    dependency = _dependency(tmp_path, "dependency")
    source = _repository(tmp_path)
    name_arguments = ("--name", module_name) if module_name is not None else ()
    _git(source, "-c", "protocol.file.allow=always", "submodule", "add",
         *name_arguments, str(dependency), module_path)
    _git(source, "config", "-f", ".gitmodules", f"submodule.{module_name or module_path}.url",
         "https://example.invalid/dependency.git")
    _git(source, "commit", "-am", "committed dependency")
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    monkeypatch.setattr(worktree_pool, "CONTAINER_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    base = (
        attach_container(container_id=CONTAINER_ID, workspace=str(source))
        if backend.startswith("container") else LocalEnvironment(str(source))
    )
    pool = WorktreePool(
        str(source), use_worktrees=True, base_environment=base, enable_adoption=backend == "container-team",
    )
    lease = None
    environment = None
    try:
        if backend in {"candidate", "container-candidate"}:
            lease = await EnvCandidateWorkspace(base).acquire("submodule-spaces")
            environment = lease.environment
        else:
            environment = await pool.acquire("submodule-spaces")
        assert await environment.read_file(f"{module_path}/source.py") == "value = 1\n"
        code = f"print(__import__('runpy').run_path({f'{module_path}/source.py'!r})['value'])"
        executed = await environment.exec_cmd(shlex.join(("python3", "-c", code)))
        assert executed.returncode == 0, executed.stderr
        assert executed.stdout == "1\n"
        assert await (lease.diff() if lease is not None else environment.get_diff()) == ""
    finally:
        if lease is not None:
            await lease.cleanup()
        await pool.release()
        await base.cleanup()
        if backend in {"container-candidate", "container-team"} and environment is not None:
            assert not Path(environment.workspace).exists()
            if Path("/tmp").resolve() != Path("/tmp"):
                # The local transport sees macOS's /tmp alias while Linux
                # containers use /tmp directly. Prune this test's removed alias.
                _git(source, "worktree", "prune")
    assert _git(source, "status", "--porcelain") == ""
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1

@pytest.mark.parametrize("nested", [False, True])
async def test_candidate_reads_committed_source_available_submodules(tmp_path, monkeypatch, nested):
    dependency = _dependency(tmp_path, "dependency")
    dependency_path = "vendor/dependency"
    if nested:
        middle = _dependency(tmp_path, "middle")
        _git(middle, "-c", "protocol.file.allow=always", "submodule", "add", str(dependency), "vendor/leaf")
        _git(middle, "config", "-f", ".gitmodules", "submodule.vendor/leaf.url", "https://example.invalid/leaf.git")
        _git(middle, "add", ".gitmodules", "vendor/leaf")
        _git(middle, "commit", "-m", "nested dependency")
        dependency = middle
        dependency_path += "/vendor/leaf"
    source = _repository(tmp_path)
    _git(source, "-c", "protocol.file.allow=always", "submodule", "add", str(dependency), "vendor/dependency")
    if nested:
        _git(
            source / "vendor/dependency", "-c", "protocol.file.allow=always",
            "-c", f"submodule.vendor/leaf.url={tmp_path / 'dependency/repo'}",
            "submodule", "update", "--init", "--no-fetch", "--", "vendor/leaf",
        )
    _git(source, "config", "-f", ".gitmodules", "submodule.vendor/dependency.url", "https://example.invalid/dep.git")
    _git(source, "add", ".gitmodules", "vendor/dependency")
    _git(source, "commit", "-m", "committed dependency")
    assert (source / dependency_path / "source.py").read_text() == "value = 1\n"
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    base = LocalEnvironment(str(source))
    lease = None
    try:
        lease = await EnvCandidateWorkspace(base).acquire("dependency")
        assert await lease.environment.read_file(f"{dependency_path}/source.py") == "value = 1\n"
        assert await lease.diff() == ""
        candidate_path = Path(lease.candidate_workspace)
    finally:
        if lease is not None:
            await lease.cleanup()
        await base.cleanup()
    assert not candidate_path.exists()
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.parametrize("backend", ["workflow", "team"])
@pytest.mark.parametrize("update", ["checkout", "none"])
@pytest.mark.parametrize(
    ("module_path", "nested"),
    [("vendor/dependency", False), ('vendor/Shared "Library"\ncopy', False),
     ('vendor/Shared "Library"\ncopy', True)],
    ids=["plain", "quoted-path", "nested"],
)
async def test_candidate_replays_initialized_submodules_with_disabled_updates(
    tmp_path, monkeypatch, backend, update, module_path, nested,
):
    leaf = _dependency(tmp_path, "leaf")
    (leaf / "source.py").write_text("value = 42\n")
    _git(leaf, "commit", "-am", "prepare executable dependency")
    dependency = leaf
    nested_path = "inner/Leaf Library"
    if nested:
        dependency = _dependency(tmp_path, "middle")
        _git(dependency, "-c", "protocol.file.allow=always", "submodule", "add",
             "--name", "leaf", str(leaf), nested_path)
        _git(dependency, "config", "-f", ".gitmodules", "submodule.leaf.update", update)
        _git(dependency, "config", "-f", ".gitmodules", "submodule.leaf.url", "https://example.invalid/leaf.git")
        _git(dependency, "commit", "-am", "record nested dependency")
    source = _repository(tmp_path)
    _git(source, "-c", "protocol.file.allow=always", "submodule", "add",
         "--name", "dependency", str(dependency), module_path)
    if nested:
        _git(source / module_path, "-c", "protocol.file.allow=always",
             "-c", f"submodule.leaf.url={leaf}", "submodule", "update",
             "--init", "--no-fetch", "--checkout", "--", nested_path)
    _git(source, "config", "-f", ".gitmodules", "submodule.dependency.update", update)
    _git(source, "config", "-f", ".gitmodules", "submodule.dependency.url", "https://example.invalid/dependency.git")
    dependency_path = f"{module_path}/{nested_path}" if nested else module_path
    (source / "app.py").write_text(
        f"import runpy\nprint(runpy.run_path({f'{dependency_path}/source.py'!r})['value'])\n"
    )
    _git(source, "add", ".")
    _git(source, "commit", "-m", "record project dependency strategy")
    assert _git(source, "status", "--porcelain") == ""
    source_index = (source / ".git/index").read_bytes()
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    base = LocalEnvironment(str(source))
    pool = WorktreePool(str(source), use_worktrees=True, enable_adoption=True)
    executions = []
    command = shlex.join((sys.executable, "-B", "app.py"))

    async def execute(environment):
        result = await environment.exec_cmd(command)
        executions.append((result.returncode, result.stdout, result.stderr))
        return result.stdout

    async def candidate(child, _args):
        environment = LocalEnvironment(child.workspace_root)
        try:
            return await execute(environment)
        finally:
            await environment.cleanup()

    try:
        await execute(base)
        assert executions[-1][:2] == (0, "42\n"), executions[-1][2]
        if backend == "workflow":
            context = WorkflowContext(_Factory(base), candidate_workspace=EnvCandidateWorkspace(base))
            captured = await context.candidate_workflow(candidate, {}, label="dependency-strategy")
            assert captured.diff == ""
        else:
            environment = await pool.acquire("coder")
            await execute(environment)
            assert await environment.get_diff() == ""
        assert executions[-1][:2] == (0, "42\n"), executions[-1][2]
        assert (source / ".git/index").read_bytes() == source_index
        assert _git(source, "status", "--porcelain") == ""
    finally:
        await pool.release()
        await base.cleanup()
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1

def _partly_initialized_source(tmp_path: Path) -> Path:
    dependency = _dependency(tmp_path, "dependency")
    source = _repository(tmp_path)
    for name in ("required", "optional"):
        _git(source, "-c", "protocol.file.allow=always", "submodule", "add", str(dependency), f"vendor/{name}")
    _git(source, "commit", "-am", "committed dependencies")
    _git(source, "submodule", "deinit", "-f", "--", "vendor/optional")
    assert _git(source, "status", "--porcelain") == ""
    return source

async def test_candidate_copies_initialized_dependency_and_leaves_optional_empty(tmp_path):
    source = _partly_initialized_source(tmp_path)
    base = LocalEnvironment(str(source))
    lease = None
    try:
        lease = await EnvCandidateWorkspace(base).acquire("partly-initialized")
        assert await lease.environment.read_file("vendor/required/source.py") == "value = 1\n"
        assert await lease.environment.read_file("source.py") == "value = 1\n"
        optional = Path(lease.candidate_workspace) / "vendor/optional"
        assert optional.is_dir()
        assert list(optional.iterdir()) == []
        assert await lease.diff() == ""
        assert _git(source, "status", "--porcelain") == ""
    finally:
        if lease is not None:
            await lease.cleanup()
        await base.cleanup()
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1

async def test_candidate_from_repository_subdirectory_initializes_only_scoped_submodules(tmp_path):
    dependency = _dependency(tmp_path, "nested-dependency")
    outside = _dependency(tmp_path, "outside-dependency")
    repository = _repository(tmp_path)
    scoped_source = repository / "src"
    scoped_source.mkdir()
    (scoped_source / "app.py").write_text("app = True\n")
    _git(repository, "-c", "protocol.file.allow=always", "submodule", "add", str(dependency), "src/vendor/required")
    _git(repository, "-c", "protocol.file.allow=always", "submodule", "add", str(outside), "vendor/outside")
    _git(repository, "commit", "-am", "add source and modules")
    _git(repository, "submodule", "deinit", "-f", "--", "vendor/outside")
    base = LocalEnvironment(str(scoped_source))
    lease = None
    try:
        lease = await EnvCandidateWorkspace(base).acquire("nested-source")
        assert await lease.environment.read_file("vendor/required/source.py") == "value = 1\n"
        optional = Path(lease.candidate_workspace) / "vendor/outside"
        assert optional.is_dir() and list(optional.iterdir()) == []
    finally:
        if lease is not None:
            await lease.cleanup()
        await base.cleanup()

async def test_worktree_environment_keeps_requiring_initialized_source_submodules(tmp_path):
    source = _partly_initialized_source(tmp_path)
    environment = WorktreeEnvironment(str(source))
    try:
        with pytest.raises(RuntimeError, match="not initialized"):
            await environment.setup()
    finally:
        await environment.cleanup()
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1

@pytest.mark.parametrize("damage", ["invalid_path", "external_module", "broken_metadata"])
async def test_source_submodule_errors_are_reported(tmp_path, damage):
    source = _partly_initialized_source(tmp_path)
    required = source / "vendor/required"
    if damage == "invalid_path":
        _git(source, "config", "-f", ".gitmodules", "submodule.vendor/required.path", "../../dependency/repo")
    elif damage == "external_module":
        shutil.rmtree(required)
        required.symlink_to(tmp_path / "dependency/repo", target_is_directory=True)
    else:
        (required / ".git").write_text("gitdir: missing-git-directory\n")
    base = LocalEnvironment(str(source))

    async def git_in(workspace, *arguments):
        return await base.exec_cmd(shlex.join(("git", "-C", workspace, *arguments)))

    try:
        with pytest.raises(RuntimeError):
            await _initialize_source_available_submodules(
                str(source), str(tmp_path / "target"), git_in=git_in, require_initialized=False,
            )
    finally:
        await base.cleanup()
