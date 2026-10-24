from __future__ import annotations

import asyncio
import base64
import math

import pytest

from opencollab.adapters.env import ExecResult, VMEnvironment, VMOperationError, VMTransportError
from opencollab.domain.vm import VMConnection


def _env():
    return VMEnvironment(VMConnection("guest", "worker"), "/workspace/repo",
                         state_directory="/var/tmp/oc-state", run_id="test")


@pytest.mark.parametrize("kwargs", [{"host": "-oProxyCommand=evil"}, {"user": "a b"}, {"port": True},
                                    {"port": 0}, {"connect_timeout": math.inf}, {"python": "a\nb"}])
def test_connection_rejects_invalid_values(kwargs):
    arguments = {"host": "guest", "user": "worker", **kwargs}
    with pytest.raises(ValueError):
        VMConnection(**arguments)


def test_ssh_keeps_host_verification_and_executable_quoting():
    env = _env()
    arguments = env._ssh_command()
    assert "StrictHostKeyChecking=yes" in arguments
    assert "BatchMode=yes" in arguments
    assert "ControlPath=none" in arguments
    assert arguments[-1] == "python3 -m opencollab.adapters._vm_remote rpc /var/tmp/oc-state test"


async def test_reconnect_reuses_operation_and_preserves_binary_exit_255(monkeypatch):
    env = _env()
    env._identity = {"workspace": env.workspace}
    requests = []
    value = {"returncode": 255, "stdout": "�", "stderr": "command error", "stdout_bytes": "/wA="}

    async def rpc(payload):
        requests.append(payload)
        if len(requests) == 1:
            raise VMTransportError("SSH disconnected", operation_id="once", returncode=255)
        return {"status": "completed", "value": value}

    monkeypatch.setattr(env, "_rpc", rpc)
    result = await env.exec_operation("once", "cat; exit 255", timeout=None, input_bytes=b"\xff\0")
    assert result.returncode == 255
    assert result.stdout_bytes == b"\xff\0"
    assert requests[0] == requests[1]
    assert requests[0]["request"]["args"]["input"] == base64.b64encode(b"\xff\0").decode()
    assert not env.pending_operations


async def test_uncertain_transport_blocks_further_work_until_recovery(monkeypatch):
    env = _env()
    env._identity = {"workspace": env.workspace}

    async def fail(payload):
        raise VMTransportError("SSH unavailable", operation_id=payload["operation_id"], returncode=255)

    monkeypatch.setattr(env, "_rpc", fail)
    with pytest.raises(VMTransportError):
        await env.exec_operation("once", "touch side-effect")
    assert env.revoked
    assert env.pending_operations == ("once",)
    with pytest.raises(VMOperationError, match="Recover uncertain"):
        await env.exec_cmd("touch another")

    async def recovered(payload):
        return {"status": "completed", "action": "exec", "value": {
            "returncode": 0, "stdout": "recovered", "stderr": "", "stdout_bytes": None}}

    monkeypatch.setattr(env, "_rpc", recovered)
    assert (await env.recover_operation("once")).stdout == "recovered"
    assert not env.revoked


async def test_cancel_waits_for_remote_cleanup(monkeypatch):
    env = _env()
    env._identity = {"workspace": env.workspace}
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def rpc(payload):
        if payload["method"] == "start":
            started.set()
            return {"status": "running"}
        if payload["method"] == "cancel":
            cancelled.set()
            return {"status": "cancelled"}
        await asyncio.Future()

    monkeypatch.setattr(env, "_rpc", rpc)
    task = asyncio.create_task(env.exec_operation("once", "sleep 30"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert not env.pending_operations


async def test_failed_cleanup_keeps_operation_and_revocation(monkeypatch):
    env = _env()
    env._operations.add("owned")

    async def fail(payload):
        raise VMTransportError("offline", operation_id="owned")

    monkeypatch.setattr(env, "_rpc", fail)
    with pytest.raises(VMTransportError):
        await env.cleanup()
    assert env.pending_operations == ("owned",)
    assert env.revoked


async def test_recover_file_operation_retains_temporary_identity(monkeypatch):
    env = _env()

    async def rpc(payload):
        return {"status": "completed", "action": "temporary", "args": {"path": "owned.tmp"}, "value": [1, 2]}

    monkeypatch.setattr(env, "_rpc", rpc)
    assert await env.recover_operation("temporary") == [1, 2]
    assert env._temporary_files == {"owned.tmp": [1, 2]}


async def test_remote_result_uses_existing_execution_result():
    result = VMEnvironment._exec_result({"returncode": 0, "stdout": "ok", "stderr": "", "stdout_bytes": "b2s="})
    assert isinstance(result, ExecResult)
    assert result.stdout_bytes == b"ok"


def test_owned_file_removal_rejects_replacement(tmp_path):
    import os

    from opencollab.adapters.safe_anchored_files import open_directory_anchor, unlink_regular_file_durable_at

    target = tmp_path / "owned"
    target.write_text("owned")
    identity = (target.stat().st_dev, target.stat().st_ino)
    target.rename(tmp_path / "saved")
    target.write_text("replacement")
    anchor = open_directory_anchor(tmp_path)
    try:
        with pytest.raises(OSError, match="replaced"):
            unlink_regular_file_durable_at(anchor, tmp_path, "owned", expected_identity=identity)
        assert target.read_text() == "replacement"
    finally:
        os.close(anchor)


def test_helper_framing_ignores_banner_and_retains_invalid_payload_error():
    framed = b'login banner\nOCVM1\n{"ok": true, "value": {"status": "completed"}}\n'
    assert VMEnvironment._decode_response(framed, "once") == {"status": "completed"}
    with pytest.raises(ValueError, match="invalid request"):
        VMEnvironment._decode_response(b'OCVM1\n{"ok": false, "error": "ValueError", '
                                       b'"message": "invalid request"}\n', "once")
    with pytest.raises(VMTransportError, match="incomplete"):
        VMEnvironment._decode_response(b'OCVM1\n{"ok":', "once")
