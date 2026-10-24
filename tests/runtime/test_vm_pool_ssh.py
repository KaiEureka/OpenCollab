from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

import pytest
import pytest_asyncio

from opencollab.adapters._env_vm import VMEnvironment, VMTransportError
from opencollab.adapters._git_patch_bytes import _patch_temporary
from opencollab.adapters._vm_leases import VMCapacityError, _VMLeases
from opencollab.adapters.vm_config import VMConnection, VMResource
from opencollab.adapters.vm_isolation import VMIsolationPool
from opencollab.adapters.worktree_pool import WorktreePool


def _repository(path):
    path.mkdir()
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    (path / "answer.txt").write_text("initial\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=Tester", "-c", "user.email=test@example.com",
                    "commit", "--quiet", "-m", "initial"], check=True)
    subprocess.run(["git", "-C", str(path), "tag", "v1.0"], check=True)


@pytest_asyncio.fixture
async def resources():
    configuration = os.environ.get("OPENCOLLAB_TEST_VM_POOL_CONFIG")
    if configuration is None:
        pytest.skip("OPENCOLLAB_TEST_VM_POOL_CONFIG must configure two borrowed Linux guests")
    entries = json.loads(Path(configuration).read_text())["resources"]
    selected = []
    parents = []
    for entry in entries:
        connection = VMConnection(**entry["connection"])
        token = uuid.uuid4().hex
        parent = VMEnvironment(connection, entry["workspace_root"], state_directory=entry["state_directory"])
        root = f"{parent.workspace}/oc-pr2-{token}"
        assert (await parent.exec_cmd(f"mkdir -m 700 -- {shlex.quote(root)}")).returncode == 0
        parents.append((parent, root))
        selected.append(VMResource(entry["resource_id"], connection, root, entry["state_directory"],
                                   f"rm -f -- {shlex.quote(root + '.global')}"))
    assert len(selected) >= 2
    try:
        yield selected
    finally:
        for parent, root in parents:
            await parent.exec_cmd(f"rm -rf -- {shlex.quote(root)} {shlex.quote(root + '.global')}")
            await parent.cleanup()


async def test_ssh_group_worktrees_and_self_contained_candidate_exports(tmp_path, resources):
    source = tmp_path / "source"
    _repository(source)
    (source / "payload.bin").write_bytes(bytes(range(256)) * 4097)
    (source / "shortcut").symlink_to("answer.txt")
    executable = source / "executable"
    executable.write_text("#!/bin/sh\nprintf executable\n")
    executable.chmod(0o755)
    pool = VMIsolationPool(str(source), resources=resources[-1:], lease_database=str(tmp_path / "leases.sqlite"),
                           owned_save_dir=str(tmp_path / "results"))
    first, second = await pool.acquire("first"), await pool.acquire("second")
    assert first.resource_id == second.resource_id
    assert first.workspace != second.workspace
    assert (await first.exec_cmd("printf changed > answer.txt; printf log > run.log")).returncode == 0
    assert (await second.exec_cmd("cat answer.txt")).stdout == "initial\n"
    child_pool = WorktreePool(first.workspace, use_worktrees=True, base_environment=first)
    child = await child_pool.acquire("nested")
    assert child.workspace != first.workspace
    assert (await child.exec_cmd("cat answer.txt")).stdout == "changed"
    assert (await child.exec_cmd("printf child > answer.txt")).returncode == 0
    assert (await first.exec_cmd("cat answer.txt")).stdout == "changed"
    await child_pool.release()
    before = await first.capture_changes()
    assert (await first.exec_cmd("printf second > answer.txt; "
                                "python3 -c \"from pathlib import Path; p=Path('payload.bin'); "
                                "data=bytearray(p.read_bytes()); data[3]=77; p.write_bytes(data)\"")).returncode == 0
    after = await first.capture_changes()
    increment = await first.diff_captured(before, after)
    assert "+second" in increment
    assert "GIT binary patch" in increment
    destination = Path(first.export_directory)
    await pool.release()
    expected = bytearray((source / "payload.bin").read_bytes())
    expected[3] = 77
    assert (destination / "payload.bin").read_bytes() == expected
    assert (destination / "shortcut").is_symlink()
    assert (destination / "executable").stat().st_mode & 0o777 == 0o755
    assert (destination / "run.log").read_text() == "log"
    assert subprocess.check_output(["git", "-C", str(destination), "show", "HEAD:answer.txt"], text=True) == "initial\n"
    assert subprocess.check_output(["git", "-C", str(destination), "describe", "--tags"], text=True).strip() == "v1.0"


