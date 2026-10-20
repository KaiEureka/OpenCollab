from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from opencollab.adapters.env import LocalEnvironment
from opencollab.adapters.tools.adopt import AdoptTool
from opencollab.application.tool_execution import ToolRuntime
from opencollab.environments import attach_container
from tests.support.worktree_delivery_support import CONTAINER_ID, make_repo
from tests.support.worktree_delivery_support import worktree_transport as worktree_transport
from tests.tools.test_adopt_tool import _git


@pytest.fixture
def handoff(tmp_path, request):
    module_path, nested = getattr(request, "param", ("vendor/library", False))
    dependency = make_repo(tmp_path / "dependency")
    new_leaf = None
    if nested:
        leaf = make_repo(tmp_path / "leaf")
        (leaf / "answer.txt").write_text("1\n")
        _git(leaf, "add", ".")
        _git(leaf, "commit", "-qm", "Prepare old leaf")
        old_leaf = _git(leaf, "rev-parse", "HEAD")
        (leaf / "answer.txt").write_text("42\n")
        _git(leaf, "commit", "-qam", "Upgrade leaf")
        new_leaf = _git(leaf, "rev-parse", "HEAD")
        _git(dependency, "-c", "protocol.file.allow=always", "submodule", "add", str(leaf), "inner/leaf")
        _git(dependency / "inner/leaf", "checkout", "-q", old_leaf)
    (dependency / "answer.txt").write_text("1\n")
    (dependency / ".gitignore").write_text("settings.local\n")
    _git(dependency, "add", ".")
    _git(dependency, "commit", "-qm", "Prepare old dependency")
    old_module = _git(dependency, "rev-parse", "HEAD")
    (dependency / "answer.txt").write_text("42\n")
    (dependency / "added.txt").write_text("candidate addition\n")
    (dependency / "settings.local").write_text("candidate setting\n")
    if nested:
        _git(dependency / "inner/leaf", "checkout", "-q", new_leaf)
    _git(dependency, "add", "--force", ".")
    _git(dependency, "commit", "-qm", "Upgrade dependency")
    new_module = _git(dependency, "rev-parse", "HEAD")

    source = make_repo(tmp_path / "source")
    _git(source, "-c", "protocol.file.allow=always", "submodule", "add", "--name", "library",
         str(dependency), module_path)
    module = source / module_path
    _git(module, "checkout", "-q", old_module)
    if nested:
        _git(source, "add", module_path)
        _git(source, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive")
    answer_path = module_path + ("/inner/leaf/answer.txt" if nested else "/answer.txt")
    (source / "app.py").write_text(
        f"from pathlib import Path\nprint(Path({answer_path!r}).read_text().strip())\n"
    )
    (source / "version.txt").write_text("old\n")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "Prepare old project")
    start = _git(source, "rev-parse", "HEAD")
    _git(module, "checkout", "-q", new_module)
    (source / "version.txt").write_text("candidate\n")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "Upgrade project dependency")
    target = _git(source, "rev-parse", "HEAD")
    _git(source, "checkout", "-q", "--detach", start)
    _git(module, "checkout", "-q", old_module)
    _git(module, "config", "remote.origin.url", "https://unavailable.invalid/dependency")
    return SimpleNamespace(source=source, module=module, dependency=dependency, start=start,
                           target=target, old_module=old_module, new_module=new_module, new_leaf=new_leaf)


async def _adopt(handoff, environment=None):
    environment = environment or LocalEnvironment(str(handoff.source))
    try:
        response = await AdoptTool(require_process_isolation=False).execute_with_runtime(
            {"sha": handoff.target}, ToolRuntime(environment, None, None),
        )
        executed = await environment.exec_cmd(shlex.join((sys.executable, "-B", "app.py")))
        return response, executed
    finally:
        await environment.cleanup()


