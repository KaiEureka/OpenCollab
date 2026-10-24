from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from opencollab.adapters.env import DockerEnvironment, LocalEnvironment
from opencollab.adapters.llm.client import LLMClient
from opencollab.adapters.llm.errors import TransientProviderError
from opencollab.adapters.llm.request_admission import request_slot
from opencollab.adapters.llm.types import LLMResponse, Usage
from opencollab.adapters.session_environments import SessionEnvironmentPool
from opencollab.adapters.tools.fs import FileWriteTool
from opencollab.application._session_run_shared import _TokenBudgetStop
from opencollab.application.ports import (
    ContextTokenEstimatorPort,
    RequestOutputRequirementPort,
    RequestTokenEstimatorPort,
)
from opencollab.application.tool_execution import ToolRuntime
from opencollab.bootstrap.authored_workflow_bridge import (
    AuthoredWorkflowBridge,
    BridgeClient,
    BridgeEnvironment,
    BridgeLLM,
)
from opencollab.bootstrap.session_factory import build_session
from opencollab.domain.agent import Agent


@pytest.mark.parametrize("docker", [False, True])
async def test_bridge_retains_native_whole_edit_lock(tmp_path, monkeypatch, docker):
    image = os.environ.get("OPENCOLLAB_DOCKER_TEST_IMAGE")
    if docker and not image:
        pytest.skip("local Docker image enables the native edit-lock probe")
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    environment = DockerEnvironment(image=image) if docker else LocalEnvironment(str(workspace))
    mailbox = LocalEnvironment(str(tmp_path))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    bridge = AuthoredWorkflowBridge(
        mailbox, str(artifacts), SessionEnvironmentPool(str(tmp_path), isolation="shared", base_environment=mailbox),
        {}, ToolRuntime(mailbox, None, None), allow_unisolated_shell=True,
    )
    bridge._environments[1] = environment
    client = BridgeClient(artifacts, bridge.options())
    proxy = BridgeEnvironment(client, {"handle": 1, "workspace": environment.workspace,
                                       "process_isolated": docker})
    native_read = environment.read_file

    async def snapshot_then_yield(path):
        value = await native_read(path)
        await asyncio.sleep(0.05)
        return value

    monkeypatch.setattr(environment, "read_file", snapshot_then_yield)
    stop = asyncio.Event()

    async def serve():
        while not stop.is_set():
            await bridge.poll()
            await asyncio.sleep(0.005)

    server = asyncio.create_task(serve())
    try:
        await environment.setup()
        for target in (environment, proxy):
            await environment.write_file("target.txt", "alpha\nbeta\n")
            runtime = ToolRuntime(target, None, None)
            replies = await asyncio.wait_for(asyncio.gather(*(
                FileWriteTool().execute_with_runtime(
                    {"mode": "str_replace", "path": "target.txt", "old_str": word, "new_str": word.upper()},
                    runtime,
                ) for word in ("alpha", "beta")
            )), 15)
            assert all("Replaced" in reply for reply in replies)
            assert await native_read("target.txt") == "ALPHA\nBETA\n"
        with pytest.raises(FileNotFoundError):
            async with proxy.file_write_lock("missing.txt"):
                await proxy.read_file("missing.txt")
    finally:
        stop.set()
        await server
        await bridge.aclose()
        await environment.cleanup()
        await mailbox.cleanup()


async def test_cancelled_bridge_edit_settles_native_write_and_releases_waiters(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    environment = LocalEnvironment(str(tmp_path))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    bridge = AuthoredWorkflowBridge(
        environment, str(artifacts),
        SessionEnvironmentPool(str(tmp_path), isolation="shared", base_environment=environment),
        {}, ToolRuntime(environment, None, None), allow_unisolated_shell=True,
    )
    client = BridgeClient(artifacts, bridge.options())
    proxy = BridgeEnvironment(client, {"handle": 0, "workspace": environment.workspace,
                                       "process_isolated": False})
    entered, finish, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()
    native_write = environment.write_file

    async def delayed_write(path, content):
        if path == "target.txt" and content == "late write":
            entered.set()
            await finish.wait()
        await native_write(path, content)

    monkeypatch.setattr(environment, "write_file", delayed_write)

    async def serve():
        while not stop.is_set():
            await bridge.poll()
            await asyncio.sleep(0.005)

    async def write():
        async with proxy.file_write_lock("target.txt"):
            await proxy.write_file("target.txt", "late write")

    async def queued():
        async with proxy.file_write_lock("target.txt"):
            pytest.fail("cancelled waiter acquired the edit lock")

    server = asyncio.create_task(serve())
    writing = asyncio.create_task(write())
    waiting = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        waiting = asyncio.create_task(queued())
        await asyncio.sleep(0.05)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiting, 2)
        writing.cancel()
        await asyncio.sleep(0.05)
        assert not writing.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(writing, 2)
        assert await environment.read_file("target.txt") == "late write"
        async with proxy.file_write_lock("target.txt"):
            await proxy.write_file("target.txt", "next edit")
        assert await environment.read_file("target.txt") == "next edit"
    finally:
        finish.set()
        for task in (writing, waiting):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (writing, waiting) if task is not None), return_exceptions=True)
        stop.set()
        await server
        await bridge.aclose()
        await environment.cleanup()


