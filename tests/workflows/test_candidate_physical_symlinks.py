from __future__ import annotations

import shlex
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
from tests.support.worktree_delivery_support import CONTAINER_ID, git, make_repo
from tests.support.worktree_delivery_support import worktree_transport as worktree_transport

_APPLICATION = "from pathlib import Path\nprint(Path('vendor/library/link.txt').read_text().strip())\n"


def _source(tmp_path, *, nested=False, symlinks="false", with_link=True):
    leaf = make_repo(tmp_path / "leaf")
    (leaf / "value.txt").write_text("42\n")
    if with_link:
        (leaf / "link.txt").symlink_to("value.txt")
    git(leaf, "add", ".")
    git(leaf, "commit", "-qm", "Prepare dependency links")
    dependency = leaf
    if nested:
        dependency = make_repo(tmp_path / "middle")
        git(dependency, "-c", "protocol.file.allow=always", "submodule", "add", str(leaf), "vendor/leaf")
        git(dependency, "commit", "-qam", "Record nested dependency")
    source = make_repo(tmp_path / "source")
    module = source / "vendor/library"
    git(source, "clone", "--quiet", "--config", f"core.symlinks={symlinks}", str(dependency), str(module))
    git(source, "-c", "protocol.file.allow=always", "submodule", "add", str(dependency), "vendor/library")
    git(source, "commit", "-qam", "Record dependency")
    if nested:
        module /= "vendor/leaf"
        git(source, "clone", "--quiet", "--config", f"core.symlinks={symlinks}", str(leaf), str(module))
        git(source / "vendor/library", "-c", "protocol.file.allow=always", "submodule", "update", "--init")
    return source, module


def _index(repository):
    return Path(git(repository, "rev-parse", "--absolute-git-dir").strip(), "index").read_bytes()


def _environment(source, transport):
    if transport == "container":
        return attach_container(container_id=CONTAINER_ID, workspace=str(source))
    return LocalEnvironment(str(source))


