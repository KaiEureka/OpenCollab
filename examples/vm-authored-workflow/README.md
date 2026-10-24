# VM member-authored Workflow

[中文](README.zh-CN.md)

Prepare two existing Linux VMs using the [VM guide](../../docs/virtual-machines.md).
Install the same OpenCollab revision on the controller and both guests, then save
a VM profile JSON file with `resources` and `lease_database` as described in the
[session isolation guide](../../docs/session-isolation.md).

```bash
uv run python examples/vm-authored-workflow/demo.py --config /path/to/vm-profile.json
```

The offline HTTP model tells a Team member to write `generated.py`, launch it
with `isolation="worktree"`, and collect its native Workflow result. The first
child agent works in a private Git worktree inside the member VM. The second
explicitly selects `vm-session` and receives another guest. Each agent reads
the source seed, reports its hostname, kernel boot identity and directory, then
creates its own `worker.txt`.

The printed workspace retains parent and child transcripts, the Workflow
manifest, usage, results and exported VM workspaces. Model requests execute in
the controller through the existing authored Workflow bridge. The guests need
their installed runtime and must accept the controller's SSH connection. Model API
credentials and request-pool paths stay in the controller.

Each VM resource needs its administrator's reset command. That command must
restore every guest change allowed by the workload before the guest returns to
the pool. The example writes inside its allocated workspaces and uses the
existing command cleanup. A workload that changes dependencies or system
configuration needs a reset that restores those changes as well.
