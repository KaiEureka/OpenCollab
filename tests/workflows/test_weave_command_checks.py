"""Weave checks execute declared behavior and retain native test proof."""

from __future__ import annotations

import shlex
import sys

import pytest

from opencollab import OpenCollab
from opencollab.builtin_workflows import WeaveAdapter
from tests.workflows.test_weave_execution import FileModel


async def _check(workspace, command, seconds=10):
    async def flow(ctx, _args):
        check = await WeaveAdapter(check_commands=[command]).verify(ctx, None, seconds)
        return {"ok": check.ok, "executed": check.executed, "report": check.report}

    result = await OpenCollab(workspace, model="offline").workflow(flow, trace=False, timeout=20)
    assert result.ok, result.reason
    return result.output


@pytest.mark.parametrize("passed", [False, True])
@pytest.mark.parametrize("wrapper", ["", "env CHECK_ENV=yes "])
async def test_echo_prefix_executes_actual_behavior_probe(tmp_path, passed, wrapper):
    (tmp_path / "check.py").write_text(
        "from pathlib import Path\n"
        "Path('executed').write_text('yes')\n"
        f"assert {passed!r}\n"
        "print('CHECK_OK')\n",
        encoding="utf-8",
    )
    command = f"echo 'Starting check' && {wrapper}{shlex.quote(sys.executable)} check.py"
    output = await _check(tmp_path, {"command": command, "expected_output": "CHECK_OK"})
    assert (tmp_path / "executed").read_text() == "yes"
    assert output["executed"] is True
    assert output["ok"] is passed


@pytest.mark.parametrize("command", [
    "echo CHECK_OK", "printf CHECK_OK", "echo Starting && echo CHECK_OK",
    "echo Starting && printf CHECK_OK", "echo 'Starting && python check.py CHECK_OK'",
    "echo CHECK_OK # && python check.py", "echo CHECK_OK && echo '&&' python check.py",
    "echo CHECK_OK | cat", "echo CHECK_OK; printf CHECK_OK", "echo Starting && /bin/echo CHECK_OK",
    "echo Starting && env printf CHECK_OK", "echo Starting && command printf CHECK_OK",
    "echo Starting && /usr/bin/env CHECK_ENV=yes printf CHECK_OK",
    "echo Starting && (printf CHECK_OK)", "echo Starting && { printf CHECK_OK; }",
])
async def test_static_printing_compounds_remain_unverified(tmp_path, command):
    output = await _check(tmp_path, {"command": command, "expected_output": "CHECK_OK"})
    assert output["executed"] is False
    assert output["ok"] is False


@pytest.mark.parametrize("group", ["({command})", "{{ {command}; }}"])
async def test_grouped_behavior_probes_remain_unverified(tmp_path, group):
    (tmp_path / "check.py").write_text(
        "from pathlib import Path\n"
        "Path('executed').write_text('yes')\n"
        "assert 6 * 7 == 42\n"
        "print('CHECK_OK')\n",
        encoding="utf-8",
    )
    command = "echo Starting && " + group.format(command=f"{shlex.quote(sys.executable)} check.py")
    output = await _check(tmp_path, {"command": command, "expected_output": "CHECK_OK"})
    assert output["executed"] is False and output["ok"] is False
    assert not (tmp_path / "executed").exists()


@pytest.mark.parametrize("quote", ["'", '"'])
async def test_quoted_group_characters_preserve_real_probe_execution(tmp_path, quote):
    target = "check (result) {value}.py"
    (tmp_path / target).write_text(
        "from pathlib import Path\nimport sys\n"
        "assert sys.argv[1] == 'argument (result) {value}'\n"
        "Path('executed').write_text('yes')\n"
        "print('CHECK_OK')\n",
        encoding="utf-8",
    )
    command = f"echo {quote}Starting (check) {{value}}{quote} && {shlex.quote(sys.executable)} "
    command += f"{quote}{target}{quote} {quote}argument (result) {{value}}{quote}"
    output = await _check(tmp_path, {"command": command, "expected_output": "CHECK_OK"})
    assert output["executed"] is True and output["ok"] is True
    assert (tmp_path / "executed").read_text() == "yes"


@pytest.mark.parametrize("target", ["test_result.py", "test result.py"])
@pytest.mark.parametrize("separator", [False, True])
async def test_documented_quiet_pytest_verifies_actual_target(tmp_path, target, separator):
    (tmp_path / target).write_text(
        "from pathlib import Path\n"
        "def test_result():\n"
        "    Path('test-executed').write_text('yes')\n"
        "    assert Path('result.txt').read_text() == 'correct\\n'\n",
        encoding="utf-8",
    )
    command = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider "
    command += ("-- " if separator else "") + shlex.quote(target)
    result = await OpenCollab(tmp_path, model="offline").workflow(
        "weave", {"groups": [{"id": "file", "prompt": "write-stage writes result.txt"}],
                  "check_commands": [command], "config": {"max_steps": 4, "max_repair_rounds": 0}},
        llm=FileModel(initial="correct\n"), budget=20_000, limit_mode="explicit", trace=False, timeout=20,
    )
    assert result.ok, result.reason
    assert (tmp_path / "test-executed").read_text() == "yes"
    assert result.output["delivery_ok"] is True
    row = result.output["verification"]["report"]["checks"][0]
    assert row["command"] == command
    assert row["test_records"] and all(record["verified"] for record in row["test_records"])
    assert f"PASSED {target}::test_result" in row["output"]


