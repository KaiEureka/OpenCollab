# Session isolation

[中文](session-isolation.zh-CN.md)

Team and Workflow calls can select an execution environment strategy. Session
messages remain private to each agent. The strategy controls the workspace and
tool execution environment.

`shared` reuses the parent environment and directory. `worktree` creates an
independent checkout inside the parent execution environment. Worktrees share
the operating system and Git object storage. A container-backed parent keeps
its children inside that container. Existing non-Git copy behavior is preserved.

## Choosing a strategy

```python
from opencollab import OpenCollab

client = OpenCollab(".")

async def solve(ctx, inputs):
    answer = await ctx.agent(inputs["task"], tools="coding")
    summary = await ctx.agent("Summarize the task", tools=[], isolation="shared")
    return {"answer": answer, "summary": summary}

result = await client.workflow(solve, {"task": "Inspect the code"}, isolation="worktree")
```

Workflow agents inherit the run strategy when `isolation` is omitted.
`isolation=True` selects `worktree`; `False` selects `shared`. Omitting the run
strategy preserves the historical shared Workflow default, including existing
worktree requirements for a clean source checkout. `ctx.agent_run` and candidate
child workflows use the same selection and cleanup path.

A Team file can select a default and role overrides.

```yaml
entry: manager
isolation: worktree
roles:
  manager:
    prompt: Coordinate the task.
    isolation: shared
    tools: [spawn_agent]
  coder:
    prompt: Implement the requested change.
    tools: [file_read, file_write]
topology:
  manager: [coder]
```

`client.team(..., isolation="shared")` overrides the Team-file default. Role
settings take precedence over the run default. With every new setting omitted,
`use_worktrees` keeps its existing behavior and the entry agent retains its
original workspace. The CLI reads these same Team-file settings.

An explicitly isolated entry agent keeps its full patch in the run artifacts
before the workspace is reclaimed. The SDK returns its path in
`result.metrics["entry_patch"]`. Trace-free runs also save this patch; without
an artifact directory, OC creates a recovery directory. Applying the patch to
the caller's source remains an explicit operation.

The Workflow CLI accepts `--isolation shared` or `--isolation worktree`.

## Custom environment providers

Pass `environment_providers={"custom": factory}` to `OpenCollab`, then select
`isolation="custom"`. Each factory receives `workspace`, `base_environment`,
`owned_save_dir`, `enable_adoption`, and `owned_output_paths` as keyword arguments.
It creates a fresh pool for that run and strategy, implementing the public
`opencollab.environments.EnvironmentPool` protocol. The existing operations are
`acquire(role, source=...)`, `release_env(environment)`, `release()`, and
`adopt(patch)`.

The source passed to `acquire` is the actual parent or review workspace. An
environment returned by the provider is ready for tool execution. The provider
owns allocations it creates; the supplied source remains borrowed. Cleanup
waits for pending acquisitions and preserves recovery material when it fails.
OC retries retained cleanup through the same pool. Tools continue to use their
existing permission and workspace checks.

Unknown strategies report an error instead of selecting a different execution
environment. Model endpoints and request concurrency are configured separately
from workspace isolation.