@pytest.mark.parametrize("active", [True, False])
async def test_adopt_updates_initialized_submodule_to_the_target_gitlink(handoff, monkeypatch, active):
    _git(handoff.source, "config", "submodule.library.active", str(active).lower())
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "")
    response, executed = await _adopt(handoff)
    assert not response.startswith("Error"), response
    assert _git(handoff.source, "rev-parse", "HEAD") == handoff.target
    assert _git(handoff.module, "rev-parse", "HEAD") == handoff.new_module
    assert executed.returncode == 0 and executed.stdout == "42\n"
    assert _git(handoff.source, "status", "--porcelain=v1") == ""


@pytest.mark.parametrize("handoff", [('vendor/\u5e93 "quoted"\npath', False)], indirect=True)
@pytest.mark.parametrize("backend", ["local", "container"])
async def test_adopt_decodes_git_quoted_submodule_paths(handoff, backend, worktree_transport):
    environment = (
        attach_container(container_id=CONTAINER_ID, workspace=str(handoff.source))
        if backend == "container" else None
    )
    response, executed = await _adopt(handoff, environment)
    assert not response.startswith("Error"), response
    assert _git(handoff.module, "rev-parse", "HEAD") == handoff.new_module
    assert executed.returncode == 0 and executed.stdout == "42\n"


@pytest.mark.parametrize("handoff", [("vendor/library", True)], indirect=True)
async def test_adopt_updates_nested_initialized_submodules(handoff, monkeypatch):
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "")
    response, executed = await _adopt(handoff)
    assert not response.startswith("Error"), response
    assert _git(handoff.module, "rev-parse", "HEAD") == handoff.new_module
    assert _git(handoff.module / "inner/leaf", "rev-parse", "HEAD") == handoff.new_leaf
    assert executed.returncode == 0 and executed.stdout == "42\n"


async def test_adopt_submodule_simulation_ignores_git_display_preferences(handoff):
    for key, value in (("color.ui", "always"), ("diff.noprefix", "true"), ("diff.context", "0"),
                       ("diff.srcPrefix", "before/"), ("diff.dstPrefix", "after/")):
        _git(handoff.module, "config", key, value)
    response, executed = await _adopt(handoff)
    assert not response.startswith("Error"), response
    assert _git(handoff.module, "rev-parse", "HEAD") == handoff.new_module
    assert executed.returncode == 0 and executed.stdout == "42\n"


@pytest.mark.parametrize("module_state", ["uninitialized", "missing-object", "new-module"])
async def test_adopt_refuses_unavailable_submodule_before_changing_the_project(handoff, module_state, monkeypatch):
    if module_state == "uninitialized":
        _git(handoff.source, "submodule", "deinit", "-f", "--", "vendor/library")
    elif module_state == "missing-object":
        (handoff.dependency / "answer.txt").write_text("99\n")
        _git(handoff.dependency, "commit", "-qam", "Unavailable dependency")
        handoff.new_module = _git(handoff.dependency, "rev-parse", "HEAD")
        _git(handoff.source, "update-index", "--cacheinfo", f"160000,{handoff.new_module},vendor/library")
        (handoff.source / "version.txt").write_text("candidate\n")
        _git(handoff.source, "add", "version.txt")
        _git(handoff.source, "commit", "-qm", "Reference unavailable dependency")
        handoff.target = _git(handoff.source, "rev-parse", "HEAD")
        _git(handoff.source, "checkout", "-q", handoff.start)
    else:
        _git(handoff.source, "checkout", "-q", handoff.target)
        _git(handoff.source, "update-index", "--add", "--cacheinfo",
             f"160000,{handoff.new_module},vendor/new-library")
        _git(handoff.source, "commit", "-qm", "Add unavailable dependency")
        handoff.target = _git(handoff.source, "rev-parse", "HEAD")
        _git(handoff.source, "checkout", "-q", handoff.start)
    index = (handoff.source / ".git/index").read_bytes()
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "")
    response, _executed = await _adopt(handoff)
    assert response.startswith("Error"), response
    assert _git(handoff.source, "rev-parse", "HEAD") == handoff.start
    assert (handoff.source / "version.txt").read_text() == "old\n"
    assert (handoff.source / ".git/index").read_bytes() == index
    if module_state == "uninitialized":
        assert not (handoff.module / ".git").exists()
    else:
        assert _git(handoff.module, "rev-parse", "HEAD") == handoff.old_module


