from __future__ import annotations

import asyncio
import gc
import itertools
import json
import os
import shutil
import tempfile
import threading
import weakref
from pathlib import Path

import httpx
import openai
import pytest

from opencollab.adapters.env import LocalEnvironment
from opencollab.adapters.llm.client import LLMClient
from opencollab.application.event_bus import EventBus
from opencollab.bootstrap import DefaultSessionFactory, SnapshotSessionError, build_session, container, snapshot_session
from opencollab.bootstrap.context_builder import SpawnConfig
from opencollab.bootstrap.team_config import RoleConfig, TeamConfig
from opencollab.domain.agent import Agent
from tests.support.session_characterization_test_support import llm_response


class _ForkableEnvironment(LocalEnvironment):
    def __init__(self, workspace):
        super().__init__(str(workspace))
        self.forks = []

    def fork_snapshot(self):
        directory = tempfile.mkdtemp(prefix="snapshot-", dir=Path(self.workspace).parent)
        shutil.copytree(self.workspace, directory, dirs_exist_ok=True)
        branch = type(self)(directory)
        self.forks.append(branch)
        return branch


class _NativeTraffic:
    def __init__(self, monkeypatch):
        self.requests = []
        self.clients = []
        self.close_calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        traffic = self
        native = openai.AsyncOpenAI

        class Transport(httpx.MockTransport):
            async def aclose(self):
                traffic.close_calls += 1
                await super().aclose()

        def client(**kwargs):
            sdk = native(**kwargs, http_client=httpx.AsyncClient(transport=Transport(self.respond)))
            self.clients.append(sdk)
            return sdk

        monkeypatch.setattr(openai, "AsyncOpenAI", client)

    async def respond(self, request):
        data = json.loads(request.content)
        self.requests.append(data)
        prompt = next(row["content"] for row in reversed(data["messages"]) if row["role"] == "user")
        if prompt == "HOLD_BRANCH":
            self.started.set()
            await self.release.wait()
        return httpx.Response(200, json={
            "id": f"chatcmpl-{len(self.requests)}", "object": "chat.completion", "created": 1,
            "model": "gpt-4o", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "ANSWER " + prompt}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        })


@pytest.fixture(autouse=True)
def _quiet_runtime(monkeypatch):
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", os.devnull)
    monkeypatch.setenv("OPENCOLLAB_BUDGET_NUDGE_MODE", "off")
    monkeypatch.setenv("OPENCOLLAB_WRITE_NUDGE_MODE", "off")


def _native_source(tmp_path, *, borrowed=False):
    workspace = tmp_path / "source"
    workspace.mkdir()
    environment = _ForkableEnvironment(workspace)
    config = SpawnConfig(
        model="gpt-4o", provider="openai", api_key="offline-test-key",  # pragma: allowlist secret
        base_url="http://127.0.0.1:9/v1", llm_timeout=3, llm_max_retries=0,
        tracer=None, event_bus=EventBus(), permission_policy=None, thinking=False,
    )
    factory = DefaultSessionFactory(
        config, team_cfg=TeamConfig(roles={"worker": RoleConfig(prompt="Return the marker.", tools=[])}),
        lead_workspace=str(workspace), lead_environment=environment,
    )
    if not borrowed:
        return factory.build_spawn_session(role="worker", env=environment, budget=100_000), None
    model = LLMClient(
        model="gpt-4o", provider="openai", api_key="offline-test-key",  # pragma: allowlist secret
        base_url="http://127.0.0.1:9/v1", max_retries=0, request_timeout=3,
    )
    agent = Agent(name="worker", system_prompt="Return the marker.", model="gpt-4o", thinking=False)
    return build_session(agent=agent, llm=model, env=environment, max_budget_tokens=100_000), model


async def _turn(session, marker):
    await session.add_user_message(marker)
    assert await session.run_loop() == "ANSWER " + marker


async def _cleanup(*sessions):
    for session in sessions:
        await session.aclose()
        await session.env.cleanup()


@pytest.mark.parametrize("borrowed", [False, True])
@pytest.mark.parametrize("first", ["source", "branch", "alive"])
async def test_native_snapshot_model_survives_either_session_close(tmp_path, monkeypatch, borrowed, first):
    traffic = _NativeTraffic(monkeypatch)
    source, external = _native_source(tmp_path, borrowed=borrowed)
    branch = None
    try:
        await _turn(source, "SOURCE_READY")
        branch = snapshot_session(source)
        assert branch._llm is source._llm
        assert len(traffic.clients) == 1
        await branch.env.write_file("marker.txt", "BRANCH_MARKER")
        await _turn(branch, "BRANCH_READY")
        if first == "alive":
            await _turn(source, "SOURCE_STILL_ALIVE")
            await _turn(branch, "BRANCH_STILL_ALIVE")
        else:
            closed, remaining = (source, branch) if first == "source" else (branch, source)
            await _cleanup(closed)
            assert not traffic.clients[0].is_closed()
            before = len(traffic.requests)
            await _turn(remaining, "AFTER_CLOSE")
            assert len(traffic.requests) == before + 1
            if first == "source":
                assert await branch.env.read_file("marker.txt") == "BRANCH_MARKER"
    finally:
        await _cleanup(*[session for session in (source, branch) if session is not None])
        if external is not None:
            assert not traffic.clients[0].is_closed()
            assert traffic.close_calls == 0
            await external.close()
    assert traffic.clients[0].is_closed()
    assert traffic.close_calls == 1
    assert all(session.env._workspace_fd is None for session in (source, branch))
    assert all(not session.pending_cleanup_tasks for session in (source, branch))


