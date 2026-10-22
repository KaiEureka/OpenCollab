"""Required writes complete only after tool execution through real provider SDKs."""

from __future__ import annotations

import json
import os

import pytest

from opencollab import OpenCollab
from opencollab.adapters.tools.fs import FileReadTool, FileWriteTool
from opencollab.application.session_run import _EMPTY_STOP_PLACEHOLDER, _REQUIRED_TOOL_RETRY_NUDGE
from opencollab.application.steering import READS_NUDGE_HARD
from tests.support.provider_sdk_http import install_sdk_transport, provider_http

_PROTOCOLS = [
    pytest.param("anthropic", "chat_completions", id="anthropic-manual-thinking"),
    pytest.param("openai", "chat_completions", id="chat-completions"),
    pytest.param("openai", "responses", id="responses"),
]
_ANSWER = "Finished the requested edit."
_WRITTEN = "actual edit\n"

def _read_blocks():
    return [
        {"type": "tool_use", "id": f"read_{index}", "name": "file_read", "input": {
            "path": f"input_{index}.txt",
        }}
        for index in range(READS_NUDGE_HARD)
    ]

def _write_blocks():
    return [{"type": "tool_use", "id": "write_1", "name": "file_write", "input": {
        "path": "delivered.txt", "mode": "create", "content": _WRITTEN,
    }}]

def _http_reply(body, blocks, *, provider, wire, index, include_thinking=True):
    input_tokens, output_tokens = 20 + index, 5 + index
    http = provider_http(provider)
    if provider == "anthropic":
        return http.Response(200, json={
            "id": f"msg_{index}", "type": "message", "role": "assistant", "model": body["model"],
            "content": ([{"type": "thinking", "thinking": "Inspect then act", "signature": "mock-signature"}]
                        if include_thinking else []) + blocks,
            "stop_reason": "tool_use" if blocks[-1]["type"] == "tool_use" else "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        })
    calls = [{"id": block["id"], "type": "function", "function": {
        "name": block["name"], "arguments": json.dumps(block["input"]),
    }} for block in blocks if block["type"] == "tool_use"]
    text = next((block["text"] for block in blocks if block["type"] == "text"), None)
    if wire == "responses":
        items = [{"type": "function_call", "id": f"fc_{call['id']}", "call_id": call["id"],
                  "name": call["function"]["name"], "arguments": call["function"]["arguments"],
                  "status": "completed"} for call in calls]
        if text:
            items.append({"type": "message", "id": f"assistant_{index}", "role": "assistant",
                          "status": "completed", "content": [
                              {"type": "output_text", "text": text, "annotations": []},
                          ]})
        response = {
            "id": f"resp_{index}", "object": "response", "status": "completed", "model": body["model"],
            "output": items, "error": None, "incomplete_details": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                      "total_tokens": input_tokens + output_tokens},
        }
        events = [{"type": "response.output_item.done", "output_index": position, "item": item}
                  for position, item in enumerate(items)]
        events.append({"type": "response.completed", "response": response})
        return http.Response(200, headers={"content-type": "text/event-stream"},
                             text="".join(f"data: {json.dumps(event)}\n\n" for event in events))
    return http.Response(200, json={
        "id": f"chat_{index}", "object": "chat.completion", "created": 1, "model": body["model"],
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text,
                      **({"tool_calls": calls} if calls else {})},
                     "finish_reason": "tool_calls" if calls else "stop"}],
        "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens,
                  "total_tokens": input_tokens + output_tokens},
    })

