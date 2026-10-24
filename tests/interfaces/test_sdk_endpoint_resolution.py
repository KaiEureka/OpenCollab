"""SDK endpoint selection remains stable across provider environment changes."""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from opencollab import OpenCollab
from opencollab.adapters.env import LocalEnvironment
from opencollab.adapters.llm.client import LLMClient
from opencollab.bootstrap import programmatic
from tests.support.provider_sdk_http import completion_http_response, install_sdk_transport

INITIAL_API_KEY = "fake-key-at-construction"  # pragma: allowlist secret
CHANGED_API_KEY = "fake-key-after-change"  # pragma: allowlist secret
PROVIDERS = ["openai", "anthropic", "deepseek"]

@pytest.fixture
def requests(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith("OPENCOLLAB_") or name in {
            "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
            "DASHSCOPE_API_KEY",
        }:
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENCOLLAB_MODEL", "initial-model")
    monkeypatch.setenv("OPENCOLLAB_API_KEY", INITIAL_API_KEY)
    captured = []

    def handler(request):
        body = json.loads(request.content)
        captured.append({
            "url": str(request.url),
            "model": body["model"],
            "api_key": request.headers.get("x-api-key") or request.headers["authorization"].removeprefix("Bearer "),
        })
        return completion_http_response(request, text="finished")

    install_sdk_transport(monkeypatch, "openai", handler)
    install_sdk_transport(monkeypatch, "anthropic", handler)
    return captured

def _base_url_env(provider):
    return "ANTHROPIC_BASE_URL" if provider == "anthropic" else "OPENAI_BASE_URL"

def _default_base_url(provider):
    return "https://api.anthropic.com" if provider == "anthropic" else "https://api.openai.com/v1"

def _request(base_url, provider, *, changed=False):
    path = "v1/messages" if provider == "anthropic" else "chat/completions"
    return {
        "url": f"{base_url.rstrip('/')}/{path}",
        "model": "changed-model" if changed else "initial-model",
        "api_key": CHANGED_API_KEY if changed else INITIAL_API_KEY,
    }

async def _complete(client, entry_point):
    if entry_point == "agent":
        result = await client.agent("finish once", tools=(), trace=False)
        assert result.ok
        assert result.output == "finished"
    else:
        async with client.create_model_client() as model:
            response = await model.complete([{"role": "user", "content": "finish once"}])
        assert response.content == "finished"

@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("entry_point", ["agent", "create_model_client"])
@pytest.mark.parametrize("source", ["default", "provider_env", "generic_env", "config", "argument"])
async def test_endpoint_is_selected_when_sdk_is_constructed(
    monkeypatch, tmp_path, requests, provider, entry_point, source,
):
    initial_base_url = _default_base_url(provider)
    kwargs = {"provider": provider}
    if source != "default":
        initial_base_url = "https://initial.example/custom/"
        if source == "provider_env":
            monkeypatch.setenv(_base_url_env(provider), initial_base_url)
        elif source == "generic_env":
            monkeypatch.setenv("OPENCOLLAB_BASE_URL", initial_base_url)
        elif source == "config":
            kwargs["config"] = {"base_url": initial_base_url}
        else:
            kwargs["base_url"] = initial_base_url

    client = OpenCollab(tmp_path, **kwargs)
    await _complete(client, entry_point)
    changed_base_url = "https://changed.example/next"
    monkeypatch.setenv(_base_url_env(provider), changed_base_url)
    monkeypatch.setenv("OPENCOLLAB_BASE_URL", changed_base_url)
    monkeypatch.setenv("OPENCOLLAB_MODEL", "changed-model")
    monkeypatch.setenv("OPENCOLLAB_API_KEY", CHANGED_API_KEY)
    await _complete(client, entry_point)
    await _complete(OpenCollab(tmp_path, provider=provider), entry_point)

    assert requests == [
        _request(initial_base_url, provider),
        _request(initial_base_url, provider),
        _request(changed_base_url, provider, changed=True),
    ]
    assert client.configuration["base_url_sha256"] == hashlib.sha256(initial_base_url.encode()).hexdigest()

@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("source", ["dotenv", "environment", "config", "argument"])
async def test_effective_base_url_priority_is_preserved(monkeypatch, tmp_path, requests, provider, source):
    dotenv_base_url = "https://dotenv.example/v1"
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / ".env").write_text(
        f"{_base_url_env(provider)}={dotenv_base_url}\n", encoding="utf-8",
    )
    expected = dotenv_base_url
    kwargs = {"provider": provider}
    if source != "dotenv":
        monkeypatch.setenv(_base_url_env(provider), "https://provider-env.example/v1")
        expected = "https://environment.example/v1"
        monkeypatch.setenv("OPENCOLLAB_BASE_URL", expected)
    if source in {"config", "argument"}:
        expected = "https://config.example/v1"
        kwargs["config"] = {"base_url": expected}
    if source == "argument":
        expected = "https://argument.example/v1"
        kwargs["base_url"] = expected
    client = OpenCollab(tmp_path, **kwargs)
    monkeypatch.setenv(_base_url_env(provider), "https://later.example/v1")
    monkeypatch.setenv("OPENCOLLAB_BASE_URL", "https://later.example/v1")
    await _complete(client, "create_model_client")
    assert requests == [_request(expected, provider)]

