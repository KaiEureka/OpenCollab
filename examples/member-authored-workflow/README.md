# Member-authored Workflows

A Team member can write Python Workflow source with its normal editing tools,
then execute that file through `manage_workflow`. The source is imported by a
Python process inside that member's execution environment. The entry function
receives the usual `ctx` and `args`, so it can use arbitrary Python control flow
and the existing Workflow operations.

Run the offline example from an installed checkout. It creates a local HTTP
model, has the parent member write `generated.py`, starts that file, and returns
the child result to the parent. Every model request stays on the local machine.

```bash
uv run python examples/member-authored-workflow/demo.py
```

The result includes `Local answer`, a computed total of `15`, and `90` tokens
reported by the scripted model across the parent and child. The printed
workspace retains the generated source, parent transcripts, child Workflow
manifest, usage, and result.

Declare the tool on the role that needs it, together with `bash` and the tools
its Workflow agents will use.

```yaml
entry: author
roles:
  author:
    prompt: Write and run Workflows to complete the task.
    tools: [file_write, file_read, bash, manage_workflow]
```

`manage_workflow` shares the role's shell execution permission. Local host
execution requires the Team caller to enable `allow_unisolated_shell`.
The target environment must provide Python with OpenCollab installed. The
`python` argument selects that environment's executable and defaults to
`python3`. A child session's requested tool names must be available to its
parent member.
Its output caps, shell isolation requirement, and file-creation restriction
also carry into child sessions.

The five actions use the same tool. `launch` returns immediately with a child
identifier belonging to the calling member. `status` observes progress, `wait`
returns a result when execution finishes, `result` reads the retained outcome,
and `stop` waits for child cleanup. An optional `timeout` on `wait` bounds that
wait and leaves the child running.

```json
{"action": "launch", "source": "generated.py", "entry": "main", "args": {"count": 6}}
{"action": "status", "id": "<child id>"}
{"action": "wait", "id": "<child id>", "timeout": 30}
{"action": "result", "id": "<child id>"}
{"action": "stop", "id": "<child id>"}
```

Model credentials and supported generation settings inherit the member's
effective configuration through the child process's standard input. Child usage is charged once to the parent's token
total. Finite child budgets reserve the member's remaining budget across
simultaneously active children and the parent's subsequent model requests.
A child with an omitted budget receives half the unreserved remainder so the
parent can coordinate its runs. Unbounded parents can launch unbounded
children. An explicit child `budget` limits that child. The parent's step limit
supplies the default for child sessions. A Workflow may select a per-call step
limit with `ctx.agent_run(max_steps=...)`.

A launch lasts until its Workflow finishes or its parent stops. Ordinary
parent completion stops any remaining children before saving the final parent
snapshot. Active child control files live in
`.opencollab/child-runs/<id>` inside the environment. When the parent run has an
artifacts directory, it receives each child's native Workflow manifest,
Session transcripts, result, and command output. The finished child's
`artifacts` field names that persistent host directory. Parent shutdown removes
child control files before the Team captures the member's patch.
Source errors, execution errors, and output serialization errors appear in the
result. Workflow agent failures retain the existing `agent_failures` field,
including cases where the Workflow chooses to return after a failed agent.

Cancellation first asks the child Workflow runtime to stop its sessions and
save its state. If it fails to settle, the owning Environment cancels its
command and performs its existing process cleanup. A `wait` call uses the
managed lifecycle, so an ordinary `bash` timeout does not determine how long
the Workflow can run.

An authored Workflow inherits its member's isolation strategy. Each
`ctx.agent` or `ctx.agent_run` call can select `shared`, `worktree`,
`docker-group`, `docker-session`, or a host-registered strategy through its
`isolation` argument. See the [session isolation guide](../../docs/session-isolation.md)
for provider configuration. For example, these calls create two independent
Docker execution environments when the host configures the `docker-session`
provider.

```python
from opencollab.tools import builtin_tools

async def main(ctx, args):
    tools = builtin_tools("bash")
    first = await ctx.agent("inspect the source", tools=tools, isolation="docker-session")
    second = await ctx.agent("verify the source", tools=tools, isolation="docker-session")
    return first, second
```

The host owns the configured provider factories and each authored run's child
environments. The member's Python process sends environment operations and
model requests through its private run directory. The host calls the member's
resolved model backend and returns its native response and usage. This also
supports a member container with Docker's default `none` network. Parent
cancellation waits for active child operations and releases their environments.