@pytest.mark.parametrize("transport", ["local", "container"])
@pytest.mark.parametrize("backend", ["workflow", "team"])
@pytest.mark.parametrize("nested", [False, True])
async def test_git_cloned_link_placeholders_cannot_change_candidate_behavior(
    worktree_transport, tmp_path, transport, backend, nested,
):
    source, module = _source(tmp_path, nested=nested)
    assert git(source, "status", "--porcelain") == ""
    assert git(module, "status", "--porcelain") == ""
    assert git(module, "config", "--bool", "core.symlinks").strip() == "false"
    assert git(module, "ls-files", "--stage", "--", "link.txt").startswith("120000 ")
    assert not (module / "link.txt").is_symlink()
    assert (module / "link.txt").read_bytes() == b"value.txt"
    original_indices = _index(source), _index(module)
    environment = _environment(source, transport)
    pool = WorktreePool(str(source), use_worktrees=True, base_environment=environment, enable_adoption=True)
    try:
        with pytest.raises(RuntimeError, match="source submodule physical contents cannot be reproduced"):
            if backend == "workflow":
                await EnvCandidateWorkspace(environment).acquire("link-placeholder")
            else:
                await pool.acquire("coder")
        assert (module / "link.txt").read_bytes() == b"value.txt"
        assert (_index(source), _index(module)) == original_indices
        assert git(source, "status", "--porcelain") == ""
        assert git(module, "status", "--porcelain") == ""
    finally:
        await pool.release()
        await environment.cleanup()
        git(source, "worktree", "prune")
    assert git(source, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.parametrize("transport", ["local", "container"])
@pytest.mark.parametrize("changed", ["source", "candidate"])
async def test_recheckout_of_links_retains_the_executed_candidate(
    worktree_transport, tmp_path, transport, changed,
):
    source, module = _source(tmp_path, symlinks="true")
    git(module, "config", "core.symlinks", "false")
    assert (module / "link.txt").is_symlink()
    original_indices = _index(source), _index(module)
    original_entries = git(module, "ls-files", "--stage")
    environment = _environment(source, transport)
    workspace = EnvCandidateWorkspace(environment)
    context = WorkflowContext(object(), candidate_workspace=workspace)
    leases = []
    acquire = workspace.acquire
    tested = []

    async def remember(label):
        lease = await acquire(label)
        leases.append(lease)
        return lease

    workspace.acquire = remember

    async def edit(child, _args):
        candidate = Path(child.workspace_root)
        link = candidate / "vendor/library/link.txt"
        assert link.is_symlink()
        assert link.read_bytes() == b"42\n"
        (candidate / "app.py").write_text(_APPLICATION)
        executed = await leases[0].environment.exec_cmd(f"{shlex.quote(sys.executable)} -B app.py")
        assert (executed.returncode, executed.stdout) == (0, "42\n")
        tested.append(executed.stdout)
        target = module if changed == "source" else candidate / "vendor/library"
        git(target, "config", "core.symlinks", "false")
        (target / "link.txt").unlink()
        git(target, "checkout-index", "--force", "--", "link.txt")
        assert not (target / "link.txt").is_symlink()
        assert (target / "link.txt").read_bytes() == b"value.txt"
        assert git(target, "--no-optional-locks", "status", "--porcelain") == ""
        return "tested link"

    try:
        failure = CandidateWorkspaceTrackingError if changed == "source" else CandidateCaptureError
        with pytest.raises(failure, match="worktree preserved"):
            await context.candidate_workflow(edit, {}, label="rechecked-link")
        assert tested == ["42\n"]
        assert Path(leases[0].candidate_workspace, "app.py").read_text() == _APPLICATION
        assert not (source / "app.py").exists()
        assert git(module, "ls-files", "--stage") == original_entries
        assert _index(source) == original_indices[0]
        if changed == "candidate":
            assert _index(module) == original_indices[1]
            assert (module / "link.txt").is_symlink()
    finally:
        for lease in leases:
            await lease.cleanup()
        await environment.cleanup()
        git(source, "worktree", "prune")
    assert git(source, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.parametrize("transport", ["local", "container"])
@pytest.mark.parametrize("with_link", [False, True])
async def test_unconverted_link_settings_keep_executable_delivery_on_the_fast_path(
    worktree_transport, tmp_path, monkeypatch, transport, with_link,
):
    source, module = _source(tmp_path, symlinks="true" if with_link else "false", with_link=with_link)
    original_indices = _index(source), _index(module)
    environment = _environment(source, transport)
    workspace = EnvCandidateWorkspace(environment)
    application = _APPLICATION if with_link else _APPLICATION.replace("link.txt", "value.txt")

    async def unexpected_scan(*args, **kwargs):
        raise AssertionError("ordinary link settings scanned a physical tree")

    monkeypatch.setattr(_candidate_physical, "_physical_tree", unexpected_scan)
    monkeypatch.setattr(candidate_workspace, "_physical_tree", unexpected_scan)
    lease = None
    try:
        assert await workspace.physical_source_revision() is None
        lease = await workspace.acquire("ordinary-links")
        candidate = Path(lease.candidate_workspace)
        assert (candidate / "vendor/library/value.txt").read_bytes() == b"42\n"
        assert (candidate / "vendor/library/link.txt").is_symlink() == with_link
        (candidate / "app.py").write_text(application)
        executed = await lease.environment.exec_cmd(f"{shlex.quote(sys.executable)} -B app.py")
        assert (executed.returncode, executed.stdout) == (0, "42\n")
        patch = await lease.capture_changes()
        assert "+print(" in patch
        await workspace.adopt(patch)
        adopted = await environment.exec_cmd(f"{shlex.quote(sys.executable)} -B app.py")
        assert (adopted.returncode, adopted.stdout) == (0, "42\n")
        assert (_index(source), _index(module)) == original_indices
    finally:
        if lease is not None:
            await lease.cleanup()
        await environment.cleanup()
        git(source, "worktree", "prune")
    assert git(source, "worktree", "list", "--porcelain").count("worktree ") == 1


@pytest.mark.parametrize("value", ["false", "0", "off", "no", "", "true", "1", "on", "yes"])
@pytest.mark.parametrize("excluded", [False, True])
async def test_symlink_conversion_detection_uses_git_boolean_values(tmp_path, value, excluded):
    source = make_repo(tmp_path / "source")
    (source / "link.txt").symlink_to("initial.txt")
    git(source, "add", "link.txt")
    git(source, "config", "core.symlinks", value)
    environment = LocalEnvironment(str(source))
    try:
        assert await _candidate_physical._has_worktree_conversions(
            environment, str(source), exclude_paths=("link.txt",) if excluded else (),
        ) == (not excluded and value in {"false", "0", "off", "no", ""})
    finally:
        await environment.cleanup()
