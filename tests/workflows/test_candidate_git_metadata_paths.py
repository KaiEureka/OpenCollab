from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from opencollab.adapters._env_local import LocalEnvironment
from opencollab.adapters.candidate_workspace import EnvCandidateWorkspace
from opencollab.adapters.worktree_pool import WorktreePool


def _git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ['git', '-C', str(root), *arguments], check=True, capture_output=True,
    ).stdout


@pytest.mark.skipif(os.name != 'posix', reason='requires paths with trailing whitespace')
@pytest.mark.parametrize('suffix', [' ', '\t'])
@pytest.mark.parametrize('backend', ['workflow', 'team'])
async def test_candidate_delivery_preserves_separate_git_directory_path(tmp_path, backend, suffix):
    source = tmp_path / 'source'
    source.mkdir()
    metadata = tmp_path / ('Git metadata' + suffix)
    _git(source, 'init', '-q', '--separate-git-dir', str(metadata))
    _git(source, 'config', 'user.name', 'Tests')
    _git(source, 'config', 'user.email', 'tests@example.invalid')
    _git(source, 'config', 'commit.gpgsign', 'false')
    (source / 'answer.py').write_text('print(1)\n')
    _git(source, 'add', '.')
    _git(source, 'commit', '-qm', 'base')
    index = (metadata / 'index').read_bytes()
    registered = _git(source, 'worktree', 'list', '--porcelain', '-z')
    environment = LocalEnvironment(str(source))
    lease = None
    pool = None
    try:
        if backend == 'workflow':
            workspace = EnvCandidateWorkspace(environment)
            lease = await workspace.acquire('metadata-path')
            candidate = lease.environment
        else:
            pool = WorktreePool(
                str(source), use_worktrees=True, base_environment=environment, enable_adoption=True,
            )
            candidate = await pool.acquire('coder')
        await candidate.write_file('answer.py', 'print(42)\n')
        patch = await lease.diff() if lease is not None else await candidate.get_diff()
        if lease is not None:
            await workspace.adopt(patch)
        else:
            await pool.adopt(patch)
        result = subprocess.run(
            [sys.executable, '-B', str(source / 'answer.py')], check=True, capture_output=True,
        )
        assert result.stdout == b'42\n'
        assert (metadata / 'index').read_bytes() == index
    finally:
        if lease is not None:
            await lease.cleanup()
        if pool is not None:
            await pool.release()
        await environment.cleanup()
    assert _git(source, 'worktree', 'list', '--porcelain', '-z') == registered
    assert not list(metadata.glob('opencollab-candidate-*'))