async def _run_agent(monkeypatch, tmp_path, *, provider="anthropic", wire="chat_completions",
                     write_at=None, require_write=True, empty_at=None):
    requests = []
    ledger_path = tmp_path / "usage.jsonl"
    config_path = tmp_path / "empty.env"
    config_path.write_text("", encoding="utf-8")
    for index in range(READS_NUDGE_HARD):
        (tmp_path / f"input_{index}.txt").write_text(f"source {index}\n", encoding="utf-8")
    monkeypatch.setenv("OPENCOLLAB_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("OPENCOLLAB_UNBOUNDED_LIMITS", "false")
    monkeypatch.setenv("OPENCOLLAB_WRITE_NUDGE_MODE", "on")
    monkeypatch.setenv("OPENCOLLAB_REQUIRE_INSTRUCTIONS_ECHO", "0")
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", str(ledger_path))

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        index = len(requests)
        assert index <= (4 if write_at == 3 else 3 if require_write else 1)
        if require_write and index == 1:
            blocks = _read_blocks()
        elif index == write_at:
            blocks = _write_blocks()
        else:
            blocks = [{"type": "text", "text": "" if index == empty_at else _ANSWER}]
        return _http_reply(body, blocks, provider=provider, wire=wire, index=index, include_thinking=index != empty_at)

    install_sdk_transport(monkeypatch, provider, handler)
    artifacts = tmp_path / "artifacts"
    thinking = provider == "anthropic"
    result = await OpenCollab(
        tmp_path, provider=provider, model="claude-sonnet-4-5" if thinking else "gpt-test",
        api_key="controlled-test", base_url="https://required-tool.invalid/v1",  # pragma: allowlist secret
        config={
            "wire_protocol": wire, "thinking": thinking,
            "thinking_params": {"thinking": {"type": "enabled", "budget_tokens": 1024}} if thinking else {},
            "max_output_tokens": 2048, "llm_max_retries": 0,
        },
    ).agent(
        "Read the provided source files and create delivered.txt with the fix." if require_write
        else "Return a brief visible answer.",
        tools=[FileReadTool(), FileWriteTool()], budget=100_000, max_steps=10,
        artifacts=artifacts, trace=True, timeout=10, cleanup_timeout=1,
    )
    snapshot = OpenCollab.read_session_snapshot(artifacts / "agent.json")
    trace = [json.loads(line) for line in (artifacts / "trajectory.jsonl").read_text().splitlines()]
    ledger = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    return result, requests, snapshot, trace, ledger

def _assert_usage(result, requests, ledger):
    expected = [25 + 2 * index for index in range(1, len(requests) + 1)]
    assert result.tokens == sum(expected)
    assert [row["status"] for row in ledger] == ["success"] * len(requests)
    assert [row["usage"]["total_tokens"] for row in ledger] == expected

@pytest.mark.parametrize("provider,wire", _PROTOCOLS)
@pytest.mark.parametrize("write_at", [None, 2, 3], ids=["repeated-prose", "immediate-write", "corrected-write"])
async def test_required_write_completion_uses_existing_correction(monkeypatch, tmp_path, provider, wire, write_at):
    result, requests, snapshot, trace, ledger = await _run_agent(
        monkeypatch, tmp_path, provider=provider, wire=wire, write_at=write_at,
    )

    assert len(requests) == (4 if write_at == 3 else 3)
    assert result.output == _ANSWER
    assert result.status == ("stopped" if write_at is None else "completed")
    assert result.reason == ("required tool was not called after correction" if write_at is None else None)
    delivered = tmp_path / "delivered.txt"
    if write_at is None:
        assert not delivered.exists()
    else:
        assert delivered.read_text(encoding="utf-8") == _WRITTEN
    tool_results = {message["tool_call_id"]: message["content"]
                    for message in snapshot["messages"] if message["role"] == "tool"}
    assert len(tool_results) == READS_NUDGE_HARD + (write_at is not None)
    assert all(f"source {index}" in tool_results[f"read_{index}"] for index in range(READS_NUDGE_HARD))
    retries = [row["payload"] for row in trace if row["type"] == "required_tool_retry"]
    assert retries == ([] if write_at == 2 else [{"allowed_tools": ["apply_patch", "file_write"]}])
    assert [tool.get("name", tool.get("function", {}).get("name")) for tool in requests[1]["tools"]] == [
        "file_write",
    ]
    assert "next action MUST be a file_write" in json.dumps(requests[1])
    if write_at != 2:
        assert _REQUIRED_TOOL_RETRY_NUDGE in json.dumps(requests[2])
        assert [tool.get("name", tool.get("function", {}).get("name")) for tool in requests[2]["tools"]] == [
            "file_write",
        ]
    if provider == "anthropic":
        assert requests[1]["tool_choice"] == {"type": "auto"}
        if write_at != 2:
            assert requests[2]["tool_choice"] == {"type": "auto"}
        assert all(request["thinking"] == {"type": "enabled", "budget_tokens": 1024} for request in requests)
    else:
        assert requests[1]["tool_choice"] == "required"
        if write_at != 2:
            assert requests[2]["tool_choice"] == "required"
    _assert_usage(result, requests, ledger)

@pytest.mark.parametrize("provider,wire", _PROTOCOLS[:2])
async def test_required_write_empty_reply_keeps_placeholder_origin_local(monkeypatch, tmp_path, provider, wire):
    result, requests, snapshot, _trace, ledger = await _run_agent(
        monkeypatch, tmp_path, provider=provider, wire=wire, empty_at=2,
    )
    placeholders = [message for message in snapshot["messages"] if message.get("_opencollab_placeholder") is True]
    assert len(placeholders) == 1
    assert placeholders[0]["content"] == _EMPTY_STOP_PLACEHOLDER
    assert _EMPTY_STOP_PLACEHOLDER in json.dumps(requests[2])
    assert all("_opencollab_placeholder" not in json.dumps(request) for request in requests)
    assert result.output == _ANSWER
    assert result.status == "stopped"
    _assert_usage(result, requests, ledger)

async def test_ordinary_anthropic_end_turn_completes_with_manual_thinking(monkeypatch, tmp_path):
    result, requests, snapshot, trace, ledger = await _run_agent(monkeypatch, tmp_path, require_write=False)

    assert result.output == _ANSWER
    assert result.status == "completed"
    assert result.reason is None
    assert len(requests) == 1
    assert not any(message["role"] == "tool" for message in snapshot["messages"])
    assert not any(row["type"] == "required_tool_retry" for row in trace)
    _assert_usage(result, requests, ledger)

@pytest.mark.parametrize("provider,wire", _PROTOCOLS)
@pytest.mark.parametrize("serialized", [False, True])
@pytest.mark.parametrize("text", ["verified", _EMPTY_STOP_PLACEHOLDER], ids=["ordinary", "same-text"])
async def test_team_required_write_keeps_real_answer_before_correction(
    monkeypatch, tmp_path, provider, wire, serialized, text,
):
    for key in tuple(os.environ):
        if key.startswith(("OPENCOLLAB_", "OPENAI_", "ANTHROPIC_", "DASHSCOPE_")):
            monkeypatch.delenv(key)
    config_path = tmp_path / "empty.env"
    config_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("OPENCOLLAB_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("OPENCOLLAB_BUDGET_NUDGE_MODE", "every-step")
    monkeypatch.setenv("OPENCOLLAB_WRITE_NUDGE_MODE", "on")
    monkeypatch.setenv("OPENCOLLAB_REQUIRE_INSTRUCTIONS_ECHO", "0")
    for index in range(READS_NUDGE_HARD):
        (tmp_path / f"input_{index}.txt").write_text(f"source {index}\n", encoding="utf-8")
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        blocks = _read_blocks() if len(requests) == 1 else [{"type": "text", "text": text}]
        return _http_reply(body, blocks, provider=provider, wire=wire, index=len(requests))

    install_sdk_transport(monkeypatch, provider, handler)
    config = tmp_path / "team.yaml"
    config.write_text(
        "entry: lead\nroles:\n  lead:\n    prompt: Read the source files and return the requested text.\n"
        "    model: controlled-demo\n    tools: [file_read, file_write]\n",
        encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    result = await OpenCollab(
        tmp_path, provider=provider, model="controlled-demo",
        api_key="controlled-test", base_url="https://controlled.invalid/v1",  # pragma: allowlist secret
        config={"wire_protocol": wire, "llm_max_retries": 0},
    ).team(
        "Read the source files and return the requested text.", config=config, budget=100_000,
        max_steps=2, use_worktrees=False, serialize_turns=serialized,
        trace=False, artifacts=artifacts, timeout=5,
    )
    snapshot = OpenCollab.read_session_snapshot(next(artifacts.glob("agent_0_*.json")))
    results = {message["tool_call_id"]: message["content"]
               for message in snapshot["messages"] if message["role"] == "tool"}
    assert len(results) == READS_NUDGE_HARD
    assert all(f"source {index}" in results[f"read_{index}"] for index in range(READS_NUDGE_HARD))
    assert len(requests) == 2
    assert result.status == "stopped"
    assert result.reason == "step limit reached: 2 steps"
    assert result.output == text
    messages = snapshot["messages"]
    answer_index = next(index for index, message in reversed(list(enumerate(messages)))
                        if message["role"] == "assistant" and message.get("content") == text)
    assert messages[answer_index + 1]["content"] == _REQUIRED_TOOL_RETRY_NUDGE
    assert messages[answer_index].get("_opencollab_placeholder") is not True
