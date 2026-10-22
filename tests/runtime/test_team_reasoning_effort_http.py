from __future__ import annotations

import json
import os

import pytest

from opencollab import OpenCollab
from tests.support.provider_sdk_http import completion_http_response, install_sdk_transport


@pytest.fixture(autouse=True)
def _isolated_provider_config(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith(("OPENCOLLAB_", "OPENAI_", "ANTHROPIC_", "DASHSCOPE_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", str(tmp_path / "usage.jsonl"))


@pytest.mark.asyncio
@pytest.mark.parametrize("role_model", ["gpt-4o", "gpt-5"])
@pytest.mark.parametrize("role_thinking", [False, True, None])
async def test_responses_team_uses_effective_role_effort(
    monkeypatch, tmp_path, role_model, role_thinking,
):
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        return completion_http_response(request, text="finished")

    install_sdk_transport(monkeypatch, "openai", handler)
    app = OpenCollab(
        tmp_path, model="gpt-5", api_key="fake-key-for-test",  # pragma: allowlist secret
        base_url="https://controlled.invalid/v1",
        config={"wire_protocol": "responses", "thinking": True,
                "reasoning_effort": "high", "llm_max_retries": 0},
    )
    team = tmp_path / "team.yaml"
    fields = "" if role_thinking is None else f"    thinking: {str(role_thinking).lower()}\n"
    team.write_text(
        "entry: reader\nroles:\n  reader:\n    prompt: Reader\n    tools: []\n"
        f"    model: {role_model}\n" + fields,
    )
    if role_model == "gpt-4o" and role_thinking is not False:
        with pytest.raises(ValueError, match="gpt-4o.*does not support Responses reasoning_effort"):
            await app.team("answer", config=team, use_worktrees=False, trace=False, max_steps=1)
        assert requests == []
        return

    result = await app.team("answer", config=team, use_worktrees=False, trace=False, max_steps=1)
    assert result.ok and result.output == "finished"
    assert len(requests) == 1
    assert requests[0]["model"] == role_model
    assert requests[0].get("reasoning") == (
        None if role_thinking is False else {"effort": "high"}
    )
    assert app.configuration["reasoning_effort"] == "high"


@pytest.mark.asyncio
@pytest.mark.parametrize("default_thinking", [False, True])
@pytest.mark.parametrize("role_thinking", [False, True, None])
async def test_anthropic_team_role_override_suppresses_inherited_effort(
    monkeypatch, tmp_path, default_thinking, role_thinking,
):
    requests = []

    async def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        disabled = body.get("thinking") == {"type": "disabled"}
        return completion_http_response(request, text="thinking-off" if disabled else "thinking-on")

    install_sdk_transport(monkeypatch, "anthropic", handler)
    app = OpenCollab(
        tmp_path, provider="anthropic", model="claude-sonnet-5",
        api_key="fake-key-for-test",  # pragma: allowlist secret
        base_url="https://controlled.invalid/v1",
        config={"thinking": default_thinking, "thinking_params": {"thinking": {"type": "adaptive"}},
                "reasoning_effort": "high", "llm_max_retries": 0},
    )
    team = tmp_path / "team.yaml"
    fields = "" if role_thinking is None else f"    thinking: {str(role_thinking).lower()}\n"
    team.write_text("entry: reader\nroles:\n  reader:\n    prompt: Reader\n    tools: []\n" + fields)

    result = await app.team("answer", config=team, use_worktrees=False, trace=False, max_steps=1)
    assert result.ok
    assert len(requests) == 1
    if role_thinking is False:
        assert result.output == "thinking-off"
        assert requests[0]["thinking"] == {"type": "disabled"}
        assert "output_config" not in requests[0]
    else:
        assert result.output == "thinking-on"
        assert requests[0]["output_config"] == {"effort": "high"}
        expected = {"type": "adaptive"} if (default_thinking or role_thinking) else None
        assert requests[0].get("thinking") == expected
    assert app.configuration["reasoning_effort"] == "high"
