from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from opencollab.adapters._env_local import LocalEnvironment
from opencollab.adapters.candidate_workspace import EnvCandidateWorkspace
from opencollab.application.workflow import WorkflowContext
from opencollab.application.workflow_candidates import CandidateCaptureError
from tests.support.worktree_delivery_support import git, make_repo


@pytest.mark.parametrize("attributes", ["source", "candidate", "none"])
async def test_physical_capture_retains_new_embedded_repository_contents(tmp_path, attributes):
    source = make_repo(tmp_path / "source")
    dependency = make_repo(tmp_path / "dependency")
    (dependency / "value.py").write_text("VALUE = 1\n")
    git(dependency, "add", ".")
    git(dependency, "commit", "-qm", "Prepare embedded dependency")
    if attributes == "source":
        (source / ".gitattributes").write_text("*.py text eol=lf\n")
        (source / "existing.py").write_text("VALUE = 1\n")
        git(source, "add", ".")
        git(source, "commit", "-qm", "Prepare source conversion")
    original_index = (source / ".git/index").read_bytes()
    environment = LocalEnvironment(str(source))
    leases = []

    class Workspace(EnvCandidateWorkspace):
        async def acquire(self, label):
            lease = await super().acquire(label)
            leases.append(lease)
            return lease

    context = WorkflowContext(object(), candidate_workspace=Workspace(environment))

    async def candidate(child, _args):
        root = Path(child.workspace_root)
        result = await leases[0].environment.exec_cmd(
            shlex.join(("git", "clone", "--quiet", str(dependency), "vendor/library")),
        )
        assert result.returncode == 0, result.stderr
        (root / "vendor/library/value.py").write_text("VALUE = 42\n")
        if attributes == "candidate":
            (root / ".gitattributes").write_text("*.py text eol=lf\n")
        (root / "app.py").write_text(
            'import runpy\nprint(runpy.run_path("vendor/library/value.py")["VALUE"])\n',
        )
        tested = await leases[0].environment.exec_cmd(shlex.join((sys.executable, "-B", "app.py")))
        assert (tested.returncode, tested.stdout) == (0, "42\n")
        return "tested embedded dependency"

    try:
        with pytest.raises(CandidateCaptureError, match="worktree preserved") as failure:
            await context.candidate_workflow(candidate, {}, label="embedded-dependency")
        assert "submodule changes cannot be delivered" in str(failure.value.__cause__)
        root = Path(leases[0].candidate_workspace)
        assert (root / "vendor/library/value.py").read_text() == "VALUE = 42\n"
        assert not (source / "app.py").exists()
        assert (source / ".git/index").read_bytes() == original_index
    finally:
        for lease in leases:
            await lease.cleanup()
        await environment.cleanup()
    assert git(source, "worktree", "list", "--porcelain").count("worktree ") == 1


async def test_physical_source_capture_rejects_embedded_gitlink_before_checkout(tmp_path):
    source = make_repo(tmp_path / "source")
    dependency = make_repo(tmp_path / "dependency")
    (source / ".gitattributes").write_text("*.py text eol=lf\n")
    (source / "existing.py").write_text("VALUE = 1\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "Prepare source conversion")
    git(source, "clone", "--quiet", str(dependency), "vendor/library")
    (source / "vendor/library/private.py").write_text("VALUE = 42\n")
    original_index = (source / ".git/index").read_bytes()
    original_worktrees = git(source, "worktree", "list", "--porcelain")
    environment = LocalEnvironment(str(source))
    try:
        with pytest.raises(RuntimeError, match="submodule changes cannot be delivered"):
            await EnvCandidateWorkspace(environment).acquire("embedded-source")
        assert (source / "vendor/library/private.py").read_text() == "VALUE = 42\n"
        assert (source / ".git/index").read_bytes() == original_index
        assert git(source, "worktree", "list", "--porcelain") == original_worktrees
    finally:
        await environment.cleanup()