async def test_ssh_two_guests_and_independent_controller_compete_for_stable_resource(tmp_path, resources):
    source = tmp_path / "source"
    _repository(source)
    database = str(tmp_path / "leases.sqlite")
    pool = VMIsolationPool(str(source), resources=resources, lease_database=database, scope="session")
    first, second = await asyncio.gather(pool.acquire("first"), pool.acquire("second"))
    assert first.resource_id != second.resource_id
    identities = await asyncio.gather(first.exec_cmd("cat /proc/sys/kernel/random/boot_id"),
                                      second.exec_cmd("cat /proc/sys/kernel/random/boot_id"))
    assert identities[0].stdout.strip() != identities[1].stdout.strip()
    encoded = json.dumps({"resources": [asdict(resource) for resource in resources], "database": database,
                          "workspace": str(source)})
    code = """
import asyncio, json, sys
from opencollab.bootstrap.vm import VMConnection, VMResource
from opencollab.adapters.vm_isolation import VMIsolationPool, VMCapacityError
config = json.loads(sys.argv[1])
resources = [VMResource(**{**item, 'connection': VMConnection(**item['connection'])}) for item in config['resources']]
async def main():
    pool = VMIsolationPool(config['workspace'], resources=resources, lease_database=config['database'])
    try:
        await pool.acquire('independent-controller')
    except VMCapacityError:
        print('capacity exhausted')
    finally:
        await pool.release()
asyncio.run(main())
"""
    result = await asyncio.to_thread(subprocess.check_output, [sys.executable, "-c", code, encoded], text=True)
    assert result.strip() == "capacity exhausted"
    with pytest.raises(VMCapacityError):
        await pool.acquire("nested-needs-another-guest", source=first)
    marker = shlex.quote(resources[0].workspace_root + ".global")
    assert (await first.exec_cmd("printf dirty > " + marker)).returncode == 0
    await pool.release_env(first)
    next_environment = await pool.acquire("next")
    assert next_environment.resource_id == first.resource_id
    assert (await next_environment.exec_cmd("test ! -e " + marker)).returncode == 0
    await pool.release()


