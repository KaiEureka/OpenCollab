"""Native Claude thinking modes reach the official SDK with valid model settings."""

from __future__ import annotations

import json
import os

import anthropic
import pytest

from opencollab import OpenCollab
from tests.support.provider_sdk_http import completion_http_response, http_response, install_sdk_transport


@pytest.fixture
def native_requests(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith(("OPENCOLLAB_", "ANTHROPIC_", "OPENAI_", "DASHSCOPE_")):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        mode = body.get("thinking", {}).get("type")
        effort = body.get("output_config", {}).get("effort")
        # https://platform.claude.com/docs/en/build-with-claude/thinking#configuring-thinking
        invalid = (
            (body["model"] in {"claude-sonnet-5-5", "claude-opus-5-5"} and mode == "disabled")
            or (mode == "between_tools" and (
                body["model"] != "claude-sonnet-5-5"
                or set(body["thinking"]) != {"type"}
                or effort in {"xhigh", "max"}
            ))
        )
        if invalid:
            return http_response(request, 400, json={
                "type": "error", "error": {"type": "invalid_request_error",
                    "message": "unsupported thinking mode for this model and effort"},
            })
        return completion_http_response(request)

    install_sdk_transport(monkeypatch, "anthropic", handler)
    return requests


def _app(tmp_path, model, config=None):
    return OpenCollab(
        tmp_path, provider="anthropic", model=model,
        api_key="controlled-test",  # pragma: allowlist secret
        base_url="https://controlled.invalid/v1",
        config={"llm_max_retries": 0, "max_output_tokens": 2048, **(config or {})},
    )


async def _native(model, thinking, effort=None):
    kwargs = {"thinking": thinking} if thinking else {}
    if effort is not None:
        kwargs["output_config"] = {"effort": effort}
    async with anthropic.AsyncAnthropic(
        api_key="controlled-test",  # pragma: allowlist secret
        base_url="https://controlled.invalid/v1", max_retries=0,
    ) as client:
        return await client.messages.create(
            model=model, max_tokens=2048,
            messages=[{"role": "user", "content": "Return ok"}], **kwargs,
        )


@pytest.mark.parametrize("model,mode", [
    ("claude-sonnet-5", "disabled"),
    ("claude-opus-5", "disabled"),
    ("claude-haiku-5-5", "disabled"),
    ("claude-sonnet-5-5", "between_tools"),
    ("claude-sonnet-4-6", None),
])
async def test_public_agent_default_matches_native_control(tmp_path, native_requests, model, mode):
    thinking = {"type": mode} if mode else None
    control = await _native(model, thinking)
    assert control.content[-1].text == "ok"
    result = await _app(tmp_path, model).agent(
        "Return ok", tools=(), trace=False, budget=10000, max_steps=1,
    )
    assert result.ok, result.error
    assert result.output == "ok" and len(native_requests) == 2
    assert native_requests[0].get("thinking") == native_requests[1].get("thinking") == thinking
    assert "output_config" not in native_requests[1]


@pytest.mark.parametrize("effort", [None, "low", "medium", "high"])
@pytest.mark.parametrize("source", ["native", "generic"])
async def test_sonnet_between_tools_matches_native_control(tmp_path, native_requests, effort, source):
    thinking = {"type": "between_tools"}
    config = {"thinking": True, "thinking_params": {"thinking": thinking}}
    if effort is not None:
        if source == "native":
            config["thinking_params"]["output_config"] = {"effort": effort}
        else:
            config["reasoning_effort"] = effort
    control = await _native("claude-sonnet-5-5", thinking, effort)
    assert control.content[-1].text == "ok"
    result = await _app(tmp_path, "claude-sonnet-5-5", config).agent(
        "Return ok", tools=(), trace=False, budget=10000, max_steps=1,
    )
    assert result.ok, result.error
    assert result.output == "ok" and len(native_requests) == 2
    for request in native_requests:
        assert request["thinking"] == thinking
        assert request.get("output_config", {}).get("effort") == effort


@pytest.mark.parametrize("model", ["claude-sonnet-5-5", "claude-opus-5-5"])
async def test_adaptive_thinking_matches_native_control(tmp_path, native_requests, model):
    thinking = {"type": "adaptive"}
    control = await _native(model, thinking, "max")
    assert control.content[-1].text == "ok"
    result = await _app(tmp_path, model, {
        "thinking": True, "thinking_params": {"thinking": thinking}, "reasoning_effort": "max",
    }).agent("Return ok", tools=(), trace=False, budget=10000, max_steps=1)
    assert result.ok, result.error
    assert result.output == "ok" and len(native_requests) == 2
    for request in native_requests:
        assert request["thinking"] == thinking
        assert request["output_config"] == {"effort": "max"}


async def test_opus_default_explains_adaptive_configuration_before_http(tmp_path, native_requests):
    with pytest.raises(anthropic.BadRequestError):
        await _native("claude-opus-5-5", {"type": "disabled"})
    result = await _app(tmp_path, "claude-opus-5-5").agent(
        "Return ok", tools=(), trace=False, budget=10000, max_steps=1,
    )
    assert result.status == "failed"
    assert "cannot disable adaptive thinking" in str(result.error)
    assert 'thinking_params={"thinking": {"type": "adaptive"}}' in str(result.error)
    assert len(native_requests) == 1


@pytest.mark.parametrize("effort", ["xhigh", "max"])
@pytest.mark.parametrize("source", ["native", "generic"])
async def test_between_tools_rejects_high_effort_before_http(tmp_path, native_requests, effort, source):
    thinking = {"type": "between_tools"}
    with pytest.raises(anthropic.BadRequestError):
        await _native("claude-sonnet-5-5", thinking, effort)
    config = {"thinking": True, "thinking_params": {"thinking": thinking}}
    if source == "native":
        config["thinking_params"]["output_config"] = {"effort": effort}
    else:
        config["reasoning_effort"] = effort
    result = await _app(tmp_path, "claude-sonnet-5-5", config).agent(
        "Return ok", tools=(), trace=False, budget=10000, max_steps=1,
    )
    assert result.status == "failed"
    assert "between_tools thinking requires effort high or lower" in str(result.error)
    assert len(native_requests) == 1


@pytest.mark.parametrize("field,value", [("display", "omitted"), ("budget_tokens", 1024)])
async def test_between_tools_rejects_extra_fields_before_http(tmp_path, native_requests, field, value):
    result = await _app(tmp_path, "claude-sonnet-5-5", {
        "thinking": True, "thinking_params": {"thinking": {"type": "between_tools", field: value}},
    }).agent("Return ok", tools=(), trace=False, budget=10000, max_steps=1)
    assert result.status == "failed"
    assert "unsupported Anthropic thinking field" in str(result.error)
    assert native_requests == []


@pytest.mark.parametrize("model", [
    "claude-opus-5-5", "claude-sonnet-5", "claude-haiku-5-5", "claude-sonnet-4-6", "compatible-model",
])
async def test_between_tools_rejects_unsupported_model_before_http(tmp_path, native_requests, model):
    result = await _app(tmp_path, model, {
        "thinking": True, "thinking_params": {"thinking": {"type": "between_tools"}},
    }).agent("Return ok", tools=(), trace=False, budget=10000, max_steps=1)
    assert result.status == "failed"
    assert "does not support between_tools thinking" in str(result.error)
    assert native_requests == []


@pytest.mark.parametrize("model", [
    "claude-sonnet-5", "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5",
])
async def test_effort_without_explicit_thinking_keeps_native_default(tmp_path, native_requests, model):
    result = await _app(tmp_path, model, {"reasoning_effort": "max"}).agent(
        "Return ok", tools=(), trace=False, budget=10000, max_steps=1,
    )
    assert result.ok, result.error
    assert len(native_requests) == 1
    assert "thinking" not in native_requests[0]
    assert native_requests[0]["output_config"] == {"effort": "max"}