async def test_quiet_pytest_preserves_cd_quotes_variables_and_selected_test(tmp_path, monkeypatch):
    directory = tmp_path / "check directory"
    directory.mkdir()
    (directory / "test result.py").write_text(
        "from pathlib import Path\n"
        "def test_selected():\n"
        "    Path('selected').write_text('yes')\n"
        "    assert 6 * 7 == 42\n"
        "def test_other():\n"
        "    raise AssertionError('selection was lost')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OC_TEST_SELECTION", "test_selected")
    command = f"cd -- 'check directory' && {shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider "
    command += '-k "$OC_TEST_SELECTION" -- "test result.py"'
    output = await _check(tmp_path, command)
    assert output["ok"] is True
    assert (directory / "selected").read_text() == "yes"
    row = output["report"]["checks"][0]
    assert row["executed_command"] == command.replace('-- "test result.py"', '-rA -- "test result.py"')
    assert "1 passed, 1 deselected" in row["output"]


@pytest.mark.parametrize("mode", ["passed", "failed", "empty", "help", "collect", "compound", "redirect", "suffix"])
@pytest.mark.parametrize("prefix", ["", "echo Starting && "])
async def test_pytest_adverse_results_and_unsupported_shell_remain_unverified(tmp_path, mode, prefix):
    (tmp_path / "test_result.py").write_text(
        "from pathlib import Path\n"
        "def test_result():\n"
        "    Path('test-executed').write_text('yes')\n"
        f"    assert {mode != 'failed'!r}\n",
        encoding="utf-8",
    )
    (tmp_path / "empty").mkdir()
    command = f"{prefix}{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider "
    command += {"empty": "empty", "help": "--help", "collect": "test_result.py --collect-only"}.get(
        mode, "test_result.py")
    command += {"compound": " && echo CHECK_OK", "redirect": " > result.log", "suffix": "; echo CHECK_OK"}.get(mode, "")
    expected = {"passed": "passed", "empty": "no tests ran", "help": "usage", "collect": "collected", "redirect": "",
                "compound": "CHECK_OK", "suffix": "CHECK_OK"}.get(mode, "failed")
    output = await _check(tmp_path, {"command": command, "expected_output": expected or "CHECK_OK"})
    assert output["ok"] is (mode == "passed" and not prefix)
    row = output["report"]["checks"][0]
    if mode in {"help", "collect"}:
        assert output["executed"] is False
        assert not (tmp_path / "test-executed").exists()
    else:
        assert output["executed"] is True
        if mode != "empty":
            assert (tmp_path / "test-executed").read_text() == "yes"
        if prefix or mode in {"compound", "redirect", "suffix"}:
            assert not row["test_records"]
            assert "executed_command" not in row
            assert "requires per-target test evidence" in row["reason"]


async def test_timed_out_behavior_probe_retains_execution_without_passing(tmp_path):
    (tmp_path / "check.py").write_text(
        "from pathlib import Path\nimport time\n"
        "Path('started').write_text('yes')\nprint('CHECK_OK', flush=True)\n"
        "time.sleep(5)\nPath('late').write_text('yes')\n",
        encoding="utf-8",
    )
    command = f"echo Starting && {shlex.quote(sys.executable)} check.py"
    output = await _check(tmp_path, {"command": command, "expected_output": "CHECK_OK"}, seconds=.2)
    assert (tmp_path / "started").read_text() == "yes"
    assert not (tmp_path / "late").exists()
    assert output["executed"] is True and output["ok"] is False
    assert output["report"]["checks"][0]["exit_code"] != 0


async def test_truncated_pytest_output_retains_failed_evidence(tmp_path, monkeypatch):
    from opencollab.adapters import _env_local

    monkeypatch.setattr(_env_local, "PROCESS_OUTPUT_CAPTURE_BYTES", 512)
    (tmp_path / "test_result.py").write_text(
        "from pathlib import Path\n"
        "def test_result():\n"
        "    Path('test-executed').write_text('yes')\n"
        "    print('x' * 5000)\n"
        "    assert 6 * 7 == 42\n",
        encoding="utf-8",
    )
    command = f"{shlex.quote(sys.executable)} -m pytest test_result.py -q -s -p no:cacheprovider"
    output = await _check(tmp_path, command)
    assert (tmp_path / "test-executed").read_text() == "yes"
    row = output["report"]["checks"][0]
    assert row["exit_code"] == 0
    assert "1 passed" in row["output"]
    assert row["test_records"] and all(not record["verified"] for record in row["test_records"])
    assert output["executed"] is True and output["ok"] is False
