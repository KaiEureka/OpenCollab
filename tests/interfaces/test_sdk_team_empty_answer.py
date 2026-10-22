from __future__ import annotations

import json
import os

import pytest

from opencollab import OpenCollab
from opencollab.adapters.llm.client import LLMClient
from opencollab.application.session_run import _EMPTY_STOP_PLACEHOLDER
from opencollab.bootstrap import load_session
from opencollab.domain.agent import Agent
from tests.support.provider_sdk_http import completion_http_response, install_sdk_transport
from tests.support.session_characterization_test_support import FakeLLMClient


@pytest.mark.parametrize("serialized", [False, True])
@pytest.mark.parametrize(
    ("provider", "wire_protocol", "replies", "max_steps", "expected", "status"),
    [
        pytest.param(provider, protocol, replies, steps, expected, status, id=f"{name}-{provider}-{protocol}")
        for provider, protocol in [
            ("openai", "chat_completions"), ("openai", "responses"), ("anthropic", "chat_completions"),
        ]
        for name, replies, steps, expected, status in [
            ("empty-twice", [None, None], 100, "", "completed"),
            ("empty-step-limit", [None], 1, None, "stopped"),
            ("retry-answer", [None, "The verified answer"], 100, "The verified answer", "completed"),
            ("direct-answer", ["The verified answer"], 100, "The verified answer", "completed"),
            ("same-text", [_EMPTY_STOP_PLACEHOLDER], 100, _EMPTY_STOP_PLACEHOLDER, "completed"),
            ("retry-same-text", [None, _EMPTY_STOP_PLACEHOLDER], 100, _EMPTY_STOP_PLACEHOLDER, "completed"),
        ]
        # Responses rejects empty output before the Session retry.
        if protocol != "responses" or replies[0] is not None
    ],
)
async def test_public_team_keeps_empty_retry_placeholder_out_of_answer(
    tmp_path, monkeypatch, serialized, provider, wire_protocol, replies, max_steps, expected, status,
):
    for key in tuple(os.environ):
        if key.startswith(("OPENCOLLAB_", "OPENAI_", "ANTHROPIC_", "DASHSCOPE_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("OPENCOLLAB_BUDGET_NUDGE_MODE", "off")
    monkeypatch.setenv("OPENCOLLAB_WRITE_NUDGE_MODE", "off")
    monkeypatch.setenv("OPENCOLLAB_LLM_MAX_RETRIES", "0")
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        reply = replies[len(requests) - 1]
        return completion_http_response(request, text=reply if provider == "openai" else reply or "")

    install_sdk_transport(monkeypatch, provider, handler)
    config = tmp_path / "team.yaml"
    config.write_text(
        "entry: lead\nroles:\n  lead:\n    prompt: Return the final task answer.\n"
        "    model: controlled-demo\n    tools: []\n",
        encoding="utf-8",
    )
    client = OpenCollab(
        tmp_path, model="controlled-demo", provider=provider,
        api_key="controlled-test",  # pragma: allowlist secret
        base_url="https://controlled.invalid/v1",
        config={"wire_protocol": wire_protocol, "llm_max_retries": 0},
    )
    result = await client.team(
        "Return the computed result.", config=config, budget=100_000,
        max_steps=max_steps, use_worktrees=False, serialize_turns=serialized,
        trace=False, timeout=5, artifacts=tmp_path / "artifacts",
    )

    assert result.output == expected
    assert result.status == status
    assert result.reason == ("step limit reached: 1 steps" if status == "stopped" else None)
    assert len(requests) == len(replies)
    assert result.tokens == 4 * len(replies)
    assert all("_opencollab_placeholder" not in json.dumps(request) for request in requests)
    if len(replies) == 2:
        assert _EMPTY_STOP_PLACEHOLDER in json.dumps(requests[1])
    snapshot_path = next((tmp_path / "artifacts").glob("agent_0_*.json"))
    snapshot = OpenCollab.read_session_snapshot(snapshot_path)
    placeholders = [message for message in snapshot["messages"] if message.get("_opencollab_placeholder") is True]
    assert len(placeholders) == int(replies[0] is None)
    assert all(message["content"] == _EMPTY_STOP_PLACEHOLDER for message in placeholders)
    restored = load_session(
        str(snapshot_path), agent=Agent(name="lead", system_prompt="Return the final task answer."),
        llm=FakeLLMClient(),
    )
    try:
        assert sum(message.get("_opencollab_placeholder") is True for message in restored.messages) == len(placeholders)
        if status == "completed":
            assert await restored.run_loop() == expected
    finally:
        await restored.aclose()

@pytest.mark.parametrize(
    ("provider", "wire_protocol"),
    [("openai", "chat_completions"), ("openai", "responses"), ("anthropic", "chat_completions")],
)
async def test_provider_request_omits_persisted_placeholder_origin(monkeypatch, provider, wire_protocol):
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        return completion_http_response(request, text="The verified answer")

    install_sdk_transport(monkeypatch, provider, handler)
    llm = LLMClient(
        model="controlled-demo", provider=provider, wire_protocol=wire_protocol,
        api_key="controlled-test", base_url="https://controlled.invalid/v1", max_retries=0,  # pragma: allowlist secret
    )
    messages = [
        {"role": "user", "content": "Return the computed result."},
        {"role": "assistant", "content": _EMPTY_STOP_PLACEHOLDER, "_opencollab_placeholder": True},
        {"role": "user", "content": "Continue with the answer."},
    ]
    try:
        result = await llm.complete(messages, temperature=0)
        assert result.content == "The verified answer"
        assert len(requests) == 1
        assert _EMPTY_STOP_PLACEHOLDER in json.dumps(requests[0])
        assert "_opencollab_placeholder" not in json.dumps(requests[0])
        assert messages[1]["_opencollab_placeholder"] is True
    finally:
        await llm.close()
