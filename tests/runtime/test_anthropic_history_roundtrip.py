"""Standard assistant histories survive native Anthropic tool rounds."""

import json

import pytest

from opencollab.adapters.llm.anthropic_provider import convert_to_anthropic_messages
from opencollab.adapters.llm.client import LLMClient
from opencollab.bootstrap import build_session, load_session
from opencollab.domain.agent import Agent
from tests.support.environment_test_support import local_environment_factory as local_environment_factory
from tests.support.provider_sdk_http import completion_http_response, http_response, install_sdk_transport


@pytest.mark.parametrize("history_shape", ["null", "omitted", "provider_state"])
async def test_anthropic_tool_response_replays_standard_assistant_history(monkeypatch, history_shape):
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        content = (
            [{"type": "tool_use", "id": "toolu_lookup", "name": "lookup", "input": {}}]
            if len(requests) == 1
            else [{"type": "text", "text": "done"}]
        )
        return http_response(request, 200, json={
            "id": "msg_roundtrip", "type": "message", "role": "assistant",
            "model": "claude-sonnet-4-6", "content": content,
            "stop_reason": "tool_use" if len(requests) == 1 else "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 25, "output_tokens": 10},
        })

    install_sdk_transport(monkeypatch, "anthropic", handler)
    tool = {"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}
    async with LLMClient(
        model="claude-sonnet-4-6", provider="anthropic", api_key="test-placeholder",  # pragma: allowlist secret
        base_url="https://provider.invalid/v1", max_retries=0,
    ) as client:
        history = [{"role": "user", "content": "look it up"}]
        first = await client.complete(history, tools=[tool])
        assert first.content is None
        assistant = {"role": "assistant", "content": first.content, "tool_calls": first.tool_calls}
        if history_shape == "omitted":
            assistant.pop("content")
        elif history_shape == "provider_state":
            assistant["provider_state"] = first.provider_state
        history += [assistant, {"role": "tool", "tool_call_id": "toolu_lookup", "content": "42"}]
        second = await client.complete(history, tools=[tool])

    assert second.content == "done"
    assert len(requests) == 2
    assert requests[1]["messages"][1]["content"] == [
        {"type": "tool_use", "id": "toolu_lookup", "name": "lookup", "input": {}}
    ]
    assert requests[1]["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_lookup", "content": "42"}
    ]

@pytest.mark.parametrize("source_provider", ["anthropic", "responses"])
@pytest.mark.parametrize("content", [
    "Keep answers concise and include the unit.",
    [{"type": "text", "text": "Keep answers concise and include the unit."}],
])
async def test_developer_session_restores_into_native_anthropic_request(
    local_environment_factory, monkeypatch, tmp_path, source_provider, content,
):
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("OPENCOLLAB_BUDGET_NUDGE_MODE", "off")
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return completion_http_response(request, text=f"answer {len(requests)}")

    install_sdk_transport(monkeypatch, "anthropic", handler)
    if source_provider == "responses":
        install_sdk_transport(monkeypatch, "openai", handler)
    source_model = "gpt-5" if source_provider == "responses" else "claude-sonnet-4-6"
    source_protocol = "responses" if source_provider == "responses" else "chat_completions"
    source_agent = Agent(name="lead", system_prompt="Answer distance questions.", model=source_model,
                         provider="openai" if source_provider == "responses" else "anthropic",
                         wire_protocol=source_protocol)
    path = tmp_path / "session.json"
    async with LLMClient(
        model=source_model, provider=source_agent.provider, wire_protocol=source_protocol,
        api_key="test-placeholder", base_url="https://provider.invalid", max_retries=0,  # pragma: allowlist secret
    ) as source_client:
        source = build_session(agent=source_agent, llm=source_client, env=local_environment_factory())
        try:
            source.messages = [*source.messages, {"role": "developer", "content": content}]
            await source.add_user_message("How far is twelve kilometres?")
            assert await source.run_loop() == "answer 1"
            source.save(str(path))
            saved_messages = source.messages.copy()
        finally:
            await source.aclose()

    agent = Agent(name="lead", system_prompt=source_agent.system_prompt,
                  model="claude-sonnet-4-6", provider="anthropic")
    async with LLMClient(
        model=agent.model, provider="anthropic", api_key="test-placeholder",  # pragma: allowlist secret
        base_url="https://provider.invalid", max_retries=0,
    ) as client:
        restored = load_session(str(path), agent=agent, llm=client, env=local_environment_factory())
        try:
            assert restored.messages == saved_messages
            await restored.add_user_message("Repeat that distance briefly.")
            assert await restored.run_loop() == "answer 2"
            assert restored.messages[:len(saved_messages)] == saved_messages
        finally:
            await restored.aclose()

    assert len(requests) == 2
    assert requests[1]["system"] == "Answer distance questions.\n\nKeep answers concise and include the unit."
    assert [message["role"] for message in requests[1]["messages"]] == ["user", "assistant", "user"]
    assert requests[1]["messages"][-1]["content"] == "Repeat that distance briefly."


def test_anthropic_developer_summary_keeps_its_conversation_position():
    system, messages = convert_to_anthropic_messages([
        {"role": "system", "content": "Answer distance questions."},
        {"role": "developer", "content": [{"type": "input_text", "text": "Keep units."}]},
        {"role": "user", "content": "Earlier question."},
        {"role": "assistant", "content": "Earlier answer."},
        {"role": "developer", "content": "The distance is twelve kilometres.", "compacted": True},
        {"role": "user", "content": "Repeat the distance."},
    ])
    assert system == ["Answer distance questions.", "Keep units."]
    assert messages[2] == {"role": "user", "content": "The distance is twelve kilometres."}
    assert messages[3] == {"role": "user", "content": "Repeat the distance."}


@pytest.mark.parametrize("role,content,error", [
    ("user", None, "content must be text or a content block list"),
    ("assistant", {"text": "invalid"}, "content must be text or a content block list"),
    ("developer", None, "content must be text or a content block list"),
    ("developer", [{"type": "text", "text": 42}], "requires text"),
])
def test_anthropic_history_retains_content_validation(role, content, error):
    with pytest.raises(ValueError, match=error):
        convert_to_anthropic_messages([{"role": role, "content": content}])