async def test_cancelled_bridge_lock_consumes_and_releases_already_published_acquisition(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    environment = LocalEnvironment(str(tmp_path))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    bridge = AuthoredWorkflowBridge(
        environment, str(artifacts),
        SessionEnvironmentPool(str(tmp_path), isolation="shared", base_environment=environment),
        {}, ToolRuntime(environment, None, None), allow_unisolated_shell=True,
    )
    client = BridgeClient(artifacts, bridge.options())
    proxy = BridgeEnvironment(client, {"handle": 0, "workspace": environment.workspace,
                                       "process_isolated": False})
    native_write = environment.write_file
    stop = asyncio.Event()

    async def publish_then_cancel(path, content):
        await native_write(path, content)
        if path.endswith("/bridge/1.json"):
            acquiring.cancel()

    monkeypatch.setattr(environment, "write_file", publish_then_cancel)

    async def acquire():
        async with proxy.file_write_lock("target.txt"):
            pytest.fail("cancelled acquisition entered its edit")

    async def serve():
        while not stop.is_set():
            await bridge.poll()
            await asyncio.sleep(0.005)

    acquiring = asyncio.create_task(acquire())
    server = asyncio.create_task(serve())
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(acquiring, 2)
        async with proxy.file_write_lock("target.txt"):
            await proxy.write_file("target.txt", "next edit")
        assert not bridge._edits
    finally:
        if not acquiring.done():
            acquiring.cancel()
        await asyncio.gather(acquiring, return_exceptions=True)
        stop.set()
        await server
        await bridge.aclose()
        await environment.cleanup()


async def test_bridge_retains_native_budget_protocols_and_thinking_output_floor(tmp_path):
    native = LLMClient(model="claude-sonnet-4-5", provider="anthropic",
                       api_key="offline-test")  # pragma: allowlist secret
    client = SimpleNamespace(
        options={"supports_response_session_identity": False, "supports_request_admission": False},
        metadata=lambda name, *args, **kwargs: getattr(native, name)(*args, **kwargs),
    )
    llm = BridgeLLM(client, None)
    thinking_params = {"thinking": {"type": "enabled", "budget_tokens": 1024}}
    environment = LocalEnvironment(str(tmp_path))
    session = build_session(
        agent=Agent(name="child", system_prompt="child", tools=[], model=native.model,
                    provider="anthropic", max_tokens_per_step=4096,
                    thinking=True, thinking_params=thinking_params),
        env=environment, llm=llm, max_budget_tokens=1024,
    )
    messages = [
        {"role": "user", "content": "Return an answer."},
        {"role": "assistant", "content": "Prior answer", "reasoning_content": "discarded " * 1000},
    ]
    try:
        configured, minimum, reserved = session.runner._request_budget_requirements(
            messages, None, thinking=True,
        )
        assert configured == 4096 and minimum == 1025
        assert reserved == native.estimate_request_tokens(messages, thinking=True, thinking_params=thinking_params)
        assert session.runner._estimate_context_message(messages[-1]) == native.estimate_context_tokens(
            [messages[-1]], thinking=True, thinking_params=thinking_params,
        )
        with pytest.raises(_TokenBudgetStop):
            session.runner._request_output_limit(messages, None, thinking=True)
        for protocol in (RequestTokenEstimatorPort, ContextTokenEstimatorPort, RequestOutputRequirementPort):
            assert isinstance(native, protocol) and isinstance(llm, protocol)
    finally:
        await session.aclose()
        await native.close()
        await environment.cleanup()


@pytest.mark.parametrize("error_usage", [False, True])
async def test_cancelled_bridge_call_waits_for_native_slot_and_retains_late_usage(tmp_path, monkeypatch, error_usage):
    monkeypatch.chdir(tmp_path)
    environment = LocalEnvironment(str(tmp_path))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pool_path = tmp_path / "request-pool"
    pool_path.mkdir()
    first_entered = asyncio.Event()
    first_cancelled = asyncio.Event()
    finish = asyncio.Event()
    admissions = []
    entered = 0

    class Model:
        supports_request_admission = True

        async def complete(self, messages, on_admitted=None):
            nonlocal entered
            async with request_slot(str(pool_path), 1):
                entered += 1
                index = entered
                if on_admitted is not None:
                    on_admitted()
                if index == 1:
                    first_entered.set()
                    try:
                        await finish.wait()
                    except asyncio.CancelledError:
                        first_cancelled.set()
                        await finish.wait()
                if error_usage and index == 1:
                    error = TransientProviderError("reported provider failure")
                    error.usage = Usage(7, 3)
                    raise error
                return LLMResponse(content="completed", usage=Usage(7, 3),
                                   provider_items=[{"type": "reasoning", "encrypted_content": "opaque"}],
                                   provider_state={"response_id": "native-response"})

        async def close(self):
            pass

    bridge = AuthoredWorkflowBridge(
        environment, str(artifacts), SessionEnvironmentPool(str(tmp_path), isolation="shared",
                                                          base_environment=environment),
        {"request_pool": str(pool_path)}, ToolRuntime(environment, None, None), allow_unisolated_shell=True,
    )
    bridge._llm = Model()
    client = BridgeClient(artifacts, bridge.options())
    llm = BridgeLLM(client, environment)
    stop = asyncio.Event()

    async def serve():
        while not stop.is_set():
            await bridge.poll()
            await asyncio.sleep(0.005)

    server = asyncio.create_task(serve())
    first = asyncio.create_task(llm.complete([], on_admitted=lambda: admissions.append("first")))
    second = None
    try:
        await asyncio.wait_for(first_entered.wait(), 2)
        first.cancel()
        await asyncio.wait_for(first_cancelled.wait(), 2)
        second = asyncio.create_task(llm.complete([], on_admitted=lambda: admissions.append("second")))
        await asyncio.sleep(0.1)
        assert not first.done() and not second.done()
        assert entered == 1 and "second" not in admissions
        finish.set()
        with pytest.raises(asyncio.CancelledError) as cancelled:
            await asyncio.wait_for(first, 2)
        assert cancelled.value.usage.total_tokens == 10
        response = await asyncio.wait_for(second, 2)
        assert response.provider_items[0]["encrypted_content"] == "opaque"
        assert response.provider_state["response_id"] == "native-response"
        assert bridge.tokens == 20 and entered == 2
    finally:
        finish.set()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)
        stop.set()
        await server
        await bridge.aclose()
        await environment.cleanup()


