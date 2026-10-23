import asyncio
import os

import pytest

from opencollab import OpenCollab
from opencollab.adapters.env import LocalEnvironment
from opencollab.bootstrap import agent_runtime, programmatic
from opencollab.domain.agent import Agent


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_failed_session_construction_releases_only_owned_environment(tmp_path, monkeypatch, owned, cancelled):
    environment = LocalEnvironment(str(tmp_path))
    descriptor = environment._workspace_fd
    failure = asyncio.CancelledError("construction cancelled") if cancelled else ValueError("invalid model settings")

    def fail_construction(**kwargs):
        raise failure

    monkeypatch.setattr(agent_runtime, "build_session", fail_construction)
    try:
        with pytest.raises(type(failure)) as caught:
            await agent_runtime.run_agent(
                agent=Agent(name="reader", system_prompt="Read."), environment=environment,
                prompt="Read.", max_tokens=100, max_steps=1, timeout_seconds=None,
                cleanup_timeout_seconds=1, transcript_path=None, cleanup_environment=owned,
            )
        assert caught.value is failure
        if owned:
            assert environment._workspace_fd is None
            with pytest.raises(OSError):
                os.fstat(descriptor)
        else:
            await environment.write_file("caller.txt", "usable")
            assert await environment.read_file("caller.txt") == "usable"
    finally:
        await environment.cleanup()


async def test_construction_failure_keeps_primary_error_when_cleanup_fails(tmp_path, monkeypatch):
    environment = LocalEnvironment(str(tmp_path))
    real_cleanup = environment.cleanup
    failure = ValueError("invalid model settings")

    def fail_construction(**kwargs):
        raise failure

    async def fail_cleanup():
        raise OSError("cleanup unavailable")

    monkeypatch.setattr(agent_runtime, "build_session", fail_construction)
    monkeypatch.setattr(environment, "cleanup", fail_cleanup)
    try:
        with pytest.raises(ValueError) as caught:
            await agent_runtime.run_agent(
                agent=Agent(name="reader", system_prompt="Read."), environment=environment,
                prompt="Read.", max_tokens=100, max_steps=1, timeout_seconds=None,
                cleanup_timeout_seconds=1, transcript_path=None, cleanup_environment=True,
            )
        assert caught.value is failure
        assert any("environment cleanup" in note for note in getattr(failure, "__notes__", ()))
    finally:
        await real_cleanup()


async def test_cancellation_during_construction_rollback_waits_for_cleanup(tmp_path, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    environment = LocalEnvironment(str(tmp_path))
    real_cleanup = environment.cleanup
    failure = ValueError("invalid model settings")

    def fail_construction(**kwargs):
        raise failure

    async def delayed_cleanup():
        started.set()
        await release.wait()
        await real_cleanup()

    monkeypatch.setattr(agent_runtime, "build_session", fail_construction)
    monkeypatch.setattr(environment, "cleanup", delayed_cleanup)
    owner = asyncio.create_task(agent_runtime.run_agent(
        agent=Agent(name="reader", system_prompt="Read."), environment=environment,
        prompt="Read.", max_tokens=100, max_steps=1, timeout_seconds=None,
        cleanup_timeout_seconds=2, transcript_path=None, cleanup_environment=True,
    ))
    try:
        await asyncio.wait_for(started.wait(), 1)
        owner.cancel()
        await asyncio.sleep(0)
        assert not owner.done()
        release.set()
        with pytest.raises(ValueError) as caught:
            await owner
        assert caught.value is failure
        assert environment._workspace_fd is None
    finally:
        release.set()
        await asyncio.gather(owner, return_exceptions=True)
        await real_cleanup()


async def test_trace_creation_failure_does_not_allocate_a_workspace(tmp_path, monkeypatch):
    allocated = []

    class ObservedEnvironment(LocalEnvironment):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            allocated.append(self)

    def fail_trace(**kwargs):
        raise OSError("trace storage unavailable")

    monkeypatch.setattr(programmatic, "LocalEnvironment", ObservedEnvironment)
    monkeypatch.setattr(programmatic, "Tracer", fail_trace)
    try:
        with pytest.raises(OSError, match="trace storage unavailable"):
            await OpenCollab(tmp_path, model="fixture").agent(
                "Read.", tools=(), artifacts=tmp_path / "artifacts", trace=True,
            )
        assert allocated == []
    finally:
        for environment in allocated:
            await environment.cleanup()
