from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from opencollab.adapters._env_local import LocalEnvironment
from opencollab.adapters.candidate_workspace import EnvCandidateWorkspace
from opencollab.adapters.worktree_pool import WorktreePool
from opencollab.application.workflow import WorkflowContext
from tests.runtime.test_worktree_submodule_physical_delivery import _owned_source
from tests.workflows.test_candidate_physical_submodules import _index
from tests.workflows.test_workflow_candidate_workspace import _git


def _source(tmp_path, nested, converted, location="module", suffix=""):
    source, module_path, save = _owned_source(tmp_path, nested, "none")
    (save / "events.jsonl").unlink()
    save = (source / "runs/current" if location == "root" else save) / suffix
    save.mkdir(parents=True, exist_ok=True)
    (save / "session.json").write_text("started\n")
    if converted:
        (source / ".gitattributes").write_text("*.py text eol=lf\n")
        _git(source, "add", ".gitattributes")
        _git(source, "commit", "-qm", "Configure source text conversion")
    return source, module_path, save


@pytest.mark.parametrize("entry", ["port", "team", "workflow"])
@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("converted", [False, True], ids=["plain", "converted"])
async def test_candidate_owned_submodule_output(tmp_path, entry, nested, converted, suffix="", location="module"):
    source, module_path, save = _source(tmp_path, nested, converted, location, suffix)
    indexes = (_index(source), _index(source / module_path))
    worktrees = _git(source, "worktree", "list", "--porcelain")
    environment = LocalEnvironment(str(source))
    backend = EnvCandidateWorkspace(environment, owned_save_dir=str(save))
    pool = WorktreePool(str(source), use_worktrees=True, owned_save_dir=str(save), enable_adoption=True)
    lease = None
    folders = []

    def execute(folder):
        folders.append(folder)
        assert not (folder / save.relative_to(source) / "session.json").exists()
        (folder / "app.py").write_text(
            "from pathlib import Path\nimport subprocess\n"
            f"Path({str(save / 'session.json')!r}).write_text('finished\\n')\n"
            f'subprocess.run(["./{module_path}/helper.sh"], check=True)\n',
        )
        tested = subprocess.run(
            [sys.executable, "-B", "app.py"], cwd=folder,
            text=True, capture_output=True, check=True,
        )
        assert tested.stdout == "42\n"
        return "passed"

    try:
        if entry == "workflow":
            context = WorkflowContext(object(), candidate_workspace=backend)

            async def run(child, _inputs):
                return execute(Path(child.workspace_root))

            result = await context.candidate_workflow(run, {}, label="owned-submodule")
            assert result.output == "passed"
            patch = result.diff
            await context.adopt_candidate(result)
        elif entry == "team":
            candidate = await pool.acquire("coder")
            execute(Path(candidate.workspace))
            patch = await candidate.get_diff()
            await pool.adopt(patch)
        else:
            assert await backend.source_diff() == ""
            lease = await backend.acquire("owned-submodule")
            execute(Path(lease.candidate_workspace))
            patch = await lease.capture_changes()
            await backend.adopt(patch)
        assert [line for line in patch.splitlines() if line.startswith("diff --git")] == [
            "diff --git a/app.py b/app.py",
        ]
        replayed = subprocess.run(
            [sys.executable, "-B", "app.py"], cwd=source,
            text=True, capture_output=True, check=True,
        )
        assert replayed.stdout == "42\n"
        assert (_index(source), _index(source / module_path)) == indexes
    finally:
        if lease is not None:
            await lease.cleanup()
        await pool.release()
        await environment.cleanup()
    assert folders and all(not folder.exists() for folder in folders)
    assert (save / "session.json").read_text() == "finished\n"
    assert _git(source, "worktree", "list", "--porcelain") == worktrees


async def test_candidate_owned_submodule_path_preserves_quotes(tmp_path):
    await test_candidate_owned_submodule_output(tmp_path, "port", True, False, suffix="trace 'events'")


async def test_candidate_root_owned_output_control(tmp_path):
    await test_candidate_owned_submodule_output(tmp_path, "port", False, False, location="root")


@pytest.mark.parametrize("entry", ["port", "team"])
@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("mutation", ["tracked", "outside-owned", "commit"])
async def test_candidate_owned_submodule_output_keeps_source_changes_visible(tmp_path, entry, nested, mutation):
    source, module_path, save = _source(tmp_path, nested, False)
    changed = save / "tracked.txt" if mutation != "outside-owned" else save.parent / "current-other.txt"
    changed.write_text("source change\n")
    if mutation == "commit":
        _git(source / module_path, "add", "runs/current/tracked.txt")
        _git(source / module_path, "-c", "user.name=Test User", "-c", "user.email=test@example.invalid",
             "commit", "-qm", "Save source dependency change")
    indexes = (_index(source), _index(source / module_path))
    worktrees = _git(source, "worktree", "list", "--porcelain")
    environment = LocalEnvironment(str(source))
    backend = EnvCandidateWorkspace(environment, owned_save_dir=str(save))
    pool = WorktreePool(str(source), use_worktrees=True, owned_save_dir=str(save), enable_adoption=True)
    try:
        with pytest.raises(RuntimeError, match="submodule"):
            if entry == "team":
                await pool.acquire("coder")
            else:
                await backend.acquire("changed-source")
        assert changed.read_text() == "source change\n"
        assert (save / "session.json").read_text() == "started\n"
        assert (_index(source), _index(source / module_path)) == indexes
        assert _git(source, "worktree", "list", "--porcelain") == worktrees
    finally:
        await pool.release()
        await environment.cleanup()


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
async def test_candidate_owned_source_output_does_not_hide_candidate_module_changes(tmp_path, nested):
    source, module_path, save = _source(tmp_path, nested, False)
    environment = LocalEnvironment(str(source))
    backend = EnvCandidateWorkspace(environment, owned_save_dir=str(save))
    lease = await backend.acquire("candidate-output")
    folder = Path(lease.candidate_workspace)
    record = folder / save.relative_to(source) / "session.json"
    record.write_text("candidate output\n")
    try:
        with pytest.raises(RuntimeError, match="submodule changes"):
            await lease.capture_changes()
        assert lease.cleaned is False
        assert folder.is_dir()
        assert record.read_text() == "candidate output\n"
        assert (save / "session.json").read_text() == "started\n"
    finally:
        await lease.cleanup()
        await environment.cleanup()
    assert not folder.exists()
