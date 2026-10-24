from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from opencollab import OpenCollab
from opencollab.environments import DockerMount, DockerOptions, docker_isolation_provider
from tests.interfaces.test_member_authored_workflow import _call, _serve_model
from tests.support.paths import REPO_ROOT


def _containers():
    return set(subprocess.check_output([
        "docker", "ps", "-aq", "--filter", "label=opencollab.owner",
    ], text=True).split())


@pytest.mark.parametrize("cancel", [False, True])
async def test_member_container_authors_workflow_with_host_owned_child_containers(tmp_path, monkeypatch, cancel):
    image = os.environ.get("OPENCOLLAB_DOCKER_TEST_IMAGE")
    wheels = os.environ.get("OPENCOLLAB_DOCKER_WHEEL_DIR")
    if not image or not wheels:
        pytest.skip("local Docker image and Linux wheel directory enable the authored container probe")
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", os.devnull)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "seed.txt").write_text("source seed\n")
    for args in (("init", "-q"), ("add", "."), (
        "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial",
    )):
        subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True)
    team = tmp_path / "team.yaml"
    team.write_text("entry: writer\nroles:\n  writer:\n    isolation: docker-group\n"
                    "    prompt: Writer\n    tools: [file_write, bash, manage_workflow]\n")
    source = '''import asyncio
from opencollab.tools import builtin_tools

async def main(ctx, args):
    tools = builtin_tools("bash")
    return await asyncio.gather(
        ctx.agent("first worker", tools=tools),
        ctx.agent("second worker", tools=tools, isolation="docker-session"),
    )
'''
    observed = {}
    workers = []
    owner_ids = _containers()
    provider = docker_isolation_provider(DockerOptions(image=image, mounts=(
        DockerMount(str(REPO_ROOT), "/oc-source"), DockerMount(str(Path(wheels).resolve()), "/oc-wheels"),
    )), scope="group")

    def manager_provider(**kwargs):
        pool = provider(**kwargs)
        acquire = pool.acquire

        async def prepare(role, *, source=None):
            environment = await acquire(role, source=source)
            outcome = await environment.exec_cmd(
                "python3 -m pip install --no-index --no-compile --find-links=/oc-wheels "
                "openai anthropic typer rich prompt-toolkit filelock pydantic pyyaml "
                ">/tmp/oc-install.log 2>&1 && "
                "printf '/oc-source\\n' > /usr/local/lib/python3.12/site-packages/oc-source.pth"
            )
            assert outcome.returncode == 0, (await environment.exec_cmd("cat /tmp/oc-install.log")).stdout
            return environment

        pool.acquire = prepare
        return pool

    child_provider = docker_isolation_provider(DockerOptions(image=image), scope="session")

    def isolated_provider(**kwargs):
        pool = child_provider(**kwargs)
        acquire = pool.acquire

        async def remember(role, *, source=None):
            environment = await acquire(role, source=source)
            workers.append(environment)
            return environment

        pool.acquire = remember
        return pool

    def respond(body):
        replies = [item for item in body["messages"] if item["role"] == "tool"]
        manager = body["messages"][0]["content"].startswith("Writer")
        if manager:
            if not replies:
                call = _call("file_write", {"path": "authored.py", "content": source, "mode": "create"})
            elif len(replies) == 1:
                call = _call("manage_workflow", {"action": "launch", "source": "authored.py"})
            elif len(replies) == 2:
                call = _call("manage_workflow", {"action": "wait", "id": json.loads(replies[-1]["content"])["id"]})
            else:
                observed.update(json.loads(replies[-1]["content"]))
                return {"role": "assistant", "content": "collected"}, {"prompt_tokens": 3, "completion_tokens": 2}
        elif not replies:
            call = _call("bash", {"command": "cat seed.txt && cat /etc/hostname && pwd && "
                          "test ! -S /var/run/docker.sock && printf ready > entered"
                          + (" && sleep 300" if cancel else "")})
        else:
            return ({"role": "assistant", "content": replies[-1]["content"]},
                    {"prompt_tokens": 3, "completion_tokens": 2})
        return ({"role": "assistant", "content": None, "tool_calls": [call]},
                {"prompt_tokens": 3, "completion_tokens": 2})

    with _serve_model(respond) as (endpoint, requests):
        task = asyncio.create_task(OpenCollab(
            workspace, model="gpt-4o", api_key="offline-test", base_url=endpoint,  # pragma: allowlist secret
            environment_providers={"docker-group": manager_provider, "docker-session": isolated_provider},
            config={"llm_max_retries": 0, "thinking": False},
        ).team("write and run your Workflow", config=team, isolation="docker-session",
               artifacts=tmp_path / "artifacts", trace=False))
        if cancel:
            async def entered():
                while len(workers) < 1:
                    await asyncio.sleep(0.05)
                while True:
                    try:
                        if await workers[0].read_file("entered") == "ready":
                            return
                    except FileNotFoundError:
                        pass
                    await asyncio.sleep(0.05)
            await asyncio.wait_for(entered(), 60)
            task.cancel()
            try:
                cancelled = await task
            except asyncio.CancelledError:
                pass
            else:
                assert cancelled.status == "stopped", cancelled
        else:
            result = await asyncio.wait_for(task, 90)
            assert result.ok and result.output == "collected"
            assert observed["status"] == "completed", observed
            answers = observed["result"]["output"]
            assert len(answers) == 2 and all("source seed" in answer for answer in answers)
            hostnames = [re.search(r"(?m)^[0-9a-f]{12}$", answer).group() for answer in answers]
            assert hostnames[0] != hostnames[1]
            saved = Path(observed["artifacts"])
            assert json.loads((saved / "workflow.json").read_text())["sessions"] == 2
            assert len(list(saved.glob("[0-9]*.json"))) == 2
            patch = Path(result.metrics["entry_patch"]).read_text()
            assert "child-runs" not in patch and "__pycache__" not in patch
            assert all("api_key" not in path.read_text() for path in saved.glob("*.json"))
        assert len(requests) >= 4
    assert _containers() == owner_ids


