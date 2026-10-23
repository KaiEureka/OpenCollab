"""Visible Responses reasoning participates in fallback output accounting."""

from __future__ import annotations

import json

import pytest

from opencollab import OpenCollab
from opencollab.adapters.tools.fs import FileReadTool
from opencollab.domain.token_estimation import estimate_tokens
from tests.support.provider_sdk_http import http_response, install_sdk_transport
from tests.support.responses_provider_test_support import function_item, message_item

_SUMMARY = "Check the requested source before answering. " * 300
_REASONING = {
    "id": "rs_usage",
    "type": "reasoning",
    "summary": [{"type": "summary_text", "text": _SUMMARY}],
    "encrypted_content": "opaque-native-state" * 2_000,
}
_CALL = function_item("call_read", "file_read", '{"path":"source.txt"}')
_REPORTED = {"input_tokens": 50, "output_tokens": 7, "total_tokens": 57}


def _reply(request, body, items, usage):
    terminal = {
        "id": "resp_usage", "object": "response", "created_at": 1,
        "status": "completed", "model": body["model"], "output": items,
        "error": None, "incomplete_details": None,
    }
    if usage is not None:
        terminal["usage"] = usage
    if not body["stream"]:
        return http_response(request, 200, json=terminal)
    events = [
        {"type": "response.output_item.done", "output_index": index, "item": item}
        for index, item in enumerate(items)
    ]
    events.append({"type": "response.completed", "response": terminal})
    wire = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    return http_response(request, 200, headers={"content-type": "text/event-stream"}, text=wire)


@pytest.mark.parametrize("model", ["o3", "o1-pro"], ids=["stream", "nonstream"])
@pytest.mark.parametrize("usage", [None, {"input_tokens": 0, "output_tokens": 0}, _REPORTED],
                         ids=["missing", "zero", "reported"])
@pytest.mark.parametrize("response_kind", ["short-answer", "tool-roundtrip", "reasoning-only"])
async def test_public_agent_estimates_visible_reasoning_and_replays_native_state(
    monkeypatch, tmp_path, model, usage, response_kind,
):
    with_tool = response_kind == "tool-roundtrip"
    reasoning_only = response_kind == "reasoning-only"
    requests = []
    usage_path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", str(usage_path))
    monkeypatch.setenv("OPENCOLLAB_UNBOUNDED_LIMITS", "false")
    monkeypatch.setenv("OPENCOLLAB_WRITE_NUDGE_MODE", "off")
    (tmp_path / "source.txt").write_text("verified source", encoding="utf-8")
    items = [_REASONING]
    if not reasoning_only:
        items.append(_CALL if with_tool else message_item("42"))

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return _reply(request, body, items, usage)
        replayed = [item for item in body["input"] if item.get("type") == "reasoning"]
        assert replayed == [_REASONING]
        outputs = [item for item in body["input"] if item.get("type") == "function_call_output"]
        assert len(outputs) == 1 and "verified source" in outputs[0]["output"]
        return _reply(request, body, [message_item("42")], _REPORTED)

    install_sdk_transport(monkeypatch, "openai", handler)
    result = await OpenCollab(
        tmp_path, model=model, api_key="fixture-local-key",  # pragma: allowlist secret
        base_url="https://reasoning-usage.invalid/v1",
        config={"wire_protocol": "responses", "llm_max_retries": 0},
    ).agent("Read source.txt and answer." if with_tool else "Answer briefly.",
            tools=[FileReadTool()] if with_tool else [], budget=100_000,
            max_steps=3, trace=False)

    assert result.ok and result.output == (_SUMMARY if reasoning_only else "42"), result.reason
    assert len(requests) == (2 if with_tool else 1)
    assert requests[0]["stream"] is (model == "o3")
    records = [json.loads(line) for line in usage_path.read_text().splitlines()]
    first = records[0]["usage"]
    assert first.get("raw_usage", {}) == (usage or {})
    if usage == _REPORTED:
        assert first["output_tokens"] == 7
        assert first["estimated"] is False
    else:
        visible = _SUMMARY
        if not reasoning_only:
            visible += "file_read" + _CALL["arguments"] if with_tool else "42"
        assert first["output_tokens"] == estimate_tokens(visible)
        assert first["output_tokens"] < estimate_tokens(_REASONING["encrypted_content"])
        assert first["estimated"] is True
    assert result.tokens == sum(record["usage"]["total_tokens"] for record in records)
