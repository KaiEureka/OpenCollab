from __future__ import annotations

import json

import pytest

from opencollab.adapters.llm.client import LLMClient
from opencollab.adapters.storage import SessionStore
from opencollab.bootstrap import build_session, load_session
from opencollab.domain.agent import Agent
from opencollab.domain.session import SessionPhase
from opencollab.sdk import OpenCollab
from tests.support.environment_test_support import local_environment_factory as local_environment_factory
from tests.support.provider_sdk_http import http_response, install_sdk_transport
from tests.support.responses_provider_test_support import message_item


@pytest.mark.parametrize("content", ["Use a concise answer.", [{"type": "text", "text": "Use a concise answer."}]])
@pytest.mark.parametrize("saved", ["autosave", "checkpoint", "export"])
async def test_responses_developer_history_survives_save_and_next_turn(
    local_environment_factory, monkeypatch, tmp_path, content, saved,
):
    usage_path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", str(usage_path))
    monkeypatch.setenv("OPENCOLLAB_BUDGET_NUDGE_MODE", "off")
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        return http_response(request, 200, json={
            "id": f"resp_{len(requests)}", "object": "response", "created_at": 1,
            "status": "completed", "error": None, "incomplete_details": None,
            "model": body["model"], "output": [message_item(f"answer {len(requests)}")],
            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        })

    install_sdk_transport(monkeypatch, "openai", handler)
    automatic = tmp_path / "automatic.json"
    path = tmp_path / "exported.json" if saved == "export" else automatic
    agent = Agent(name="lead", system_prompt="System instructions.", model="o1-pro", wire_protocol="responses")
    async with LLMClient(
        model="o1-pro", wire_protocol="responses", max_retries=0,
        api_key="controlled-test",  # pragma: allowlist secret
        base_url="https://controlled.invalid/v1",
    ) as client:
        original = build_session(
            agent=agent, env=local_environment_factory(), llm=client,
            auto_save_path=str(automatic), aid=7, run_id="developer-roundtrip",
        )
        restored = None
        try:
            original.messages = [*original.messages, {"role": "developer", "content": content}]
            await original.add_user_message("First request.")
            assert await original.run_loop() == "answer 1"
            assert original.persistence_errors == ()
            assert original.phase is SessionPhase.DONE
            assert original.used_tokens == 12
            if saved != "autosave":
                original.save(str(path))
            snapshot = OpenCollab.read_session_snapshot(path)
            assert snapshot["messages"][1]["role"] == "developer"
            assert snapshot["messages"][1]["content"] == content
            assert snapshot["aid"] == 7
            assert snapshot["run_id"] == "developer-roundtrip"
            assert snapshot["session_state"]["used_tokens"] == 12
            assert snapshot["session_state"]["phase"] == "done"
            messages = original.messages.copy()
            await original.aclose()

            restored = load_session(
                str(path), agent=agent, env=local_environment_factory(), llm=client,
                auto_save_path=str(automatic),
            )
            assert restored.messages == messages
            assert restored.state.aid == 7
            assert restored.run_id == "developer-roundtrip"
            assert restored.used_tokens == 12
            assert restored.phase is SessionPhase.DONE
            await restored.add_user_message("Second request.")
            assert await restored.run_loop() == "answer 2"
            assert restored.used_tokens == 24
            assert restored.persistence_errors == ()
            persisted = OpenCollab.read_session_snapshot(automatic)
            assert persisted["session_state"]["used_tokens"] == 24
            assert persisted["aid"] == 7
            assert persisted["run_id"] == "developer-roundtrip"
            assert persisted["messages"][1]["role"] == "developer"
            assert persisted["messages"][1]["content"] == content
        finally:
            await original.aclose()
            if restored is not None:
                await restored.aclose()

    assert len(requests) == 2
    expected = content if isinstance(content, str) else [{"type": "input_text", "text": content[0]["text"]}]
    for request in requests:
        assert request["instructions"] == "System instructions."
        assert request["input"][0] == {"role": "developer", "content": expected}
    assert requests[0]["input"][1] == {"role": "user", "content": "First request."}
    assert requests[1]["input"][-1] == {"role": "user", "content": "Second request."}
    assert [row["role"] for row in requests[1]["input"]] == ["developer", "user", "assistant", "user"]
    usage = [json.loads(line) for line in usage_path.read_text(encoding="utf-8").splitlines()]
    assert len(usage) == 2
    assert [row["usage"]["total_tokens"] for row in usage] == [12, 12]


@pytest.mark.parametrize("format", ["json", "legacy-json", "jsonl", "journal", "checkpoint"])
@pytest.mark.parametrize("role", ["developer", "system", "user"])
def test_instruction_roles_round_trip_through_store_formats(tmp_path, format, role):
    path = tmp_path / "session.json"
    messages = [
        {"role": "system", "content": "System instructions."},
        {"role": role, "content": [{"type": "input_text", "text": "Keep this instruction."}]},
    ]
    store = SessionStore()
    if format == "json":
        store.save(str(path), messages, meta={"snapshot_version": 1, "aid": 7})
    elif format == "legacy-json":
        path.write_text(json.dumps(messages), encoding="utf-8")
    elif format == "jsonl":
        path.write_text("".join(json.dumps(message) + "\n" for message in messages), encoding="utf-8")
    elif format == "journal":
        store.append_snapshot_delta(str(path), sequence=1, replace_from=0, messages=messages, meta={})
    else:
        store.append_snapshot_delta(str(path), sequence=1, replace_from=0, messages=messages, meta={})
        store.checkpoint_snapshot(str(path), messages, sequence=1, meta={})
    assert store.load_messages(str(path), "fallback") == messages


@pytest.mark.parametrize("format", ["json", "jsonl", "journal"])
@pytest.mark.parametrize(("message", "error"), [
    ({"role": "developer"}, "missing 'content'"),
    ({"role": "developer", "content": None}, "must be text or a content-part list"),
    ({"role": "developer", "content": {}}, "must be text or a content-part list"),
    ({"role": "developer", "content": []}, "part list must not be empty"),
    ({"role": "developer", "content": [{"type": "input_text"}]}, "requires text"),
    ({"role": "developer", "content": [{"type": "text", "text": 42}]}, "requires text"),
    ({"role": "unknown", "content": "invalid"}, "Invalid message role"),
    ({"role": "Developer", "content": "invalid"}, "Invalid message role"),
    ({"role": "tool", "content": "result"}, "expected non-empty 'tool_call_id'"),
])
def test_store_still_rejects_malformed_messages(tmp_path, format, message, error):
    path = tmp_path / "invalid.json"
    store = SessionStore()
    if format == "json":
        path.write_text(json.dumps({"messages": [message]}), encoding="utf-8")
    elif format == "jsonl":
        path.write_text(
            json.dumps({"role": "system", "content": "System instructions."}) + "\n" + json.dumps(message) + "\n",
            encoding="utf-8",
        )
    else:
        with pytest.raises(ValueError, match=error):
            store.append_snapshot_delta(str(path), sequence=1, replace_from=0, messages=[message], meta={})
        assert not store.has_snapshot(str(path))
        return
    with pytest.raises(ValueError, match=error):
        store.load_snapshot(str(path), "fallback")
