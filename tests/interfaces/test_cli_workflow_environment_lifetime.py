"""CLI workflow agents share the source environment through owned cleanup."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opencollab.adapters.cli.workflow import app as workflow_app
from opencollab.adapters.env import LocalEnvironment
from opencollab.adapters.llm.types import LLMResponse, Usage
from opencollab.bootstrap.workflow_runtime import run_workflow
from tests.support.provider_sdk_http import completion_http_response, install_sdk_transport


def _directory_fds() -> set[int]:
    found = set()
    for name in os.listdir('/dev/fd'):
        fd = int(name)
        try:
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                found.add(fd)
        except OSError:
            pass
    return found


@pytest.fixture
def workflow_workspace(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    for args in (('init', '-q'), ('-c', 'user.name=Tests', '-c', 'user.email=tests@example.invalid',
                                 '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-qm', 'initial')):
        subprocess.run(['git', *args], cwd=workspace, check=True, capture_output=True)
    workflows = workspace / 'workflows'
    workflows.mkdir()
    (workflows / 'repeated.py').write_text('''from opencollab import workflow

@workflow(name="repeated")
async def repeated(ctx, inputs):
    for index in range(inputs.get("count", 8)):
        if inputs.get("managed"):
            result = await ctx.agent_run("Reply once.", tools=[], label=f"reply-{index}")
            if result.status != "completed":
                raise RuntimeError(result.reason)
            reply = result.output
        else:
            reply = await ctx.agent("Reply once.", tools=[], label=f"reply-{index}")
        if reply != "finished":
            raise RuntimeError(f"unexpected reply {reply!r}")
    return "finished"
''')
    subprocess.run(['git', 'add', '.'], cwd=workspace, check=True, capture_output=True)
    subprocess.run(['git', '-c', 'user.name=Tests', '-c', 'user.email=tests@example.invalid',
                    '-c', 'commit.gpgsign=false', 'commit', '-qm', 'add workflow'],
                   cwd=workspace, check=True, capture_output=True)
    return workspace


@pytest.mark.parametrize('method', ['agent', 'agent_run', 'parallel', 'nested'])
@pytest.mark.parametrize('supplied', [False, True])
@pytest.mark.parametrize('ending', ['success', 'error', 'cancel'])
async def test_workflow_session_paths_release_only_owned_environments(
    workflow_workspace, method, supplied, ending,
):
    started = asyncio.Event()

    class ReplyLLM:
        async def complete(self, messages, **kwargs):
            started.set()
            if ending == 'cancel':
                await asyncio.Event().wait()
            if ending == 'error':
                raise ValueError('offline model failure')
            return LLMResponse(content='finished', usage=Usage(4, 2), finish_reason='stop')

    async def child(ctx, args):
        return await ctx.agent('Reply once.', tools=[])

    async def flow(ctx, args):
        if method == 'nested':
            result = await ctx.candidate_workflow(child, {}, label='child')
            output = result.output
        elif method == 'parallel':
            output = await ctx.parallel([lambda: ctx.agent('Reply once.', tools=[]) for _ in range(3)])
        elif method == 'agent_run':
            output = (await ctx.agent_run('Reply once.', tools=[])).output
        else:
            output = await ctx.agent('Reply once.', tools=[])
        if ending == 'error':
            raise ValueError('workflow failed')
        return output

    external = LocalEnvironment(str(workflow_workspace)) if supplied else None
    baseline = _directory_fds()
    try:
        for _ in range(3):
            started.clear()
            task = asyncio.create_task(run_workflow(
                flow, {}, cfg={'model': 'offline', 'provider': 'openai'},
                workspace=str(workflow_workspace), env=external, llm=ReplyLLM(), trace=False,
            ))
            try:
                await asyncio.wait_for(started.wait(), 5)
                if ending == 'cancel':
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                elif ending == 'error':
                    with pytest.raises(ValueError, match='workflow failed'):
                        await task
                else:
                    result = await task
                    assert result == (['finished'] * 3 if method == 'parallel' else 'finished')
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            assert _directory_fds() == baseline
            if external is not None:
                await external.write_file('caller.txt', 'usable')
                assert await external.read_file('caller.txt') == 'usable'
    finally:
        if external is not None:
            await external.cleanup()


@pytest.mark.parametrize('managed', [False, True])
def test_repeated_real_cli_workflows_release_agent_workspaces(monkeypatch, workflow_workspace, managed):
    requests = []

    def respond(request):
        requests.append(request)
        return completion_http_response(request, text='finished')

    install_sdk_transport(monkeypatch, 'openai', respond)
    baseline = _directory_fds()
    for _ in range(3):
        result = CliRunner().invoke(workflow_app, [
            'run', 'repeated', '--workspace', str(workflow_workspace), '--model', 'offline', '--provider', 'openai',
            '--api-key', 'offline-unused', '--base-url', 'https://offline.invalid/v1', '--no-save', '--no-trace',
            '--args', json.dumps({'managed': managed}),
        ])
        assert result.exit_code == 0, (result.output, result.exception)
        assert 'finished' in result.output
        assert _directory_fds() == baseline
    assert len(requests) == 24


@pytest.mark.parametrize('managed', [False, True])
def test_cli_many_agents_fit_a_low_descriptor_limit(workflow_workspace, managed):
    probe = Path(__file__).parents[1] / 'support' / 'workflow_fd_limit_probe.py'
    completed = subprocess.run(
        [sys.executable, str(probe), str(workflow_workspace), str(int(managed))],
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    receipt = json.loads(completed.stdout)
    assert receipt == {'exit_code': 0, 'requests': 80, 'remaining_directory_fds': 0}
