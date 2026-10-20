"""Anthropic credentials stay with their SDK owner across environment changes."""

from __future__ import annotations

import json
import os

import pytest

from opencollab import OpenCollab
from opencollab.adapters.llm.usage_ledger import build_usage_record
from opencollab.bootstrap.config import build_config
from tests.support.provider_sdk_http import completion_http_response, install_sdk_transport

FIRST_CREDENTIAL = "controlled-native-first"  # pragma: allowlist secret
SECOND_CREDENTIAL = "controlled-native-second"  # pragma: allowlist secret


@pytest.fixture
def native_requests(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith(("OPENCOLLAB_", "OPENAI_", "ANTHROPIC_", "DASHSCOPE_")):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("OPENCOLLAB_BUDGET_NUDGE_MODE", "off")
    monkeypatch.setenv("OPENCOLLAB_WRITE_NUDGE_MODE", "off")
    captured = []

    def handler(request):
        captured.append((request.headers.get("x-api-key"), request.headers.get("authorization")))
        return completion_http_response(request, text="finished")

    install_sdk_transport(monkeypatch, "anthropic", handler)
    return captured


async def _run(client, entry, artifacts):
    if entry == "create_model_client":
        async with client.create_model_client() as model:
            response = await model.complete([{"role": "user", "content": "Finish briefly."}])
            assert response.content == "finished"
        assert model._anthropic.is_closed()
        return
    if entry == "agent":
        result = await client.agent("Finish briefly.", tools=(), artifacts=artifacts)
    elif entry == "workflow":
        async def flow(ctx, _inputs):
            return await ctx.agent("Finish briefly.", tools=())

        result = await client.workflow(flow, artifacts=artifacts)
    else:
        team = artifacts.parent / "team.yaml"
        team.write_text("entry: reader\nroles:\n  reader:\n    prompt: Finish briefly.\n    tools: []\n")
        result = await client.team("Finish briefly.", config=team, use_worktrees=False, artifacts=artifacts)
    assert result.ok and result.output == "finished"


@pytest.mark.parametrize("entry", ["agent", "create_model_client", "workflow", "team"])
@pytest.mark.parametrize("source", ["native_token", "native_key", "argument", "config"])
async def test_sdk_credentials_follow_their_owner(
    monkeypatch, tmp_path, native_requests, caplog, entry, source,
):
    env_name = "ANTHROPIC_AUTH_TOKEN" if source == "native_token" else "ANTHROPIC_API_KEY"

    def client(credential):
        options = {}
        config = {"max_output_tokens": 128, "llm_max_retries": 0}
        if source == "argument":
            options["api_key"] = credential
        elif source == "config":
            config["api_key"] = credential
        return OpenCollab(
            tmp_path, model="fixture-model", provider="anthropic",
            base_url="https://controlled.invalid", config=config, **options,
        )

    monkeypatch.setenv(env_name, FIRST_CREDENTIAL)
    first = client(FIRST_CREDENTIAL)
    await _run(first, entry, tmp_path / "first")
    monkeypatch.setenv(env_name, SECOND_CREDENTIAL)
    second = client(SECOND_CREDENTIAL)
    await _run(second, entry, tmp_path / "second")
    await _run(first, entry, tmp_path / "first-again")

    def headers(credential):
        return (None, f"Bearer {credential}") if source == "native_token" else (credential, None)

    assert native_requests == [headers(FIRST_CREDENTIAL), headers(SECOND_CREDENTIAL), headers(FIRST_CREDENTIAL)]
    for owner in (first, second):
        public = json.dumps(dict(owner.configuration))
        assert "auth_token" not in owner.configuration
        assert FIRST_CREDENTIAL not in public and SECOND_CREDENTIAL not in public
    persisted = "\n".join(path.read_text() for path in tmp_path.rglob("*") if path.is_file())
    assert FIRST_CREDENTIAL not in persisted and SECOND_CREDENTIAL not in persisted
    assert FIRST_CREDENTIAL not in caplog.text and SECOND_CREDENTIAL not in caplog.text


@pytest.mark.parametrize("entry", ["agent", "create_model_client", "workflow", "team"])
async def test_sdk_captures_dotenv_bearer_before_the_file_changes(
    monkeypatch, tmp_path, native_requests, caplog, entry,
):
    configs = tmp_path / "configs"
    configs.mkdir()
    config_file = configs / ".env"

    def client(credential):
        config_file.write_text(f"ANTHROPIC_AUTH_TOKEN={credential}\n")
        return OpenCollab(
            tmp_path, model="fixture-model", provider="anthropic",
            base_url="https://controlled.invalid", config={"llm_max_retries": 0},
        )

    first = client(FIRST_CREDENTIAL)
    second = client(SECOND_CREDENTIAL)
    for owner, name in ((first, "first"), (second, "second"), (first, "first-again")):
        await _run(owner, entry, tmp_path / name)
        public = json.dumps(dict(owner.configuration))
        assert FIRST_CREDENTIAL not in public and SECOND_CREDENTIAL not in public
    assert native_requests == [
        (None, f"Bearer {FIRST_CREDENTIAL}"),
        (None, f"Bearer {SECOND_CREDENTIAL}"),
        (None, f"Bearer {FIRST_CREDENTIAL}"),
    ]
    artifacts = "\n".join(
        path.read_text() for name in ("first", "second", "first-again")
        for path in (tmp_path / name).rglob("*") if path.is_file()
    )
    assert FIRST_CREDENTIAL not in artifacts and SECOND_CREDENTIAL not in artifacts
    assert FIRST_CREDENTIAL not in caplog.text and SECOND_CREDENTIAL not in caplog.text


@pytest.mark.parametrize("entry", ["agent", "create_model_client", "workflow", "team"])
async def test_captured_bearer_ignores_a_later_environment_api_key(
    monkeypatch, tmp_path, native_requests, entry,
):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", FIRST_CREDENTIAL)
    first = OpenCollab(
        tmp_path, model="fixture-model", provider="anthropic", base_url="https://controlled.invalid",
    )
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN")
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECOND_CREDENTIAL)
    await _run(first, entry, tmp_path / "captured-bearer")
    assert native_requests == [(None, f"Bearer {FIRST_CREDENTIAL}")]


