from __future__ import annotations

import asyncio
import errno
import os
import shutil
import tempfile
import threading
from pathlib import Path

import pytest

from opencollab.adapters.env import LocalEnvironment
from opencollab.bootstrap import SnapshotSessionError, build_session, snapshot_session
from opencollab.domain.agent import Agent
from tests.support.session_characterization_test_support import llm_response


class _ForkableEnvironment(LocalEnvironment):
    def __init__(self, workspace):
        super().__init__(str(workspace))
        self.forks = []
        self.cleanup_calls = 0

    def fork_snapshot(self):
        directory = tempfile.mkdtemp(prefix="snapshot-", dir=Path(self.workspace).parent)
        shutil.copytree(self.workspace, directory, dirs_exist_ok=True)
        branch = type(self)(directory)
        self.forks.append((branch, branch._workspace_fd))
        return branch

    async def cleanup(self):
        self.cleanup_calls += 1
        await super().cleanup()


class _LockedShaper:
    def __init__(self):
        self.lock = threading.Lock()

    def shape(self, messages):
        with self.lock:
            return list(messages)


class _Model:
    def __init__(self):
        self.close_calls = 0

    async def complete(self, messages, **kwargs):
        return llm_response(content="source still works")

    async def close(self):
        self.close_calls += 1


def _source(tmp_path, shaper):
    workspace = tmp_path / "source"
    workspace.mkdir()
    return build_session(
        agent=Agent(name="owner", system_prompt="Help the user."),
        env=_ForkableEnvironment(workspace), llm=_Model(), shaper=shaper,
    )


def _assert_closed(environment, descriptor):
    with pytest.raises(OSError) as caught:
        os.fstat(descriptor)
    assert caught.value.errno == errno.EBADF
    assert environment.cleanup_calls == 1
    assert environment._workspace_fd is None


async def _continue_source(source):
    assert source.env.cleanup_calls == 0
    assert source._llm.close_calls == 0
    await source.env.write_file("source.txt", "still open")
    assert await source.env.read_file("source.txt") == "still open"
    await source.add_user_message("Continue the source")
    assert await source.run_loop() == "source still works"


async def _cleanup(source):
    for environment, _descriptor in source.env.forks:
        await LocalEnvironment.cleanup(environment)
    await source.aclose()
    await source.env.cleanup()


@pytest.mark.parametrize("running_loop", [False, True])
def test_uncloneable_shaper_releases_each_snapshot_before_raising(tmp_path, running_loop):
    source = _source(tmp_path, _LockedShaper())

    def attempt():
        with pytest.raises(SnapshotSessionError, match="context shaper") as caught:
            snapshot_session(source)
        assert isinstance(caught.value.__cause__, TypeError)
        assert "lock" in str(caught.value.__cause__)
        _assert_closed(*source.env.forks[-1])

    async def scenario():
        for _ in range(3):
            attempt()
        await _continue_source(source)

    try:
        if running_loop:
            asyncio.run(scenario())
        else:
            for _ in range(3):
                attempt()
            asyncio.run(_continue_source(source))
        assert len(source.env.forks) == 3
    finally:
        asyncio.run(_cleanup(source))


@pytest.mark.parametrize("component", ["permission policy", "ask policy", "safety policy", "session state"])
async def test_uncloneable_components_fail_before_forking_the_environment(tmp_path, component):
    source = _source(tmp_path, None)
    if component == "permission policy":
        source.permission_policy = _LockedShaper()
    elif component == "ask policy":
        source.tool_execution.ask_policy = _LockedShaper()
    elif component == "safety policy":
        source.tool_execution.safety_policy = _LockedShaper()
    else:
        source.state.copy_lock = threading.Lock()
    try:
        with pytest.raises(SnapshotSessionError, match=component):
            snapshot_session(source)
        assert source.env.forks == []
        assert source.env.cleanup_calls == 0
        os.fstat(source.env._workspace_fd)
    finally:
        await _cleanup(source)


async def test_successful_snapshot_rebinds_every_shaper_owner(tmp_path):
    class Shaper:
        def shape(self, messages):
            return list(messages)

    shaper = Shaper()
    source = _source(tmp_path, shaper)
    owners = {
        "session": source,
        "state": source.state,
        "runner": source.runner,
        "tool_execution": source.tool_execution,
        "events": source.event_bus,
        "agent": source.agent,
        "environment": source.env,
        "model": source._llm,
        "summarizer": source._context_summarizer,
    }
    for name, owner in owners.items():
        setattr(shaper, name, owner)
    branch = None
    try:
        branch = snapshot_session(source)
        cloned = branch.runner.shaper
        assert cloned is not shaper
        assert cloned.session is branch
        assert cloned.state is branch.state
        assert cloned.runner is branch.runner
        assert cloned.tool_execution is branch.tool_execution
        assert cloned.events is branch.event_bus
        assert cloned.agent is branch.agent
        assert cloned.environment is branch.env
        assert cloned.model is source._llm
        assert cloned.summarizer is branch._context_summarizer
        assert branch.env.cleanup_calls == 0
        await branch.env.write_file("branch.txt", "branch output")
        assert not (Path(source.env.workspace) / "branch.txt").exists()
        await branch.add_user_message("Continue the branch")
        assert await branch.run_loop() == "source still works"
        assert source._llm.close_calls == 0
        await _continue_source(source)
    finally:
        if branch is not None:
            await branch.aclose()
        await _cleanup(source)


@pytest.mark.parametrize("failure", ["raises", "returns_source"])
async def test_invalid_environment_fork_preserves_the_source_owner(tmp_path, monkeypatch, failure):
    source = _source(tmp_path, None)

    def fork():
        if failure == "raises":
            raise OSError("snapshot workspace unavailable")
        return source.env

    monkeypatch.setattr(source.env, "fork_snapshot", fork)
    try:
        with pytest.raises(SnapshotSessionError, match="environment"):
            snapshot_session(source)
        assert source.env.forks == []
        await _continue_source(source)
    finally:
        await _cleanup(source)


async def test_cleanup_failure_keeps_the_clone_error_and_recovery_location(tmp_path, monkeypatch):
    source = _source(tmp_path, _LockedShaper())
    fork = source.env.fork_snapshot

    async def failed_cleanup():
        await asyncio.sleep(0)
        raise OSError("snapshot cleanup unavailable")

    def fork_with_failed_cleanup():
        environment = fork()
        environment.cleanup = failed_cleanup
        return environment

    monkeypatch.setattr(source.env, "fork_snapshot", fork_with_failed_cleanup)
    try:
        with pytest.raises(SnapshotSessionError, match="context shaper") as caught:
            snapshot_session(source)
        assert isinstance(caught.value.__cause__, TypeError)
        assert "lock" in str(caught.value.__cause__)
        notes = "\n".join(caught.value.__notes__)
        assert "OSError" in notes
        assert "snapshot cleanup unavailable" in notes
        assert source.env.forks[0][0].workspace in notes
        await _continue_source(source)
    finally:
        await _cleanup(source)