@pytest.mark.parametrize("mutation", ["dirty", "staged", "untracked", "ignored", "root"])
async def test_adopt_keeps_uncommitted_source_and_submodule_changes(handoff, mutation):
    changed = handoff.module / {"untracked": "added.txt", "ignored": "settings.local"}.get(mutation, "answer.txt")
    if mutation == "root":
        changed = handoff.source / "version.txt"
    changed.write_text("user modification\n")
    if mutation == "staged":
        _git(handoff.module, "add", "answer.txt")
    index = (handoff.source / ".git/index").read_bytes()
    module_index = _git(handoff.module, "ls-files", "--stage")
    response, _executed = await _adopt(handoff)
    assert response.startswith("Error"), response
    assert _git(handoff.source, "rev-parse", "HEAD") == handoff.start
    assert _git(handoff.module, "rev-parse", "HEAD") == handoff.old_module
    assert changed.read_text() == "user modification\n"
    assert (handoff.source / ".git/index").read_bytes() == index
    assert _git(handoff.module, "ls-files", "--stage") == module_index


async def test_adopt_preserves_unrelated_submodule_user_changes(handoff):
    note = handoff.module / "user-note.txt"
    note.write_text("user note\n")
    response, executed = await _adopt(handoff)
    assert not response.startswith("Error"), response
    assert _git(handoff.module, "rev-parse", "HEAD") == handoff.new_module
    assert executed.returncode == 0 and executed.stdout == "42\n"
    assert note.read_text() == "user note\n"


async def test_adopt_keeps_submodule_filters_from_running(handoff, tmp_path):
    marker = tmp_path / "filter-ran"
    _git(handoff.module, "config", "filter.probe.smudge", f"touch {shlex.quote(str(marker))}; cat")
    _git(handoff.module, "config", "core.attributesFile", str(tmp_path / "attributes"))
    (tmp_path / "attributes").write_text("answer.txt filter=probe\n")
    response, _executed = await _adopt(handoff)
    assert response.startswith("Error"), response
    assert _git(handoff.source, "rev-parse", "HEAD") == handoff.start
    assert _git(handoff.module, "rev-parse", "HEAD") == handoff.old_module
    assert not marker.exists()


async def test_adopt_suppresses_submodule_checkout_hooks(handoff, tmp_path):
    marker = tmp_path / "hook-ran"
    hook = Path(_git(handoff.module, "rev-parse", "--absolute-git-dir")) / "hooks/post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\n")
    hook.chmod(0o755)
    response, executed = await _adopt(handoff)
    assert not response.startswith("Error"), response
    assert executed.returncode == 0 and executed.stdout == "42\n"
    assert not marker.exists()


async def test_adopt_removing_a_module_preserves_its_untracked_user_files(handoff):
    _git(handoff.source, "rm", "--cached", "vendor/library")
    _git(handoff.source, "commit", "-qm", "Remove dependency")
    handoff.target = _git(handoff.source, "rev-parse", "HEAD")
    _git(handoff.source, "checkout", "-q", handoff.start)
    notes = [handoff.module / "user-note.txt", handoff.module / "settings.local"]
    for note in notes:
        note.write_text("user note\n")
    response, _executed = await _adopt(handoff)
    for note in notes:
        assert note.read_text() == "user note\n"
    expected = handoff.start if response.startswith("Error") else handoff.target
    assert _git(handoff.source, "rev-parse", "HEAD") == expected


