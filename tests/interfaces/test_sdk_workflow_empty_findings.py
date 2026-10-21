from __future__ import annotations

import json
import os

import pytest

from opencollab import OpenCollab
from opencollab.application.session_run import _EMPTY_STOP_PLACEHOLDER, ENFORCEMENT_OFF, ENFORCEMENT_ON
from tests.support.provider_sdk_http import completion_http_response, install_sdk_transport


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("enforcement", [ENFORCEMENT_OFF, ENFORCEMENT_ON])
@pytest.mark.parametrize(
    ("replies", "max_steps", "expected"),
    [
        pytest.param([None, None], 100, "", id="empty-twice"),
        pytest.param([None], 1, "", id="empty-step-limit"),
        pytest.param([None, "Measured findings"], 100, "Measured findings", id="retry-answer"),
        pytest.param(["Measured findings"], 100, "Measured findings", id="direct-answer"),
        pytest.param([_EMPTY_STOP_PLACEHOLDER], 100, _EMPTY_STOP_PLACEHOLDER, id="same-text"),
        pytest.param([None, _EMPTY_STOP_PLACEHOLDER], 100, _EMPTY_STOP_PLACEHOLDER, id="retry-same-text"),
    ],
)
async def test_public_workflow_harvest_preserves_model_output_and_placeholder_origin(
    tmp_path, monkeypatch, provider, enforcement, replies, max_steps, expected,
):
    for key in tuple(os.environ):
        if key.startswith(("OPENCOLLAB_", "OPENAI_", "ANTHROPIC_", "DASHSCOPE_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("OPENCOLLAB_BUDGET_NUDGE_MODE", "off")
    monkeypatch.setenv("OPENCOLLAB_WRITE_NUDGE_MODE", "off")
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", "")
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        reply = replies[len(requests) - 1]
        return completion_http_response(request, text=reply if provider == "openai" else reply or "")

    install_sdk_transport(monkeypatch, provider, handler)

    async def workflow(ctx, _args):
        return await ctx.agent(
            "Return measured findings.", label="scout", tools=[], enforcement_strength=enforcement,
        )

    artifacts = tmp_path / "artifacts"
    result = await OpenCollab(
        tmp_path, model="controlled-demo", provider=provider,
        api_key="controlled-test",  # pragma: allowlist secret
        base_url="https://controlled.invalid/v1", config={"llm_max_retries": 0},
    ).workflow(
        workflow, budget=60_000, max_steps=max_steps, system_prompt="Return measured findings.",
        trace=True, artifacts=artifacts, timeout=5,
    )

    assert result.ok, result.reason
    assert result.output == expected
    assert len(requests) == len(replies)
    assert result.tokens == 4 * len(replies)
    assert all("_opencollab_placeholder" not in json.dumps(request) for request in requests)
    if len(replies) == 2:
        assert _EMPTY_STOP_PLACEHOLDER in json.dumps(requests[1])
    snapshot = OpenCollab.read_session_snapshot(artifacts / "000_scout.json")
    placeholders = [message for message in snapshot["messages"] if message.get("_opencollab_placeholder") is True]
    assert len(placeholders) == int(replies[0] is None)
    assert all(message["content"] == _EMPTY_STOP_PLACEHOLDER for message in placeholders)
    if expected:
        assert any(
            message.get("role") == "assistant" and message.get("content") == expected
            and message.get("_opencollab_placeholder") is not True
            for message in snapshot["messages"]
        )
    rows = [json.loads(line) for line in (artifacts / "orchestration.jsonl").read_text().splitlines()]
    metrics = [row["payload"] for row in rows if row.get("type") == "commitment_terminus"]
    if enforcement == ENFORCEMENT_ON:
        assert len(metrics) == 1
        assert metrics[0]["artifact_nonempty"] is bool(expected)
        assert metrics[0]["evidence_anchor_count"] == metrics[0]["unverified_count"] == 0
    else:
        assert metrics == []