@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("entry_point", ["workflow", "team"])
async def test_workflow_and_team_use_the_sdk_endpoint(monkeypatch, tmp_path, requests, provider, entry_point):
    client = OpenCollab(tmp_path, provider=provider)
    monkeypatch.setenv(_base_url_env(provider), "https://later.example/v1")

    if entry_point == "workflow":
        async def flow(ctx, inputs):
            return await ctx.agent(inputs["prompt"], tools=(), label="reader")

        result = await client.workflow(flow, {"prompt": "finish once"}, trace=False)
    else:
        team_file = tmp_path / "team.yaml"
        team_file.write_text(
            "entry: reader\nroles:\n  reader:\n    prompt: Finish assigned text.\n    tools: []\n", encoding="utf-8",
        )
        result = await client.team("finish once", config=team_file, use_worktrees=False, trace=False)

    assert result.ok
    assert result.output == "finished"
    assert requests == [_request(_default_base_url(provider), provider)]

@pytest.mark.parametrize("provider", PROVIDERS)
async def test_independent_llm_client_keeps_provider_environment_selection(monkeypatch, requests, provider):
    monkeypatch.setenv("OPENCOLLAB_BASE_URL", "https://sdk-only.example/v1")
    async with LLMClient(model="initial-model", provider=provider, api_key=INITIAL_API_KEY) as model:
        await model.complete([{"role": "user", "content": "finish once"}])
    provider_base_url = "https://current.example/provider"
    monkeypatch.setenv(_base_url_env(provider), provider_base_url)
    async with LLMClient(model="initial-model", provider=provider, api_key=INITIAL_API_KEY) as model:
        await model.complete([{"role": "user", "content": "finish once"}])
    assert requests == [_request(_default_base_url(provider), provider), _request(provider_base_url, provider)]


