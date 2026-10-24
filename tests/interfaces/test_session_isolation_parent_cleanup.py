from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from opencollab.adapters import session_environments
from opencollab.adapters.llm.types import LLMResponse, Usage
from opencollab.adapters.session_environments import SessionEnvironmentPool
from opencollab.adapters.worktree_pool import WorktreePool
from opencollab.bootstrap import build_runtime_context, build_scheduler, container
from opencollab.bootstrap.isolation import prepare_scheduler


def _repository(path: Path) -> None:
    for args in (("init", "-q"), ("config", "user.name", "Test"),
                 ("config", "user.email", "test@example.invalid")):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    (path / "seed.txt").write_text("initial")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "initial"], check=True, capture_output=True)


@pytest.mark.parametrize("tester_isolation", ["shared", "worktree"])
async def test_nested_team_role_uses_its_spawning_parent_environment(tmp_path, monkeypatch, tester_isolation):
    _repository(tmp_path)
    team = tmp_path / "team.yaml"
    team.write_text(f"""entry: manager
isolation: shared
roles:
  manager:
    prompt: Manager
    tools: [spawn_agent]
  coder:
    prompt: Coder
    isolation: worktree
    tools: [spawn_agent]
  tester:
    prompt: Tester
    isolation: {tester_isolation}
    tools: []
topology:
  manager: [coder]
  coder: [tester]
""")
    release = asyncio.Event()

    class HoldingLLM:
        def __init__(self, **kwargs):
            pass

        def context_window(self):
            return 100_000

        async def complete(self, *args, **kwargs):
            await release.wait()
            return LLMResponse(content="done", usage=Usage(1, 1), finish_reason="stop")

        async def close(self):
            pass

    monkeypatch.setattr(container, "LLMClient", HoldingLLM)
    context = build_runtime_context(str(tmp_path), {
        "model": "fixture-model", "provider": "openai", "api_key": "local-fixture",  # pragma: allowlist secret
        "base_url": "https://controlled.invalid/v1", "budget": 100_000,
    }, trace=False)
    scheduler = await prepare_scheduler(
        context, builder=build_scheduler, use_worktrees=True, interactive=False,
        auto_save=False, team_config_path=team,
    )
    try:
        coder = await scheduler.spawn(0, "coder", "edit")
        coder_environment = scheduler.agent_environment(coder)
        await coder_environment.write_file("coder-only.txt", "candidate")
        tester = await scheduler.spawn(coder, "tester", "test")
        tester_environment = scheduler.agent_environment(tester)
        assert await tester_environment.read_file("coder-only.txt") == "candidate"
        assert (tester_environment is coder_environment) is (tester_isolation == "shared")
        assert not (tmp_path / "coder-only.txt").exists()
    finally:
        release.set()
        await asyncio.gather(*tuple(scheduler._tasks.values()), return_exceptions=True)
        await scheduler.cleanup()


async def test_strategy_cleanup_releases_reviewer_before_its_custom_source(tmp_path):
    _repository(tmp_path)

    def custom(**kwargs):
        return WorktreePool(use_worktrees=True, **kwargs)

    pool = SessionEnvironmentPool(str(tmp_path), isolation="worktree", providers={"custom": custom})
    environments = []
    try:
        entry = await pool.acquire("manager")
        environments.append(entry)
        pool.set_source(entry)
        coder = await pool.acquire("coder", isolation="custom")
        environments.append(coder)
        reviewer = await pool.acquire("reviewer", source=coder, isolation="worktree")
        environments.append(reviewer)
        paths = [Path(environment.workspace) for environment in environments]
        await pool.release()
        assert all(not path.exists() for path in paths)
    finally:
        for environment in reversed(environments):
            shutil.rmtree(environment.workspace, ignore_errors=True)


async def test_failed_shared_spawn_keeps_its_isolated_parent_usable(tmp_path, monkeypatch):
    _repository(tmp_path)
    team = tmp_path / "team.yaml"
    team.write_text("""entry: manager
isolation: worktree
roles:
  manager:
    prompt: Manager
    tools: [spawn_agent]
  coder:
    prompt: Coder
    isolation: shared
    tools: []
topology:
  manager: [coder]
""")
    context = build_runtime_context(str(tmp_path), {
        "model": "fixture-model", "provider": "openai", "api_key": "local-fixture",  # pragma: allowlist secret
        "base_url": "https://controlled.invalid/v1", "budget": 100_000,
    }, trace=False)
    scheduler = await prepare_scheduler(
        context, builder=build_scheduler, use_worktrees=True, interactive=False,
        auto_save=False, team_config_path=team,
    )

    def fail_build(**kwargs):
        raise ValueError("child construction failed")

    monkeypatch.setattr(scheduler._session_factory, "build_spawn_session", fail_build)
    try:
        parent = scheduler.agent_environment(0)
        with pytest.raises(ValueError, match="child construction failed"):
            await scheduler.spawn(0, "coder", "edit")
        assert await parent.read_file("seed.txt") == "initial"
        assert Path(parent.workspace).is_dir()
    finally:
        await scheduler.cleanup()


def test_unknown_strategy_does_not_retain_an_owned_directory_descriptor(tmp_path, monkeypatch):
    created = []
    original = session_environments.LocalEnvironment

    def record(*args, **kwargs):
        environment = original(*args, **kwargs)
        created.append(environment)
        return environment

    monkeypatch.setattr(session_environments, "LocalEnvironment", record)
    try:
        with pytest.raises(ValueError, match="needs an environment provider"):
            SessionEnvironmentPool(str(tmp_path), isolation="missing")
        assert all(environment._workspace_fd is None for environment in created)
    finally:
        for environment in created:
            environment._close_workspace()