async def test_bridge_rejects_foreign_handles_endpoint_controls_and_source_paths(tmp_path):
    environment = LocalEnvironment(str(tmp_path))
    pool = SessionEnvironmentPool(str(tmp_path), isolation="shared", base_environment=environment)
    bridge = AuthoredWorkflowBridge(
        environment, str(tmp_path / "artifacts"), pool, {}, ToolRuntime(environment, None, None),
        allow_unisolated_shell=True,
    )
    try:
        with pytest.raises(ValueError, match="inside the member workspace"):
            AuthoredWorkflowBridge(environment, str(tmp_path.parent), pool, {}, None, allow_unisolated_shell=True)
        with pytest.raises(ValueError, match="unknown environment"):
            await bridge._dispatch("1", {"action": "exec_cmd", "environment": 99,
                                         "arguments": {"cmd": "touch foreign"}})
        with pytest.raises(ValueError, match="strategy and label"):
            await bridge._dispatch("2", {"action": "acquire", "arguments": {
                "strategy": "worktree", "label": "child", "workspace": str(tmp_path.parent),
            }})
        with pytest.raises(ValueError, match="unknown completion controls"):
            await bridge._dispatch("3", {"action": "complete", "arguments": {"base_url": "http://localhost"}})
        assert bridge._llm is None and not (tmp_path / "foreign").exists()
    finally:
        await bridge.aclose()
        await environment.cleanup()