@pytest.mark.parametrize("module_state", ["filter", "user-edit"])
async def test_adopt_ordinary_file_keeps_an_unchanged_submodule(handoff, tmp_path, module_state):
    (handoff.source / "version.txt").write_text("candidate\n")
    _git(handoff.source, "commit", "-qam", "Update ordinary project file")
    handoff.target = _git(handoff.source, "rev-parse", "HEAD")
    _git(handoff.source, "checkout", "-q", handoff.start)
    marker = tmp_path / "filter-ran"
    if module_state == "filter":
        _git(handoff.module, "config", "filter.probe.smudge", f"touch {shlex.quote(str(marker))}; cat")
        metadata = Path(_git(handoff.module, "rev-parse", "--absolute-git-dir"))
        (metadata / "info/attributes").write_text("answer.txt filter=probe\n")
    else:
        (handoff.module / "answer.txt").write_text("user edit\n")
    expected = (handoff.module / "answer.txt").read_bytes()
    response, _executed = await _adopt(handoff)
    assert not response.startswith("Error"), response
    assert _git(handoff.source, "rev-parse", "HEAD") == handoff.target
    assert _git(handoff.module, "rev-parse", "HEAD") == handoff.old_module
    assert (handoff.module / "answer.txt").read_bytes() == expected
    assert not marker.exists()


@pytest.mark.parametrize("module_state", ["uninitialized", "own-commit"])
async def test_adopt_ordinary_file_keeps_unmodified_gitlinks(handoff, module_state):
    (handoff.source / "version.txt").write_text("candidate\n")
    _git(handoff.source, "commit", "-qam", "Update ordinary project file")
    handoff.target = _git(handoff.source, "rev-parse", "HEAD")
    _git(handoff.source, "checkout", "-q", handoff.start)
    if module_state == "uninitialized":
        _git(handoff.source, "submodule", "deinit", "-f", "--", "vendor/library")
        expected_head = None
    else:
        (handoff.module / "answer.txt").write_text("user commit\n")
        _git(handoff.module, "-c", "user.name=OpenCollab Tests", "-c", "user.email=tests@example.invalid",
             "commit", "-qam", "Save module work")
        expected_head = _git(handoff.module, "rev-parse", "HEAD")
    response, _executed = await _adopt(handoff)
    assert not response.startswith("Error"), response
    assert _git(handoff.source, "rev-parse", "HEAD") == handoff.target
    if expected_head is None:
        assert not (handoff.module / ".git").exists()
    else:
        assert _git(handoff.module, "rev-parse", "HEAD") == expected_head
        assert (handoff.module / "answer.txt").read_text() == "user commit\n"


def _save_module_target(handoff):
    _git(handoff.module, "add", "--all")
    _git(handoff.module, "-c", "user.name=OpenCollab Tests", "-c", "user.email=tests@example.invalid",
         "commit", "-qm", "Update dependency")
    handoff.new_module = _git(handoff.module, "rev-parse", "HEAD")
    _git(handoff.source, "add", "vendor/library", "app.py")
    _git(handoff.source, "commit", "-qm", "Update project dependency")
    handoff.target = _git(handoff.source, "rev-parse", "HEAD")
    _git(handoff.source, "checkout", "-q", handoff.start)
    _git(handoff.module, "checkout", "-q", handoff.old_module)


async def test_adopt_case_only_module_rename_keeps_unrelated_ignored_files(handoff):
    _git(handoff.module, "checkout", "-q", handoff.new_module)
    _git(handoff.module, "mv", "--force", "answer.txt", "Answer.txt")
    (handoff.source / "app.py").write_text(
        "from pathlib import Path\nprint(Path('vendor/library/Answer.txt').read_text().strip())\n"
    )
    _save_module_target(handoff)
    metadata = Path(_git(handoff.module, "rev-parse", "--absolute-git-dir"))
    (metadata / "info/exclude").write_text("other.cache\n")
    note = handoff.module / "other.cache"
    note.write_text("user note\n")
    response, executed = await _adopt(handoff)
    assert not response.startswith("Error"), response
    assert _git(handoff.module, "rev-parse", "HEAD") == handoff.new_module
    assert executed.returncode == 0 and executed.stdout == "42\n"
    assert note.read_text() == "user note\n"