async def test_source_close_keeps_inflight_native_snapshot_request_alive(tmp_path, monkeypatch):
    traffic = _NativeTraffic(monkeypatch)
    source, _ = _native_source(tmp_path)
    branch = snapshot_session(source)
    running = asyncio.create_task(_turn(branch, "HOLD_BRANCH"))
    try:
        await asyncio.wait_for(traffic.started.wait(), 2)
        await _cleanup(source)
        assert not traffic.clients[0].is_closed()
        traffic.release.set()
        await asyncio.wait_for(running, 2)
        await _turn(branch, "AFTER_INFLIGHT")
        assert len(traffic.requests) == 2
    finally:
        traffic.release.set()
        await asyncio.gather(running, return_exceptions=True)
        await _cleanup(branch, source)
    assert traffic.clients[0].is_closed()
    assert traffic.close_calls == 1


class _Model:
    def __init__(self, *, error=None):
        self.close_calls = 0
        self.error = error

    async def complete(self, messages, **kwargs):
        assert self.close_calls == 0
        return llm_response(content="ANSWER " + messages[-1]["content"])

    async def close(self):
        self.close_calls += 1
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error


def _owned_source(tmp_path, monkeypatch, model):
    monkeypatch.setattr(container, "LLMClient", lambda **_kwargs: model)
    workspace = tmp_path / "source"
    workspace.mkdir()
    return build_session(
        agent=Agent(name="worker", system_prompt="Return the marker."),
        env=_ForkableEnvironment(workspace),
    )


@pytest.mark.parametrize("order", list(itertools.permutations(range(4))))
async def test_snapshot_siblings_and_descendants_close_model_after_last_owner(tmp_path, monkeypatch, order):
    model = _Model()
    source = _owned_source(tmp_path, monkeypatch, model)
    branch = snapshot_session(source)
    sessions = [source, branch, snapshot_session(source), snapshot_session(branch)]
    try:
        for index in order[:-1]:
            await sessions[index].aclose()
            await sessions[index].aclose()
            assert model.close_calls == 0
        await _turn(sessions[order[-1]], "LAST_OWNER")
        await sessions[order[-1]].aclose()
        assert model.close_calls == 1
    finally:
        await _cleanup(*sessions)
    assert model.close_calls == 1


async def test_concurrent_snapshot_closes_release_each_owner_once(tmp_path, monkeypatch):
    model = _Model()
    source = _owned_source(tmp_path, monkeypatch, model)
    branch = snapshot_session(source)
    descendant = snapshot_session(branch)
    try:
        await asyncio.gather(*(session.aclose() for session in (source, branch, source, branch)))
        assert model.close_calls == 0
        await _turn(descendant, "LAST_OWNER")
        await asyncio.gather(descendant.aclose(), descendant.aclose())
        assert model.close_calls == 1
    finally:
        await _cleanup(descendant, branch, source)


@pytest.mark.parametrize("failure", ["fork", "context"])
async def test_failed_snapshot_leaves_no_model_owner(tmp_path, monkeypatch, failure):
    model = _Model()
    source = _owned_source(tmp_path, monkeypatch, model)
    if failure == "fork":
        def fail_fork():
            raise OSError("fork failed")
        monkeypatch.setattr(source.env, "fork_snapshot", fail_fork)
    else:
        class LockedShaper:
            def __init__(self):
                self.lock = threading.Lock()
        source.runner.shaper = LockedShaper()
    try:
        with pytest.raises(SnapshotSessionError):
            snapshot_session(source)
        assert all(environment._workspace_fd is None for environment in source.env.forks)
        await source.aclose()
        assert model.close_calls == 1
    finally:
        await _cleanup(source)


async def test_live_snapshot_does_not_retain_closed_source_session(tmp_path, monkeypatch):
    model = _Model()
    source = _owned_source(tmp_path, monkeypatch, model)
    branch = snapshot_session(source)
    source_ref = weakref.ref(source)
    await _cleanup(source)
    del source
    gc.collect()
    descendant = None
    try:
        assert source_ref() is None
        assert model.close_calls == 0
        await _turn(branch, "SURVIVING_BRANCH")
        descendant = snapshot_session(branch)
        await _cleanup(branch)
        assert model.close_calls == 0
        await _turn(descendant, "SURVIVING_DESCENDANT")
    finally:
        await _cleanup(branch)
        if descendant is not None:
            await _cleanup(descendant)
    assert model.close_calls == 1


@pytest.mark.parametrize("error", [OSError("close failed"), asyncio.CancelledError("close cancelled")])
async def test_last_snapshot_close_retains_model_close_failure(tmp_path, monkeypatch, error):
    model = _Model(error=error)
    source = _owned_source(tmp_path, monkeypatch, model)
    branch = snapshot_session(source)
    try:
        await source.aclose()
        assert model.close_calls == 0
        for _ in range(2):
            with pytest.raises(type(error)) as caught:
                await branch.aclose()
            assert caught.value is error
        assert model.close_calls == 1
        await source.aclose()
    finally:
        await source.env.cleanup()
        await branch.env.cleanup()


@pytest.mark.parametrize("has_branch", [False, True])
async def test_snapshot_refuses_released_model_owner(tmp_path, monkeypatch, has_branch):
    model = _Model()
    source = _owned_source(tmp_path, monkeypatch, model)
    branch = snapshot_session(source) if has_branch else None
    try:
        await source.aclose()
        before = len(source.env.forks)
        with pytest.raises(SnapshotSessionError, match="closed"):
            snapshot_session(source)
        assert len(source.env.forks) == before
        if branch is not None:
            await _turn(branch, "SURVIVING_BRANCH")
    finally:
        await _cleanup(*[session for session in (source, branch) if session is not None])
    assert model.close_calls == 1
