from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from opencollab.adapters.env import LocalEnvironment
from opencollab.adapters.llm.client import LLMClient
from opencollab.adapters.llm.errors import TransientProviderError
from opencollab.adapters.llm.request_admission import request_slot
from opencollab.adapters.llm.types import LLMResponse, Usage
from opencollab.adapters.session_environments import SessionEnvironmentPool
from opencollab.application._session_run_shared import _TokenBudgetStop
from opencollab.application.ports import (
    ContextTokenEstimatorPort,
    RequestOutputRequirementPort,
    RequestTokenEstimatorPort,
)
from opencollab.application.tool_execution import ToolRuntime
from opencollab.bootstrap.authored_workflow_bridge import AuthoredWorkflowBridge, BridgeClient, BridgeLLM
from opencollab.bootstrap.session_factory import build_session
from opencollab.domain.agent import Agent


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