@pytest.mark.parametrize("structure", ["ignored-file-ancestor", "untracked-file-ancestor", "ignored-directory"])
async def test_adopt_preserves_ignored_paths_at_file_directory_boundaries(handoff, structure):
    _git(handoff.module, "checkout", "-q", handoff.new_module)
    if structure.endswith("file-ancestor"):
        (handoff.module / "folder").mkdir()
        (handoff.module / "folder/added.txt").write_text("candidate\n")
    else:
        (handoff.module / "settings.local").write_text("candidate boundary\n")
    _save_module_target(handoff)
    metadata = Path(_git(handoff.module, "rev-parse", "--absolute-git-dir"))
    if structure == "ignored-file-ancestor":
        (metadata / "info/exclude").write_text("folder\n")
    if structure.endswith("file-ancestor"):
        note = handoff.module / "folder"
    else:
        (handoff.module / "settings.local").mkdir()
        note = handoff.module / "settings.local/user-note.txt"
    note.write_text("user data\n")
    response, _executed = await _adopt(handoff)
    assert response.startswith("Error"), response
    assert _git(handoff.source, "rev-parse", "HEAD") == handoff.start
    assert _git(handoff.module, "rev-parse", "HEAD") == handoff.old_module
    assert note.read_text() == "user data\n"


async def test_adopt_preserves_git_promisor_fetch_for_ordinary_files(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    source = make_repo(tmp_path / "source")
    for name in ("a", "b"):
        (source / f"{name}.txt").write_text(f"old {name}\n")
    (source / "app.py").write_text(
        "from pathlib import Path\nprint(Path('a.txt').read_text().strip())\n"
        "print(Path('b.txt').read_text().strip())\n"
    )
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "Prepare ordinary project")
    start = _git(source, "rev-parse", "HEAD")
    for name in ("a", "b"):
        (source / f"{name}.txt").write_text(f"candidate {name}\n")
    _git(source, "commit", "-qam", "Update ordinary files")
    target = _git(source, "rev-parse", "HEAD")
    blob = _git(source, "rev-parse", "HEAD:b.txt")
    _git(source, "config", "uploadpack.allowFilter", "true")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "--filter=blob:none", "--no-checkout", source.as_uri(), str(clone))
    _git(clone, "config", "protocol.file.allow", "always")
    _git(clone, "checkout", "-q", "--detach", start)
    _git(clone, "cat-file", "-p", target + ":a.txt")
    unavailable = subprocess.run(
        ["git", "-C", str(clone), "cat-file", "-e", blob],
        env={**os.environ, "GIT_ALLOW_PROTOCOL": ""}, capture_output=True,
    )
    assert unavailable.returncode != 0
    response, executed = await _adopt(SimpleNamespace(source=clone, target=target))
    assert not response.startswith("Error"), response
    assert _git(clone, "rev-parse", "HEAD") == target
    assert executed.returncode == 0 and executed.stdout == "candidate a\ncandidate b\n"


async def test_adopt_streams_plain_repository_tree_beyond_the_capture_limit(tmp_path, monkeypatch):
    from opencollab.adapters import _env_local

    source = make_repo(tmp_path / "source")
    for number in range(100):
        (source / (f"file{number:04d}_" + "x" * 80)).write_text("content\n")
    (source / "app.py").write_text("print(1)\n")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "Prepare ordinary repository")
    start = _git(source, "rev-parse", "HEAD")
    (source / "app.py").write_text("print(42)\n")
    _git(source, "commit", "-qam", "Update ordinary file")
    target = _git(source, "rev-parse", "HEAD")
    _git(source, "checkout", "-q", start)
    monkeypatch.setattr(_env_local, "PROCESS_OUTPUT_CAPTURE_BYTES", 4096)
    environment = LocalEnvironment(str(source))
    try:
        assert (await environment.exec_cmd("git ls-tree -r HEAD")).stdout_truncated
        response = await AdoptTool(require_process_isolation=False).execute_with_runtime(
            {"sha": target}, ToolRuntime(environment, None, None),
        )
        assert not response.startswith("Error"), response
        assert _git(source, "rev-parse", "HEAD") == target
        executed = await environment.exec_cmd(shlex.join((sys.executable, "-B", "app.py")))
        assert executed.returncode == 0 and executed.stdout == "42\n"
    finally:
        await environment.cleanup()