@pytest.mark.parametrize("entry", ["agent", "create_model_client"])
@pytest.mark.parametrize("source", ["argument", "config", "environment"])
@pytest.mark.parametrize("token_source", ["environment", "dotenv"])
async def test_explicit_api_key_precedes_a_native_environment_token(
    monkeypatch, tmp_path, native_requests, entry, source, token_source,
):
    if token_source == "environment":
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", FIRST_CREDENTIAL)
    else:
        (tmp_path / ".env").write_text(f"ANTHROPIC_AUTH_TOKEN={FIRST_CREDENTIAL}\n")
    options = {}
    if source == "argument":
        options["api_key"] = SECOND_CREDENTIAL
    elif source == "config":
        options["config"] = {"api_key": SECOND_CREDENTIAL}
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", SECOND_CREDENTIAL)
    client = OpenCollab(
        tmp_path, model="fixture-model", provider="anthropic", base_url="https://controlled.invalid",
        **options,
    )
    await _run(client, entry, tmp_path / "explicit-key")
    assert native_requests == [(SECOND_CREDENTIAL, None)]


@pytest.mark.parametrize("entry", ["agent", "create_model_client", "workflow", "team"])
@pytest.mark.parametrize("token_source", ["environment", "dotenv"])
@pytest.mark.parametrize("generic_source", ["environment", "dotenv"])
async def test_native_bearer_precedes_a_generic_key(
    monkeypatch, tmp_path, native_requests, entry, token_source, generic_source,
):
    dotenv = []
    for source, name, value in (
        (token_source, "ANTHROPIC_AUTH_TOKEN", FIRST_CREDENTIAL),
        (generic_source, "OPENCOLLAB_API_KEY", SECOND_CREDENTIAL),
    ):
        if source == "environment":
            monkeypatch.setenv(name, value)
        else:
            dotenv.append(f"{name}={value}")
    (tmp_path / ".env").write_text("\n".join(dotenv))
    client = OpenCollab(
        tmp_path, model="fixture-model", provider="anthropic", base_url="https://controlled.invalid",
    )
    await _run(client, entry, tmp_path / "native-bearer")
    assert native_requests == [(None, f"Bearer {FIRST_CREDENTIAL}")]


@pytest.mark.parametrize("entry", ["agent", "create_model_client"])
@pytest.mark.parametrize("explicit_key", [False, True])
async def test_explicit_bearer_precedes_environment_credentials(
    monkeypatch, tmp_path, native_requests, entry, explicit_key,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECOND_CREDENTIAL)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", SECOND_CREDENTIAL)
    monkeypatch.setenv("OPENCOLLAB_API_KEY", SECOND_CREDENTIAL)
    config = {"auth_token": FIRST_CREDENTIAL}
    if explicit_key:
        config["api_key"] = SECOND_CREDENTIAL
    client = OpenCollab(
        tmp_path, model="fixture-model", provider="anthropic", base_url="https://controlled.invalid",
        config=config,
    )
    await _run(client, entry, tmp_path / "explicit-bearer")
    assert native_requests == [
        (SECOND_CREDENTIAL, None) if explicit_key else (None, f"Bearer {FIRST_CREDENTIAL}"),
    ]


def test_resolved_bearer_is_excluded_from_config_repr(monkeypatch, tmp_path, native_requests):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", FIRST_CREDENTIAL)
    config = build_config(str(tmp_path), overrides={"provider": "anthropic"})
    assert config.auth_token == FIRST_CREDENTIAL
    assert FIRST_CREDENTIAL not in repr(config)


def test_usage_errors_redact_a_prior_bearer_after_environment_rotation(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", SECOND_CREDENTIAL)
    for message in (f"Bearer {FIRST_CREDENTIAL}", f'auth_token="{FIRST_CREDENTIAL}"'):
        record = build_usage_record(
            provider="anthropic", model="fixture-model", base_url="https://controlled.invalid",
            latency_s=0.1, status="error", error=RuntimeError(message),
        )
        assert FIRST_CREDENTIAL not in json.dumps(record)
        assert "[redacted]" in record["error"]["message"]
