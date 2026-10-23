"""Exercise the real CLI with more agent calls than available descriptors."""

from __future__ import annotations

import json
import os
import resource
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from typer.testing import CliRunner

from opencollab.adapters.cli.workflow import app
from tests.support.provider_sdk_http import completion_http_response, install_sdk_transport


def _directories() -> int:
    count = 0
    for fd in range(64):
        try:
            count += stat.S_ISDIR(os.fstat(fd).st_mode)
        except OSError:
            pass
    return count


def main() -> None:
    requests = 0

    def respond(request):
        nonlocal requests
        requests += 1
        return completion_http_response(request, text='finished')

    with pytest.MonkeyPatch.context() as monkeypatch:
        for name in tuple(os.environ):
            if name.startswith(('OPENCOLLAB_', 'OPENAI_', 'ANTHROPIC_', 'DASHSCOPE_')):
                monkeypatch.delenv(name)
        install_sdk_transport(monkeypatch, 'openai', respond)
        baseline = _directories()
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, resource.getrlimit(resource.RLIMIT_NOFILE)[1]))
        result = CliRunner().invoke(app, [
            'run', 'repeated', '--workspace', sys.argv[1], '--model', 'offline', '--provider', 'openai',
            '--api-key', 'offline-unused', '--base-url', 'https://offline.invalid/v1', '--no-save', '--no-trace',
            '--args', json.dumps({'count': 80, 'managed': bool(int(sys.argv[2]))}),
        ])
        assert result.exit_code == 0, (result.output, result.exception)
        print(json.dumps({'exit_code': result.exit_code, 'requests': requests,
                          'remaining_directory_fds': _directories() - baseline}))


if __name__ == '__main__':
    main()
