# Existing Linux virtual machines

`VMEnvironment` attaches to an existing Linux VM over system OpenSSH. The SSH account needs Python 3.10 or later, the same OpenCollab distribution as the controller, Linux child-subreaper and pidfd support, and a complete `/proc` view for its operation descendants. Install the controller's wheel on the guest before connecting. The helper runs from the installed `opencollab.adapters._vm_remote` module and reuses `LocalEnvironment` for commands and file access.

Prepare an existing workspace and a private state directory owned by the SSH account. Keep operation state outside the workspace. Each environment uses a run ID beneath that directory. Preserve the run ID when reconstructing an environment after a controller restart.

```python
from opencollab.environments import VMConnection, VMEnvironment

connection = VMConnection(
    host="vm.example.net",
    user="worker",
    identity_file="/home/controller/.ssh/vm-key",
    known_hosts_file="/home/controller/.ssh/known_hosts",
)
env = VMEnvironment(
    connection,
    "/srv/work/repository",
    state_directory="/var/tmp/opencollab-operations",
    run_id="experiment-42",
)
await env.setup()
result = await env.exec_operation(
    "build-1",
    "python3 -c 'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())'",
    input_bytes=b"binary\x00input",
)
assert result.stdout_bytes == b"binary\x00input"
await env.write_file("notes.txt", "A remote edit\n")
page = await env.read_text_range("notes.txt", offset=1, limit=20, max_chars=4000)
await env.cleanup()
```

Host-key verification is always strict. OpenSSH uses the supplied known-hosts file or its ordinary known-hosts locations. `identity_file` refers to an existing key. `python` can select a guest virtual environment's Python executable. Transport options remain separate from VM resource identity and machine allocation.

Commands retain the existing bounded stdout and stderr behavior, truncation counters, timeout notices, and binary `stdout_bytes`. `input_bytes` supplies binary stdin. A command's exit code 255 remains a command result. SSH failures raise `VMTransportError` carrying `transport_returncode` and `operation_id`. A timeout of `None` or `math.inf` gives the remote command an unlimited duration.

Every operation records its full request, operation ID, supervisor process identity, and terminal result in the private run directory. Submission retries compare the original request and use the same operation ID. A detached supervisor holds the operation lock throughout execution and cleanup, so a disconnected SSH client can reconnect to the same execution. `exec_cmd` allocates an operation ID automatically. `exec_operation` accepts an explicit ID for durable callers.

Use `recover_operation(error.operation_id)` after restoring SSH connectivity. For command operations it returns the original `ExecResult`. File operations return their original value, and temporary-file recovery restores the environment's ownership record. Pending or uncertain operation IDs remain available through `pending_operations`. An uncertain operation prevents further work until recovery settles its state. A missing supervisor after a claimed execution leaves an unknown operation with its evidence retained. Recovery of that state requires inspecting the preserved guest and operation records before the VM is reused.

Cancellation stops the command's process group, then the supervisor's descendants. The Linux subreaper retains ownership when children call `setsid` or double-fork, and pidfds bind termination signals to the observed process identities. A cleanup failure preserves the supervisor and descendant records for a later cleanup retry. Cleanup also removes owned temporary files after checking their recorded device and inode. The VM remains running after environment cleanup.

File operations use the existing anchored regular-file implementation, workspace identity checks, read and write limits, and paginated text reads. `file_write_lock(path)` holds a guest filesystem lock across a complete read-modify-write edit. The lock serializes edits throughout the same physical workspace across controller processes and run IDs. An uncertain file operation retains its edit lock until recovery and cleanup can settle it. Git working-tree probes and diff tools execute through the environment's ordinary command interface.

The opt-in SSH regression suite reads `OPENCOLLAB_TEST_VM_CONFIG` from a JSON file containing `connection`, `workspace`, and `state_directory`. `connection` contains `VMConnection` keyword arguments. The tests create task directories below the supplied workspace, exercise real commands and files, interrupt an SSH client, stop a daemonized descendant, compare concurrent edits, and leave the borrowed guest running. Run the suite with `pytest -q tests/runtime/test_vm_ssh.py` after configuring a dedicated test workspace.

Adapter integrations can call `copy_to_host(source, target, directory=False)` and `copy_from_host(source, destination, directory=False)` to stream binary files or directory archives across SSH beyond command-output limits. Guest source and destination paths are explicit absolute paths selected by trusted workspace adapters, including repository metadata outside an exposed subdirectory. Directory transfers copy contents into an empty or absent destination and preserve the inode of an existing workspace. Regular permissions, symlinks, empty files, and filenames supported by the destination filesystem survive the roundtrip. Setuid and setgid bits are removed when crossing the transfer boundary.

Archive extraction uses an owned staging directory on the destination filesystem and descriptor-relative publication. Parent traversal, repeated paths, hard links, device entries, and writes through symlink parents fail with the staging retained. Upload chunks carry an explicit completion frame before a durable import can execute. An interrupted upload keeps its operation ID and partial archive for inspection or cleanup. Operation records also retain failed import staging paths. Copying a directory into an existing populated destination raises `FileExistsError`.

For SDK, CLI and Team execution, select `vm-group` or `vm-session` through the
existing isolation setting. The [session isolation guide](session-isolation.md#vm-strategies)
describes `VMResource`, the public `vm_isolation_provider` helper, serializable
`config["vm"]` profiles and `OPENCOLLAB_VM`. Both scopes use the same local lease
database and stable resource identities. Returned guests run the administrator's
reset command before their leases become available again. A process restart
preserves persistent leases for administrator recovery after verifying remote
process cleanup and resetting the guest.

The [member-authored VM example](../examples/vm-authored-workflow/README.md)
starts with a Team member writing Workflow source inside its own guest. Its
child default selects `worktree`, and an explicit `vm-session` call allocates
another VM. Model requests use the existing host bridge while native results
and Session records return to the controller. Run the public integration suite
with `pytest -q tests/interfaces/test_sdk_vm_isolation.py` after setting
`OPENCOLLAB_TEST_VM_PROFILE` to a JSON profile for two dedicated guests.