async def test_authored_worker_keeps_resolved_model_when_physical_name_is_an_alias(tmp_path, monkeypatch):
    import sys

    from opencollab.adapters.env import LocalEnvironment
    from opencollab.application.tool_execution import ToolRuntime
    from tests.interfaces.test_member_authored_workflow import _manager

    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", os.devnull)
    (tmp_path / "authored.py").write_text(
        "async def main(ctx, args):\n    return await ctx.agent('child', tools=[])\n"
    )

    def respond(body):
        return {"role": "assistant", "content": body["model"]}, {"prompt_tokens": 3, "completion_tokens": 2}

    with _serve_model(respond) as (endpoint, requests):
        environment = LocalEnvironment(str(tmp_path))
        parent, tool, _runtime = _manager(environment)
        parent.agent.model = "physical-model"
        parent.agent.logical_model = "writer"
        parent.agent.api_key = "offline-test"  # pragma: allowlist secret
        parent.agent.base_url = endpoint
        tool._config.model_routes = {
            "writer": {"model": "physical-model", "provider": "openai", "base_url": endpoint,
                       "api_key": "local"},  # pragma: allowlist secret
            "physical-model": {"model": "wrong-model", "provider": "openai", "base_url": endpoint,
                               "api_key": "wrong"},  # pragma: allowlist secret
        }
        runtime = ToolRuntime(environment, None, None)
        try:
            launched = json.loads(await tool.execute_with_runtime({
                "action": "launch", "source": "authored.py", "python": sys.executable,
            }, runtime))
            result = json.loads(await tool.execute_with_runtime({"action": "wait", "id": launched["id"]}, runtime))
            assert result["status"] == "completed", result
            assert result["result"]["output"] == "physical-model"
            assert [request["model"] for request in requests] == ["physical-model"]
        finally:
            await parent.aclose()
            await environment.cleanup()
