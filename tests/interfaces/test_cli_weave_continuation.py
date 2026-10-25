"""The CLI can retain the identity of an exported Weave run."""

from __future__ import annotations

import asyncio
import json
import shlex
import sys

import pytest
from typer.testing import CliRunner

from opencollab import OpenCollab
from opencollab.adapters.cli.workflow import app
from tests.support.provider_sdk_http import completion_http_response, install_sdk_transport


@pytest.mark.parametrize("changed", [None, "run_id", "groups"])
async def test_cli_continues_exported_weave_state_with_original_identity(tmp_path, monkeypatch, changed):
    (tmp_path / "check.py").write_text(
        "from pathlib import Path\n"
        "assert 6 * 7 == 42\n"
        "with Path('checks.log').open('a') as stream: stream.write('checked\\n')\n"
        "print('CHECK_OK')\n",
        encoding="utf-8",
    )
    calls = []
    install_sdk_transport(monkeypatch, "openai", lambda request: (
        calls.append(request) or completion_http_response(request, text="ready")))
    args = {"groups": [{"id": "file", "prompt": "The fixture is ready"}],
            "check_commands": [{"command": f"{shlex.quote(sys.executable)} check.py", "expected_output": "CHECK_OK"}],
            "config": {"max_steps": 4, "max_repair_rounds": 0}}
    first = await OpenCollab(
        tmp_path, model="offline", provider="openai", api_key="offline-unused",  # pragma: allowlist secret
        base_url="https://offline.invalid/v1", config={"max_output_tokens": 128},
    ).workflow("weave", args, trace=False, budget=20_000, limit_mode="explicit")
    assert first.ok and first.output["delivery_ok"]
    args["state"] = json.loads(json.dumps(first.output["state"]))
    run_id = args["state"]["run_id"]
    if changed == "run_id":
        run_id += "-other"
    elif changed == "groups":
        args["groups"][0]["prompt"] = "Different work"
    calls_before = len(calls)
    checks_before = (tmp_path / "checks.log").read_text().count("checked\n")
    result = await asyncio.to_thread(lambda: CliRunner().invoke(app, [
        "run", "weave", "--workspace", str(tmp_path), "--args", json.dumps(args),
        "--run-id", run_id, "--model", "offline", "--provider", "openai",
        "--api-key", "offline-unused", "--base-url", "https://offline.invalid/v1",
        "--budget", "20000", "--no-trace",
    ]))
    assert len(calls) == calls_before
    if changed is None:
        assert result.exit_code == 0, (result.stdout, result.exception)
    else:
        assert isinstance(result.exception, ValueError), (result.stdout, result.exception)
    manifest_path, = (tmp_path / ".opencollab/sessions").glob("*/workflow.json")
    manifest = json.loads(manifest_path.read_text())
    assert manifest["run_id"] == run_id
    if changed is not None:
        assert result.exit_code != 0
        assert isinstance(result.exception, ValueError)
        assert "continuation requires" in str(result.exception)
        assert manifest["status"] == "failed"
        assert (tmp_path / "checks.log").read_text().count("checked\n") == checks_before
    else:
        assert result.exit_code == 0, (result.stdout, result.exception)
        assert '"delivery_ok": true' in result.stdout
        assert manifest["status"] == "completed"
        assert (tmp_path / "checks.log").read_text().count("checked\n") > checks_before
