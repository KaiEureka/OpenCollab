from __future__ import annotations

import asyncio
import base64
import math
import sys
import threading

import pytest

from opencollab import __version__
from opencollab.adapters import _env_vm
from opencollab.adapters.env import ExecResult, VMEnvironment, VMOperationError, VMTransportError
from opencollab.adapters.vm_config import VMConnection


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
    identity = {"workspace": env.workspace, "device": 1, "inode": 2, "boot_id": "boot"}
    setup_attempts = 0

    async def rpc(payload):
        nonlocal setup_attempts
        if payload["method"] == "setup":
            setup_attempts += 1
            if setup_attempts == 1:
                raise VMTransportError("offline", operation_id="setup")
            return {"identity": identity, "version": __version__}
        return {"status": "completed", "action": "temporary", "args": {"path": "owned.tmp"}, "value": [1, 2]}

    monkeypatch.setattr(env, "_rpc", rpc)
    with pytest.raises(VMTransportError):
        await env.recover_operation("temporary")
    assert env.revoked
    assert await env.recover_operation("temporary") == [1, 2]
    assert env._identity == identity
    assert env._temporary_files == {"owned.tmp": [1, 2]}
    assert not env.revoked


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


@pytest.mark.parametrize("action", ["setup", "exec", "lock", "upload", "download"])
async def test_cleanup_during_setup_prevents_later_work(monkeypatch, tmp_path, action):
    env = _env()
    entered = asyncio.Event()
    release = asyncio.Event()
    requests = []

    async def rpc(payload):
        requests.append(payload["method"])
        assert payload["method"] == "setup"
        entered.set()
        await release.wait()
        return {"version": __version__, "identity": {"workspace": env.workspace}}

    async def work():
        if action == "setup":
            await env.setup()
        elif action == "exec":
            await env.exec_cmd("touch late")
        elif action == "lock":
            async with env.file_write_lock("late"):
                pytest.fail("edit lock was acquired after cleanup")
        elif action == "upload":
            await env.copy_from_host(tmp_path / "source", "late")
        else:
            await env.copy_to_host("late", tmp_path / "target")

    monkeypatch.setattr(env, "_rpc", rpc)
    task = asyncio.create_task(work())
    await asyncio.wait_for(entered.wait(), 2)
    await env.cleanup()
    release.set()
    with pytest.raises(RuntimeError, match="aborted"):
        await task
    assert requests == ["setup"]
    assert not env.pending_operations
    assert not (tmp_path / "target").exists()


@pytest.mark.parametrize("stage", ["stream", "unpack", "publish"])
async def test_cleanup_waits_for_download_and_local_publication(monkeypatch, tmp_path, stage):
    env = _env()
    env._identity = {"workspace": env.workspace}
    source = tmp_path / "source"
    source.write_bytes(b"downloaded\0\xff")
    archive = tmp_path / "source.tar"
    with archive.open("wb") as output:
        _env_vm.pack(source, output, directory=False)
    marker = tmp_path / "stream-ready"
    code = (
        "import pathlib,struct,sys,time; sys.stdin.buffer.read(); "
        "data=pathlib.Path(sys.argv[1]).read_bytes(); "
        "sys.stdout.buffer.write(b'OCVM1\\n{\"ok\":true,\"value\":{\"stream\":true}}\\n'); "
        "sys.stdout.buffer.flush(); pathlib.Path(sys.argv[2]).touch(); "
        "time.sleep(30) if sys.argv[3]=='stream' else None; "
        "sys.stdout.buffer.write(struct.pack('!I',len(data))+data+struct.pack('!I',0)); "
        "sys.stdout.buffer.flush()"
    )
    monkeypatch.setattr(env, "_ssh_command", lambda _method: [
        sys.executable, "-c", code, str(archive), str(marker), stage])
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    if stage != "stream":
        original = getattr(_env_vm, stage)

        def blocked(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise TimeoutError("test did not release download worker")
            return original(*args, **kwargs)

        monkeypatch.setattr(_env_vm, stage, blocked)

    target = tmp_path / "target"
    task = asyncio.create_task(env.copy_to_host("source", target))
    cleanup = None
    try:
        if stage == "stream":
            async def ready():
                while not marker.exists():
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(ready(), 2)
        else:
            await asyncio.wait_for(entered.wait(), 2)
        cleanup = asyncio.create_task(env.cleanup())
        if stage != "stream":
            await asyncio.wait({cleanup}, timeout=0.05)
            assert not cleanup.done()
        release.set()
        await asyncio.wait_for(cleanup, 2)
        assert task.done()
        with pytest.raises(asyncio.CancelledError):
            await task
        if stage == "publish":
            assert target.read_bytes() == source.read_bytes()
        else:
            assert not target.exists()
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if cleanup is not None:
            await asyncio.gather(cleanup, return_exceptions=True)


async def test_download_cleanup_failure_reaches_environment_cleanup(monkeypatch, tmp_path):
    env = _env()
    env._identity = {"workspace": env.workspace}
    entered = asyncio.Event()

    async def download(*args, **kwargs):
        try:
            entered.set()
            await asyncio.Future()
        finally:
            raise _env_vm.ProcessCleanupError("download process remained alive")

    monkeypatch.setattr(env, "_copy_to_host", download)
    task = asyncio.create_task(env.copy_to_host("source", tmp_path / "target"))
    await asyncio.wait_for(entered.wait(), 2)
    with pytest.raises(_env_vm.ProcessCleanupError, match="remained alive"):
        await env.cleanup()
    with pytest.raises(_env_vm.ProcessCleanupError, match="remained alive"):
        await task