async def test_ssh_unknown_operation_export_failure_and_reset_failure_keep_lease(tmp_path, resources, monkeypatch):
    source = tmp_path / "source"
    _repository(source)
    original = resources[-1]
    ready = original.workspace_root + "/reset-ready"
    marker = original.workspace_root + ".global"
    resource = VMResource(original.resource_id, original.connection, original.workspace_root, original.state_directory,
                          f"test -f {shlex.quote(ready)} && rm -f -- {shlex.quote(marker)}")
    database = str(tmp_path / "leases.sqlite")
    pool = VMIsolationPool(str(source), resources=[resource], lease_database=database)
    environment = await pool.acquire("worker")
    delegate = environment._delegate
    operation_id = uuid.uuid4().hex
    assert (await environment.exec_cmd("printf dirty > " + shlex.quote(marker))).returncode == 0
    await delegate.setup()
    await delegate._rpc({"method": "start", "operation_id": operation_id, "request": {
        "workspace": delegate.workspace, "identity": delegate._identity, "action": "exec",
        "args": {"cmd": "printf candidate > answer.txt; sleep 30", "timeout": 60, "input": None}}})
    delegate._operations.add(operation_id)
    delegate._uncertain.add(operation_id)
    rpc = delegate._rpc

    async def disconnected(payload):
        raise VMTransportError("connection interrupted", operation_id=operation_id)

    monkeypatch.setattr(delegate, "_rpc", disconnected)
    with pytest.raises(OSError, match="retained"):
        await pool.release()
    with pytest.raises(VMCapacityError):
        await _VMLeases(database, [resource], 0).acquire("other-controller")
    monkeypatch.setattr(delegate, "_rpc", rpc)
    transfer = VMEnvironment.copy_to_host

    async def fail_export(self, source, target, *, directory=False):
        raise OSError("export destination unavailable")

    monkeypatch.setattr(VMEnvironment, "copy_to_host", fail_export)
    with pytest.raises(OSError, match="retained"):
        await pool.release()
    observer = VMEnvironment(resource.connection, resource.workspace_root, state_directory=resource.state_directory)
    assert (await observer.exec_cmd("test -d " + shlex.quote(environment.workspace))).returncode == 0
    monkeypatch.setattr(VMEnvironment, "copy_to_host", transfer)
    with pytest.raises(OSError, match="retained"):
        await pool.release()
    assert (await observer.exec_cmd("test -f " + shlex.quote(marker))).returncode == 0
    with pytest.raises(VMCapacityError):
        await _VMLeases(database, [resource], 0).acquire("other-controller")
    assert (await observer.exec_cmd("touch " + shlex.quote(ready))).returncode == 0
    await pool.release()
    assert (Path(environment.export_directory) / "answer.txt").read_text() == "candidate"
    assert (await observer.exec_cmd("test ! -e " + shlex.quote(marker))).returncode == 0
    assert not pool.retained_resources
    await observer.cleanup()


async def test_ssh_git_temporary_allows_atomic_files_and_rejects_replaced_directory(resources):
    resource = resources[-1]
    environment = VMEnvironment(resource.connection, resource.workspace_root, state_directory=resource.state_directory)
    outside = resource.workspace_root + "/outside"
    assert (await environment.exec_cmd("printf keep > " + shlex.quote(outside))).returncode == 0
    async with _patch_temporary(environment, environment.workspace, "before", prefix="git-", suffix=".index",
                                repository=False) as path:
        directory = str(Path(path).parent)
        old_inode = (await environment.exec_cmd("stat -c %i " + shlex.quote(path))).stdout
        assert (await environment.exec_cmd("stat -c %a " + shlex.quote(directory))).stdout.strip() == "700"
        command = (f"printf replacement > {shlex.quote(path + '.new')} && "
                   f"mv {shlex.quote(path + '.new')} {shlex.quote(path)} && "
                   f"mkdir {shlex.quote(path + '.parts')} && "
                   f"ln -s {shlex.quote(outside)} {shlex.quote(path + '.parts/link')}")
        assert (await environment.exec_cmd(command)).returncode == 0
        assert (await environment.exec_cmd("stat -c %i " + shlex.quote(path))).stdout != old_inode
    assert (await environment.exec_cmd("test ! -e " + shlex.quote(directory))).returncode == 0
    assert (await environment.exec_cmd("cat " + shlex.quote(outside))).stdout == "keep"
    context = environment._mutable_temporary("before", prefix="git-", suffix=".index")
    path = await context.__aenter__()
    directory = str(Path(path).parent)
    command = (f"mv {shlex.quote(directory)} {shlex.quote(directory + '.saved')} && "
               f"mkdir {shlex.quote(directory)} && printf replacement > {shlex.quote(directory + '/other')}")
    assert (await environment.exec_cmd(command)).returncode == 0
    with pytest.raises(OSError, match="directory was replaced"):
        await context.__aexit__(None, None, None)
    assert (await environment.exec_cmd("cat " + shlex.quote(directory + "/other"))).stdout == "replacement"
    assert environment._mutable_temporaries
    command = f"rm -r {shlex.quote(directory)} && mv {shlex.quote(directory + '.saved')} {shlex.quote(directory)}"
    assert (await environment.exec_cmd(command)).returncode == 0
    await environment.cleanup()
    assert not environment._mutable_temporaries
