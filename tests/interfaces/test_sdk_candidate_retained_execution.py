"""Retained candidate evidence outlives its completed execution resources."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from opencollab import OpenCollab
from opencollab.adapters.candidate_workspace import EnvCandidateWorkspace, _CandidateLease
from opencollab.application.workflow import WorkflowContext
from opencollab.application.workflow_candidates import (
    CandidateCaptureError,
    CandidateRun,
    CandidateWorkspaceTrackingError,
)
from opencollab.bootstrap import _workflow_runtime_session as runtime
from opencollab.tools import builtin_tools
from tests.interfaces.test_sdk_candidate_runtime_artifacts import EditingModel
from tests.support.environment_test_support import local_environment_factory as local_environment_factory
from tests.workflows.test_workflow_candidate_workspace import _git, _repository


@pytest.fixture
def candidate_leases(monkeypatch):
    leases = []
    acquire = EnvCandidateWorkspace.acquire

    async def remember(workspace, label):
        lease = await acquire(workspace, label)
        leases.append(lease)
        return lease

    monkeypatch.setattr(EnvCandidateWorkspace, "acquire", remember)
    return leases


async def _run(repo, entry, model):
    async def edit(ctx, _inputs):
        return await ctx.agent("edit source.py", tools=builtin_tools("file_write"))

    async def flow(ctx, inputs):
        if entry == "agent":
            return await ctx.candidate_agent("edit source.py", label="edit", tools=builtin_tools("file_write"))
        return await ctx.candidate_workflow(edit, inputs, label="edit")

    return await OpenCollab(repo, model="controlled-model").workflow(
        flow, llm=model, artifacts=repo / "records", trace=False,
        limit_mode="explicit", budget=10000, max_steps=4, timeout=20,
    )


async def _discard(leases):
    for lease in reversed(leases):
        await lease.environment.cleanup()
        if Path(lease.candidate_workspace).exists():
            _git(Path(lease.source_workspace), "worktree", "remove", "--force", lease.candidate_workspace)


@pytest.mark.parametrize("entry", ["agent", "workflow"])
@pytest.mark.parametrize("failure", ["tracked", "untracked", "head", "capture", "none"])
async def test_public_retained_candidate_closes_execution_and_preserves_recovery(
    tmp_path, monkeypatch, candidate_leases, entry, failure,
):
    repo = _repository(tmp_path)
    initial_index = (repo / ".git" / "index").read_bytes()
    initial_head = _git(repo, "rev-parse", "HEAD")

    def mutate():
        if failure == "head":
            _git(repo, "commit", "--allow-empty", "-m", "test: simulate concurrent user commit")
        elif failure in {"tracked", "untracked"}:
            (repo / ("source.py" if failure == "tracked" else "user.txt")).write_text("user edit\n")

    if failure == "capture":
        async def unavailable(_lease):
            raise OSError("candidate export temporarily unavailable")

        monkeypatch.setattr(_CandidateLease, "capture_changes", unavailable)
    try:
        result = await _run(repo, entry, EditingModel(mutate))
        lease, = candidate_leases
        workspace = Path(lease.candidate_workspace)
        assert lease.environment.revoked
        assert lease.environment._workspace_fd is None
        assert (repo / ".git" / "index").read_bytes() == initial_index
        if failure == "none":
            assert result.ok, result.reason
            assert "+value = 2" in result.output.diff
            assert not workspace.exists()
            return
        expected = CandidateCaptureError if failure == "capture" else CandidateWorkspaceTrackingError
        assert isinstance(result.error, expected), result.reason
        assert str(workspace) in result.reason
        assert (workspace / "source.py").read_text() == "value = 2\n"
        assert "+value = 2" in _git(workspace, "diff", "HEAD", "--", "source.py")
        assert _git(workspace, "show", "refs/worktree/opencollab-source:source.py") == "value = 1\n"
        assert str(workspace) in _git(repo, "worktree", "list", "--porcelain")
        assert not lease.cleaned
        assert (repo / "source.py").read_text() == ("user edit\n" if failure == "tracked" else "value = 1\n")
        if failure == "untracked":
            assert (repo / "user.txt").read_text() == "user edit\n"
        assert (_git(repo, "rev-parse", "HEAD") != initial_head) is (failure == "head")
    finally:
        await _discard(candidate_leases)


@pytest.mark.parametrize("entry", ["agent", "workflow"])
async def test_candidate_retention_waits_for_the_native_worker(
    tmp_path, monkeypatch, candidate_leases, entry,
):
    repo = _repository(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    build = runtime.WorkflowSessionFactory.build_workflow_session

    def with_worker(factory, **kwargs):
        session = build(factory, **kwargs)

        async def write_later():
            entered.set()
            await release.wait()
            await session.env.write_file("late.txt", "completed native write\n")

        task = asyncio.create_task(write_later())
        pending = session.tool_execution._pending_cleanup_tasks
        pending.add(task)
        task.add_done_callback(pending.discard)
        return session

    monkeypatch.setattr(runtime.WorkflowSessionFactory, "build_workflow_session", with_worker)
    running = asyncio.create_task(_run(
        repo, entry, EditingModel(lambda: (repo / "user.txt").write_text("user edit\n")),
    ))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        for _ in range(5):
            await asyncio.sleep(0)
        lease, = candidate_leases
        assert not running.done()
        assert not lease.environment.revoked
        await lease.environment.write_file("while-running.txt", "still available\n")
        release.set()
        result = await running
        assert isinstance(result.error, CandidateWorkspaceTrackingError)
        assert lease.environment._workspace_fd is None
        assert (Path(lease.candidate_workspace) / "late.txt").read_text() == "completed native write\n"
    finally:
        release.set()
        await asyncio.gather(running, return_exceptions=True)
        await _discard(candidate_leases)


async def test_retained_execution_cleanup_failure_preserves_the_error_and_can_retry(
    tmp_path, monkeypatch, candidate_leases,
):
    repo = _repository(tmp_path)
    cleanup = _CandidateLease.close_execution
    attempts = 0

    async def fail_once(lease):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("native candidate cleanup temporarily unavailable")
        await cleanup(lease)

    monkeypatch.setattr(_CandidateLease, "close_execution", fail_once)
    try:
        result = await _run(repo, "workflow", EditingModel(lambda: (repo / "user.txt").write_text("user edit\n")))
        lease, = candidate_leases
        assert isinstance(result.error, CandidateWorkspaceTrackingError)
        assert any("native candidate cleanup temporarily unavailable" in note for note in result.error.__notes__)
        assert not lease.environment.revoked
        assert (Path(lease.candidate_workspace) / "source.py").read_text() == "value = 2\n"
        await lease.close_execution()
        assert attempts == 2
        assert lease.environment._workspace_fd is None
        assert not lease.cleaned
        assert _git(
            Path(lease.candidate_workspace), "show", "refs/worktree/opencollab-source:source.py",
        ) == "value = 1\n"
    finally:
        await _discard(candidate_leases)


@pytest.mark.parametrize("failure", [False, True])
async def test_retention_keeps_execution_available_until_a_failure_is_quiescent(
    tmp_path, local_environment_factory, failure,
):
    repo = _repository(tmp_path)
    base = local_environment_factory(str(repo))
    lease = await EnvCandidateWorkspace(base).acquire("held")
    ctx = WorkflowContext(object())
    error = CandidateWorkspaceTrackingError("source changed") if failure else None
    candidate = None if failure else CandidateRun("held", "done", "", (), ())
    try:
        if failure:
            with pytest.raises(CandidateWorkspaceTrackingError, match="source changed"):
                await ctx._finish_candidate_run(
                    lease, candidate, error, label="held", preserve_lease=True, cleanup_quiesced=False,
                )
        else:
            assert await ctx._finish_candidate_run(
                lease, candidate, error, label="held", preserve_lease=True, cleanup_quiesced=True,
            ) is candidate
        assert not lease.environment.revoked
        await lease.environment.write_file("usable.txt", "continue\n")
        assert await lease.environment.read_file("usable.txt") == "continue\n"
    finally:
        await lease.cleanup()


async def test_candidate_workflow_keeps_execution_for_pending_isolation_cleanup(
    tmp_path, monkeypatch, candidate_leases,
):
    from tests.interfaces.test_sdk_candidate_minimal_lease import _WritingModel

    repo = _repository(tmp_path)
    isolated = []
    retained = []

    async def unavailable():
        raise OSError("isolated cleanup temporarily unavailable")

    async def edit(child, _inputs):
        await child.agent("generate", tools=builtin_tools("file_write"))
        await child.agent("inspect", isolation=True, tools=[])
        environment = child.sessions[-1].env
        isolated.append((environment, environment.cleanup))
        monkeypatch.setattr(environment, "cleanup", unavailable)
        return "done"

    async def flow(ctx, inputs):
        try:
            return await ctx.candidate_workflow(edit, inputs, label="held")
        finally:
            retained.append(not candidate_leases[0].environment.revoked)
            for environment, cleanup in isolated:
                monkeypatch.setattr(environment, "cleanup", cleanup)
            await ctx.release_isolated_workspaces()

    try:
        result = await OpenCollab(repo, model="controlled-model").workflow(
            flow, llm=_WritingModel("candidate data\n", mutate=lambda: (repo / "user.txt").write_text("user edit\n")),
            trace=False, limit_mode="explicit", budget=10000, max_steps=4, timeout=20,
        )
        outer = candidate_leases[0]
        assert isinstance(result.error, CandidateWorkspaceTrackingError), result.reason
        assert retained == [True]
        assert any("isolated cleanup temporarily unavailable" in note for note in result.error.__notes__)
        assert not outer.environment.revoked
        assert (Path(outer.candidate_workspace) / "generated.txt").read_text() == "candidate data\n"
        assert isolated[0][0].revoked
        await outer.environment.write_file("retry.txt", "available for recovery\n")
    finally:
        for environment, cleanup in isolated:
            monkeypatch.setattr(environment, "cleanup", cleanup)
        await _discard(candidate_leases)
