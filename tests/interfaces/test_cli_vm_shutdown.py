from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

import pytest

from tests.support.paths import REPO_ROOT

_DRIVER = '''import asyncio, importlib, json, sys
from pathlib import Path
entry, path, requested, delay = sys.argv[1:]
root = Path(path)
module = importlib.import_module("opencollab.adapters.cli." + ("main" if entry == "team" else "workflow"))
shutdown = module.run_with_bounded_shutdown

def remember(awaitable, **kwargs):
    (root / "shutdown.json").write_text(json.dumps(kwargs))
    return shutdown(awaitable, **kwargs)

async def run(*args, **kwargs):
    try:
        (root / "entered").write_text("ready")
        await asyncio.sleep(300)
    finally:
        (root / "cleanup-started").write_text("started")
        await asyncio.sleep(float(delay))
        (root / "cleanup-complete").write_text("complete")

module.run_with_bounded_shutdown = remember
if entry == "team":
    module._run = run
    arguments = ["--workspace", path, "--prompt", "run"]
else:
    module.run_workflow = run
    arguments = ["workflow", "run", "probe", "--workspace", path, "--no-save", "--no-trace"]
    if requested != "none":
        arguments.extend(["--cleanup-timeout", requested])
importlib.import_module("opencollab.adapters.cli.main").app(args=arguments, standalone_mode=False)
'''


@pytest.mark.parametrize("entry, profile, explicit, expected", [
    ("team", True, None, 6), ("workflow", True, None, 6), ("workflow", True, 4, 4),
    ("team", False, None, 2), ("workflow", False, None, 2),
])
def test_cli_sigint_finishes_cleanup_with_the_selected_grace(tmp_path, entry, profile, explicit, expected):
    (tmp_path / "driver.py").write_text(_DRIVER)
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    (workflows / "probe.py").write_text(
        "from opencollab.workflows import workflow\n@workflow\nasync def probe(ctx, args):\n    return 'unused'\n"
    )
    if profile:
        configs = tmp_path / "configs"
        configs.mkdir()
        value = {"resources": [{"resource_id": "unused", "connection": {"host": "unused.invalid", "user": "worker"}}],
                 "lease_database": str(tmp_path / "unused.sqlite3"), "cleanup_timeout": 6}
        (configs / ".env").write_text("OPENCOLLAB_VM='" + json.dumps(value) + "'\n")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("OPENCOLLAB_")}
    environment.update(PYTHONPATH=str(REPO_ROOT), OPENCOLLAB_API_KEY="offline-cli-test",  # pragma: allowlist secret
                       OPENCOLLAB_API_USAGE_LOG=os.devnull)
    process = subprocess.Popen(
        [sys.executable, str(tmp_path / "driver.py"), entry, str(tmp_path), str(explicit) if explicit else "none",
         "3" if profile else "0.2"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / "entered").exists():
            if process.poll() is not None or time.monotonic() > deadline:
                pytest.fail("CLI failed to enter the run")
            time.sleep(0.02)
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, (stdout, stderr)
        assert (tmp_path / "cleanup-started").exists()
        assert (tmp_path / "cleanup-complete").exists()
        assert json.loads((tmp_path / "shutdown.json").read_text())["shutdown_timeout"] == expected
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()
