from __future__ import annotations

import asyncio
import subprocess
import sys
from types import SimpleNamespace

import pytest

from opencollab.application.workflow import WorkflowContext
from opencollab.application.workflow_candidates import CandidateRun


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("failure", [False, True])
async def test_candidate_adoption_finishes_before_propagating_repeated_cancellation(
    tmp_path, modern, failure,
):
    entered = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()
    selected = CandidateRun("selected", "tested", "complete patch", (), ())
    calls = []

    async def deliver(candidate, preserve_paths):
        calls.append((candidate, preserve_paths))
        (tmp_path / "app.py").write_text("from dependency import VALUE\nprint(VALUE)\n")
        entered.set()
        await release.wait()
        (tmp_path / "dependency.py").write_text("VALUE = 42\n")
        completed.set()
        if failure:
            raise OSError("delivery cleanup failed")

    backend = SimpleNamespace(**{"adopt_run" if modern else "adopt": deliver})
    context = WorkflowContext(object(), candidate_workspace=backend)
    operation = asyncio.create_task(context.adopt_candidate(selected, preserve_paths=("tests",)))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        for _ in range(2):
            operation.cancel()
            await asyncio.sleep(0)
        assert not operation.done()
        assert not completed.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await asyncio.wait_for(operation, 10)
        assert completed.is_set()
        assert calls == [(selected if modern else selected.diff, ("tests",))]
        if failure and sys.version_info >= (3, 11):
            assert any("delivery cleanup failed" in note for note in getattr(cancelled.value, "__notes__", ()))
        executed = subprocess.run(
            [sys.executable, "-B", "app.py"], cwd=tmp_path, capture_output=True, check=True,
        )
        assert executed.stdout == b"42\n"
    finally:
        release.set()
        await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.parametrize("modern", [False, True])
async def test_candidate_adoption_preserves_uncancelled_backend_failure(modern):
    failure = OSError("delivery failed")

    async def deliver(candidate, preserve_paths):
        raise failure

    backend = SimpleNamespace(**{"adopt_run" if modern else "adopt": deliver})
    context = WorkflowContext(object(), candidate_workspace=backend)
    selected = CandidateRun("selected", "tested", "complete patch", (), ())
    with pytest.raises(OSError) as raised:
        await context.adopt_candidate(selected)
    assert raised.value is failure