async def test_dashscope_does_not_send_an_unrelated_provider_key(monkeypatch, tmp_path, requests):
    environments = []

    class ObservedEnvironment(LocalEnvironment):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            environments.append((self, self._workspace_fd))

    monkeypatch.setattr(programmatic, "LocalEnvironment", ObservedEnvironment)
    monkeypatch.delenv("OPENCOLLAB_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-unrelated-openai-key")  # pragma: allowlist secret
    endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    client = OpenCollab(tmp_path, model="fixture-model", base_url=endpoint)

    with pytest.raises(ValueError, match="no API key configured"):
        client.create_model_client()
    with pytest.raises(ValueError, match="no API key configured"):
        LLMClient(model="fixture-model", base_url=endpoint)
    with pytest.raises(ValueError, match="no API key configured"):
        await client.agent("finish once", tools=(), trace=False)
    assert requests == []
    assert len(environments) == 1
    environment, descriptor = environments[0]
    assert environment._workspace_fd is None
    with pytest.raises(OSError):
        os.fstat(descriptor)

    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-dashscope-key")  # pragma: allowlist secret
    configured = OpenCollab(tmp_path, model="fixture-model", base_url=endpoint)
    await _complete(configured, "create_model_client")
    assert requests == [{
        "url": f"{endpoint}/chat/completions",
        "model": "fixture-model",
        "api_key": "fake-dashscope-key",  # pragma: allowlist secret
    }]


async def test_independent_openai_client_keeps_its_own_environment_key(monkeypatch, requests):
    monkeypatch.setenv("OPENAI_API_KEY", CHANGED_API_KEY)
    endpoint = "https://custom.example/v1"
    async with LLMClient(model="initial-model", base_url=endpoint) as client:
        response = await client.complete([{"role": "user", "content": "finish once"}])
    assert response.content == "finished"
    assert requests == [{
        "url": f"{endpoint}/chat/completions",
        "model": "initial-model",
        "api_key": CHANGED_API_KEY,
    }]


async def test_independent_anthropic_client_keeps_native_auth_token(monkeypatch, requests):
    monkeypatch.delenv("OPENCOLLAB_API_KEY")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", CHANGED_API_KEY)
    endpoint = "https://anthropic.example"
    async with LLMClient(model="initial-model", provider="anthropic", base_url=endpoint) as client:
        response = await client.complete([{"role": "user", "content": "finish once"}])
    assert response.content == "finished"
    assert requests == [{
        "url": f"{endpoint}/v1/messages",
        "model": "initial-model",
        "api_key": CHANGED_API_KEY,
    }]


@pytest.mark.parametrize("entry_point", ["agent", "create_model_client"])
@pytest.mark.parametrize("endpoint", [
    "https://service.cn-hangzhou.pai-eas.aliyuncs.com/api/predict/qwen/v1",
    "https://gateway.example/dashscope/v1",
    "https://gateway.example/v1?upstream=dashscope",
    "https://dashscope.proxy.example/v1",
    "https://dashscope.aliyuncs.com.proxy.example/v1",
    "https://workspace.cn-beijing.maas.aliyuncs.com.proxy.example/v1",
])
async def test_ordinary_compatible_endpoints_keep_openai_environment_key(
    monkeypatch, tmp_path, requests, entry_point, endpoint,
):
    monkeypatch.delenv("OPENCOLLAB_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", INITIAL_API_KEY)
    client = OpenCollab(tmp_path, provider="openai", base_url=endpoint)

    await _complete(client, entry_point)

    assert len(requests) == 1
    assert requests[0]["api_key"] == INITIAL_API_KEY


@pytest.mark.parametrize("endpoint", [
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
    "https://coding.dashscope.aliyuncs.com/v1",
    "https://vpc-ap-southeast-1.dashscope.aliyuncs.com/compatible-mode/v1",
    "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "https://workspace.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    "https://trial.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
])
async def test_model_studio_endpoints_keep_provider_specific_credentials(
    monkeypatch, tmp_path, requests, endpoint,
):
    monkeypatch.delenv("OPENCOLLAB_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", INITIAL_API_KEY)
    client = OpenCollab(tmp_path, provider="openai", base_url=endpoint)

    with pytest.raises(ValueError, match="no API key configured"):
        client.create_model_client()
    assert requests == []

    monkeypatch.setenv("DASHSCOPE_API_KEY", CHANGED_API_KEY)
    configured = OpenCollab(tmp_path, provider="openai", base_url=endpoint)
    await _complete(configured, "create_model_client")

    assert requests == [_request(endpoint, "openai") | {"api_key": CHANGED_API_KEY}]
