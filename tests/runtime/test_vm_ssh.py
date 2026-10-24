"""Opt-in probes against a borrowed Linux guest with installed OpenCollab."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from opencollab.adapters.env import VMEnvironment
from opencollab.adapters.working_tree import EnvWorkingTreeProbe
from opencollab.domain.vm import VMConnection


@pytest_asyncio.fixture
async def vm():
    config_path = os.environ.get("OPENCOLLAB_TEST_VM_CONFIG")
    if config_path is None:
        pytest.skip("OPENCOLLAB_TEST_VM_CONFIG must identify a borrowed Linux test guest")
    config = json.loads(Path(config_path).read_text())
    connection = VMConnection(**config["connection"])
    parent = VMEnvironment(connection, config["workspace"], state_directory=config["state_directory"])
    child = parent.workspace + "/probe-" + uuid.uuid4().hex
    result = await parent.exec_cmd("mkdir -m 700 -- " + shlex.quote(child))
    assert result.returncode == 0
    await parent.cleanup()
    env = VMEnvironment(connection, child, state_directory=config["state_directory"])
    try:
        await env.setup()
        yield env
    finally:
        await env.cleanup()


async def test_ssh_binary_stdin_exit_255_and_output_bounds(vm):
    result = await vm.exec_cmd("cat; printf stderr >&2; exit 255", input_bytes=b"\x00\xffabc", timeout=None)
    assert result.returncode == 255
    assert result.stdout_bytes == b"\x00\xffabc"
    assert result.stderr == "stderr"
    result = await vm.exec_cmd("python3 -c 'import sys; sys.stdout.buffer.write(b\"A\"*1200000); "
                               "sys.stderr.buffer.write(b\"B\"*1200000)'")
    assert len(result.stdout_bytes) == 1024 * 1024
    assert result.stdout_dropped_bytes == 1200000 - 1024 * 1024
    assert result.stderr_dropped_bytes == 1200000 - 1024 * 1024


async def test_ssh_files_ranges_symlinks_and_owned_temporary(vm):
    await vm.write_file("file.txt", "first\nsecond\nthird\n")
    assert await vm.read_file("file.txt") == "first\nsecond\nthird\n"
    page = await vm.read_text_range("file.txt", offset=2, limit=1, max_chars=100)
    assert page.lines == ["second"]
    assert page.has_more
    assert (await vm.exec_cmd("ln -s /etc/passwd link")).returncode == 0
    with pytest.raises(OSError):
        await vm.read_file("link")
    with pytest.raises(PermissionError):
        await vm.write_file("../outside", "escape")
    temporary = await vm.write_temp_file("owned", prefix="test-")
    with pytest.raises(OSError, match="unowned"):
        await vm.remove_file("file.txt")
    await vm.exec_cmd("mv " + shlex.quote(temporary) + " " + shlex.quote(temporary + ".saved")
                      + "; printf replacement > " + shlex.quote(temporary))
    with pytest.raises(OSError, match="replaced"):
        await vm.remove_file(temporary)
    assert await vm.read_file(temporary) == "replacement"
    await vm.exec_cmd("rm " + shlex.quote(temporary) + "; mv " + shlex.quote(temporary + ".saved")
                      + " " + shlex.quote(temporary))
    await vm.remove_file(temporary)


async def test_ssh_operation_recovery_does_not_repeat_side_effect(vm):
    operation_id = uuid.uuid4().hex
    command = "printf once >> counter; printf before; sleep 0.8; printf after"
    request = {"method": "start", "operation_id": operation_id, "request": {
        "workspace": vm.workspace, "identity": vm._identity, "action": "exec",
        "args": {"cmd": command, "timeout": 20, "input": None}}}
    await vm._rpc(request)
    # Disconnect a real SSH transport while the durable command is running.
    ssh = vm._ssh_command()
    ssh[-1] = "sleep 30; " + ssh[-1]
    transport = await asyncio.create_subprocess_exec(*ssh, stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    transport.stdin.write(json.dumps({"method": "query", "operation_id": operation_id}).encode())
    await transport.stdin.drain()
    transport.stdin.close()
    await asyncio.sleep(0.1)
    transport.kill()
    await transport.wait()
    result = await vm.exec_operation(operation_id, command, timeout=20)
    assert result.stdout == "beforeafter"
    assert await vm.read_file("counter") == "once"
    recovered = VMEnvironment(vm.connection, vm.workspace, state_directory=vm.state_directory, run_id=vm.run_id)
    assert (await recovered.recover_operation(operation_id)).stdout == "beforeafter"
    await recovered.cleanup()


async def test_ssh_cancel_kills_double_forked_daemon_and_preserves_guest(vm):
    code = ("import os,time,signal; p=os.fork(); "
            "os._exit(0) if p else None; os.setsid(); p=os.fork(); "
            "os._exit(0) if p else None; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "open('daemon.pid','w').write(str(os.getpid())); "
            "[(open('ticks','a').write('x'),time.sleep(.05)) for _ in range(1000)]")
    task = asyncio.create_task(vm.exec_cmd("python3 -c " + shlex.quote(code) + "; sleep 30", timeout=None))
    for _ in range(60):
        await asyncio.sleep(0.05)
        if (await vm.exec_cmd("test -f daemon.pid")).returncode == 0:
            break
    else:
        pytest.fail("daemon did not start")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    size = (await vm.exec_cmd("wc -c < ticks")).stdout.strip()
    await asyncio.sleep(0.2)
    assert (await vm.exec_cmd("wc -c < ticks")).stdout.strip() == size
    assert (await vm.exec_cmd("kill -0 $(cat daemon.pid) 2>/dev/null")).returncode != 0
    assert (await vm.exec_cmd("test -r /proc/1/stat")).returncode == 0


async def test_ssh_timeout_preserves_output_and_stops_background(vm):
    result = await vm.exec_cmd("printf before; printf warning >&2; sleep 30", timeout=0.2)
    assert result.timed_out
    assert result.stdout == "before"
    assert "warning" in result.stderr


async def test_ssh_workspace_edits_serialize_across_environment_instances(vm):
    other = VMEnvironment(vm.connection, vm.workspace, state_directory=vm.state_directory)
    async def edit(env):
        async with env.file_write_lock("counter"):
            value = await env.read_file("counter")
            await asyncio.sleep(0.1)
            await env.write_file("counter", str(int(value) + 1))
    await vm.write_file("counter", "0")
    try:
        await asyncio.gather(edit(vm), edit(other))
        assert await vm.read_file("counter") == "2"
    finally:
        await other.cleanup()


async def test_ssh_working_tree_probe_and_diff(vm):
    assert (await vm.exec_cmd("git init -q; git -c user.name=Test -c user.email=test@example.invalid "
                              "commit -q --allow-empty -m initial")).returncode == 0
    probe = EnvWorkingTreeProbe(vm)
    assert not await probe.changed()
    await vm.write_file("new.txt", "new\n")
    assert await probe.changed()
    assert "+new" in await probe.diff()


async def test_ssh_reused_operation_id_rejects_changed_request(vm):
    operation_id = uuid.uuid4().hex
    assert (await vm.exec_operation(operation_id, "printf original")).stdout == "original"
    with pytest.raises(ValueError, match="another request"):
        await vm.exec_operation(operation_id, "printf changed")
    with pytest.raises(FileNotFoundError):
        await vm.recover_operation("absent")


async def test_ssh_recovered_temporary_cleanup_initializes_workspace_identity(vm):
    operation_id = uuid.uuid4().hex
    await vm._operate("temporary", {"path": "recovered.tmp", "content": "owned"}, operation_id)
    recovered = VMEnvironment(vm.connection, vm.workspace, state_directory=vm.state_directory, run_id=vm.run_id)
    try:
        await recovered.recover_operation(operation_id)
    finally:
        await recovered.cleanup()
    assert (await vm.exec_cmd("test ! -e recovered.tmp")).returncode == 0


async def test_ssh_cancelled_temporary_creation_keeps_cleanup_ownership(vm, monkeypatch):
    other = VMEnvironment(vm.connection, vm.workspace, state_directory=vm.state_directory)
    original_rpc = other._rpc
    completed = asyncio.Event()
    release = asyncio.Event()

    async def delay_completed_response(payload):
        response = await original_rpc(payload)
        if response.get("status") == "completed" and response.get("action") == "temporary":
            completed.set()
            await release.wait()
        return response

    monkeypatch.setattr(other, "_rpc", delay_completed_response)
    task = asyncio.create_task(other.write_temp_file("owned", prefix="cancelled-"))
    try:
        await asyncio.wait_for(completed.wait(), 10)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(other._temporary_files) == 1
        temporary = next(iter(other._temporary_files))
        assert (await vm.exec_cmd("test -f " + shlex.quote(temporary))).returncode == 0
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await other.cleanup()
    assert (await vm.exec_cmd("test ! -e " + shlex.quote(temporary))).returncode == 0


async def test_ssh_streaming_copy_preserves_tree_and_binary_files(vm, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    name = "with space\nand \u4e2d\u6587"
    data = b"\0\xffbinary\n" * 200000
    (source / name).write_bytes(data)
    (source / name).chmod(0o775)
    (source / "empty").touch()
    (source / "link").symlink_to(name)
    destination = vm.workspace + "/imported"
    await vm.copy_from_host(source, destination, directory=True)
    downloaded = tmp_path / "downloaded"
    await vm.copy_to_host(destination, downloaded, directory=True)
    assert (downloaded / name).read_bytes() == data
    assert (downloaded / name).stat().st_mode & 0o777 == 0o775
    assert (downloaded / "empty").read_bytes() == b""
    assert os.readlink(downloaded / "link") == name
    await vm.copy_from_host(source / "empty", vm.workspace + "/empty-file")
    await vm.copy_to_host(vm.workspace + "/empty-file", tmp_path / "empty-file")
    assert (tmp_path / "empty-file").read_bytes() == b""
    # A second archive location exercises state and workspace independence.
    separate = VMEnvironment(vm.connection, vm.workspace, state_directory=vm.state_directory + "-separate")
    try:
        await separate.copy_from_host(source / name, vm.workspace + "/separate-binary")
        await separate.copy_to_host(vm.workspace + "/separate-binary", tmp_path / "binary")
        assert (tmp_path / "binary").read_bytes() == data
    finally:
        await separate.cleanup()
