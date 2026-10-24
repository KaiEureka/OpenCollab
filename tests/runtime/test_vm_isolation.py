from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from opencollab.adapters import candidate_workspace, vm_isolation
from opencollab.adapters._env_vm import VMEnvironment
from opencollab.adapters._vm_leases import VMCapacityError, _VMLeases
from opencollab.adapters.env import LocalEnvironment
from opencollab.adapters.session_environments import SessionEnvironmentPool
from opencollab.adapters.vm_isolation import VMIsolationPool
from opencollab.bootstrap.vm import vm_isolation_provider
from opencollab.domain.vm import VMConnection
from opencollab.domain.vm_resources import VMResource


def _repository(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    (path / "answer.txt").write_text("initial\n")
    for arguments in (("add", "."), ("-c", "user.name=Tester", "-c", "user.email=test@example.com",
                                         "commit", "--quiet", "-m", "initial")):
        subprocess.run(["git", "-C", str(path), *arguments], check=True)


def _resource(path: Path, resource_id: str = "vm-one", *, reset_command: str = "true") -> VMResource:
    path.mkdir(exist_ok=True)
    return VMResource(resource_id, VMConnection("localhost", "test"), str(path), str(path.parent / "state"),
                      reset_command)


class _LocalVM(VMEnvironment):
    copy_failure = False

    def __init__(self, connection, workspace, *, state_directory, run_id=None):
        super().__init__(connection, workspace, state_directory=state_directory, run_id=run_id)
        self.local = LocalEnvironment(workspace)

    async def setup(self, mount_dir=None):
        self._ensure_active()
        return self.workspace

    async def exec_cmd(self, cmd, timeout=120, *, input_bytes=None):
        self._ensure_active()
        return await self.local.exec_cmd(cmd, timeout, input_bytes=input_bytes)

    async def write_temp_file(self, *args, **kwargs):
        return await self.local.write_temp_file(*args, **kwargs)

    async def remove_file(self, path):
        await self.local.remove_file(path)

    async def copy_to_host(self, source, destination, *, directory=False):
        if self.copy_failure:
            raise OSError("transfer interrupted")
        if directory:
            shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copyfile(source, destination)

    async def copy_from_host(self, source, destination, *, directory=False):
        if directory:
            shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copyfile(source, destination)

    async def cleanup(self):
        self.revoke()
        await self.local.cleanup()


@pytest.fixture
def local_vm(monkeypatch):
    monkeypatch.setattr(vm_isolation, "VMEnvironment", _LocalVM)
    monkeypatch.setattr(candidate_workspace, "VMEnvironment", _LocalVM)
    monkeypatch.setattr(_LocalVM, "copy_failure", False)


async def test_database_claim_is_exclusive_across_controller_processes_and_connection_aliases(tmp_path):
    resource = _resource(tmp_path / "guest")
    database = str(tmp_path / "leases.sqlite")
    leases = _VMLeases(database, [resource], 0)
    await leases.acquire("parent")
    code = """
import asyncio, sys
from opencollab.adapters._vm_leases import _VMLeases, VMCapacityError
from opencollab.domain.vm import VMConnection
from opencollab.domain.vm_resources import VMResource
resource = VMResource('vm-one', VMConnection('different-alias', 'test'), sys.argv[2], reset_command='true')
async def main():
    try:
        await _VMLeases(sys.argv[1], [resource], 0).acquire('child')
    except VMCapacityError:
        print('exhausted')
    else:
        print('claimed')
asyncio.run(main())
"""
    def child():
        return subprocess.check_output(
            [sys.executable, "-c", code, database, resource.workspace_root], text=True,
        ).strip()
    assert await asyncio.to_thread(child) == "exhausted"
    leases.release(resource, "parent")
    assert await asyncio.to_thread(child) == "claimed"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT resource_id, owner FROM vm_leases").fetchall() == [("vm-one", "child")]


async def test_resource_waiting_is_cancellable_without_claiming_the_occupied_vm(tmp_path):
    resource = _resource(tmp_path / "guest")
    database = str(tmp_path / "leases.sqlite")
    immediate = _VMLeases(database, [resource], 0)
    await immediate.acquire("parent")
    waiting = _VMLeases(database, [resource], None)
    task = asyncio.create_task(waiting.acquire("cancelled"))
    await asyncio.sleep(0.08)
    task.cancel("stop nested run")
    with pytest.raises(asyncio.CancelledError, match="stop nested run"):
        await task
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT owner FROM vm_leases").fetchall() == [("parent",)]
    immediate.release(resource, "parent")
    assert await waiting.acquire("next") == resource


async def test_group_sessions_are_separate_and_exports_keep_binary_symlinks_and_history(tmp_path, local_vm):
    source = tmp_path / "source"
    _repository(source)
    (source / "payload.bin").write_bytes(b"\0\xff\x80")
    (source / "shortcut").symlink_to("answer.txt")
    (source / "executable").write_text("#!/bin/sh\nexit 0\n")
    (source / "executable").chmod(0o755)
    pool = VMIsolationPool(str(source), resources=[_resource(tmp_path / "guest")],
                           lease_database=str(tmp_path / "leases.sqlite"), owned_save_dir=str(tmp_path / "results"))
    first, second = await pool.acquire("first"), await pool.acquire("second")
    assert first.resource_id == second.resource_id == "vm-one"
    assert first.workspace != second.workspace
    await first.exec_cmd("printf 'changed\\n' > answer.txt && printf 'log\\n' > run.log")
    assert (await second.exec_cmd("cat answer.txt")).stdout == "initial\n"
    destination = Path(first.export_directory)
    await pool.release()
    assert not pool.retained_resources
    assert (destination / "payload.bin").read_bytes() == b"\0\xff\x80"
    assert (destination / "shortcut").is_symlink()
    assert os.readlink(destination / "shortcut") == "answer.txt"
    assert (destination / "executable").stat().st_mode & 0o777 == 0o755
    assert (destination / "run.log").read_text() == "log\n"
    assert subprocess.check_output(["git", "-C", str(destination), "show", "HEAD:answer.txt"], text=True) == "initial\n"
    assert (source / "answer.txt").read_text() == "initial\n"


async def test_session_scopes_claim_different_vms_and_nested_exhaustion_is_clear(tmp_path, local_vm):
    source = tmp_path / "source"
    _repository(source)
    resources = [_resource(tmp_path / "guest-one", "one"), _resource(tmp_path / "guest-two", "two")]
    provider = vm_isolation_provider(resources, lease_database=str(tmp_path / "leases.sqlite"), scope="session")
    sessions = SessionEnvironmentPool(str(source), isolation="vm-session", providers={"vm-session": provider})
    first, second = await sessions.acquire("first"), await sessions.acquire("second")
    assert first.resource_id != second.resource_id
    child = sessions.child_pool(first, role="child")
    with pytest.raises(VMCapacityError, match="capacity exhausted"):
        await child.acquire("nested")
    await child.release()
    await sessions.release()


async def test_export_failure_retains_workspace_and_lease_until_retry(tmp_path, local_vm, monkeypatch):
    source = tmp_path / "source"
    _repository(source)
    resource = _resource(tmp_path / "guest")
    database = str(tmp_path / "leases.sqlite")
    pool = VMIsolationPool(str(source), resources=[resource], lease_database=database)
    environment = await pool.acquire("worker")
    await environment.exec_cmd("printf 'candidate\\n' > answer.txt")
    monkeypatch.setattr(_LocalVM, "copy_failure", True)
    with pytest.raises(OSError, match="retained"):
        await pool.release()
    assert Path(environment.workspace).is_dir()
    assert pool.retained_resources == (resource.resource_id,)
    with pytest.raises(VMCapacityError):
        await _VMLeases(database, [resource], 0).acquire("other")
    monkeypatch.setattr(_LocalVM, "copy_failure", False)
    await pool.release()
    assert (Path(environment.export_directory) / "answer.txt").read_text() == "candidate\n"
    assert not Path(environment.workspace).exists()


async def test_reset_failure_retains_lease_and_restores_guest_before_next_claim(tmp_path, local_vm):
    source = tmp_path / "source"
    _repository(source)
    marker, ready = tmp_path / "global-state", tmp_path / "reset-ready"
    marker.write_text("dirty")
    resource = _resource(tmp_path / "guest", reset_command=f"test -f '{ready}' && rm -f '{marker}'")
    database = str(tmp_path / "leases.sqlite")
    pool = VMIsolationPool(str(source), resources=[resource], lease_database=database)
    await pool.acquire("worker")
    with pytest.raises(OSError, match="retained"):
        await pool.release()
    assert marker.exists()
    with pytest.raises(VMCapacityError):
        await _VMLeases(database, [resource], 0).acquire("other")
    ready.touch()
    await pool.release()
    assert not marker.exists()
    assert await _VMLeases(database, [resource], 0).acquire("next") == resource


async def test_release_interrupts_configured_capacity_wait(tmp_path, local_vm):
    resource = _resource(tmp_path / "guest")
    database = str(tmp_path / "leases.sqlite")
    registry = _VMLeases(database, [resource], 0)
    await registry.acquire("outer")
    pool = VMIsolationPool(str(tmp_path), resources=[resource], lease_database=database, wait_timeout=None)
    acquisition = asyncio.create_task(pool.acquire("nested"))
    await asyncio.sleep(0.08)
    await asyncio.wait_for(pool.release(), 1)
    with pytest.raises(RuntimeError, match="closed"):
        await acquisition
    registry.release(resource, "outer")


def test_reusable_pool_requires_explicit_guest_reset(tmp_path):
    resource = VMResource("one", VMConnection("localhost", "test"))
    with pytest.raises(ValueError, match="reset_command"):
        VMIsolationPool(str(tmp_path), resources=[resource], lease_database=str(tmp_path / "leases.sqlite"))
