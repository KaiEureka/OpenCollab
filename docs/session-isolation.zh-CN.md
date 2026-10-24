# Session 隔离

[English](session-isolation.md)

Team 和 Workflow 可以选择执行环境策略。各 Agent 的对话状态独立保存，隔离策略控制工作目录和工具执行环境。

`shared` 复用父任务的环境与目录。`worktree` 在父执行环境中创建独立工作树，工作树之间共享操作系统和 Git 对象存储。父任务使用容器时，子工作树继续位于该容器中。非 Git 目录沿用已有的副本处理方式。

## 选择策略

```python
from opencollab import OpenCollab

client = OpenCollab(".")

async def solve(ctx, inputs):
    answer = await ctx.agent(inputs["task"], tools="coding")
    summary = await ctx.agent("Summarize the task", tools=[], isolation="shared")
    return {"answer": answer, "summary": summary}

result = await client.workflow(solve, {"task": "Inspect the code"}, isolation="worktree")
```

省略单次调用的 `isolation` 时，Workflow Agent 继承运行默认策略。`True` 选择 `worktree`，`False` 选择 `shared`。运行默认策略也省略时，保留原有的共享环境行为，已有工作树对干净源目录的要求继续生效。`ctx.agent_run` 和候选子工作流采用相同的选择与清理过程。

Team 配置文件可以声明默认策略，并为角色覆盖。

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

`client.team(..., isolation="shared")` 覆盖 Team 文件的默认策略，角色设置优先于运行默认值。新选项全部省略时，`use_worktrees` 保留原语义，入口 Agent 继续使用原工作目录。CLI 读取相同的 Team 文件设置。

显式隔离的入口 Agent 在工作区回收前保存完整补丁。SDK 通过 `result.metrics["entry_patch"]` 返回补丁位置。关闭轨迹记录时也会保存补丁，调用方省略产物目录时则创建恢复目录。调用方可以显式将补丁应用到源仓库。

Workflow CLI 支持 `--isolation shared` 和 `--isolation worktree`。

## 自定义环境提供器

向 `OpenCollab` 传入 `environment_providers={"custom": factory}`，随后使用 `isolation="custom"`。每个工厂通过关键字参数接收 `workspace`、`base_environment`、`owned_save_dir`、`enable_adoption` 和 `owned_output_paths`。

工厂为每次运行中的该策略创建一个环境池，实现公开的 `opencollab.environments.EnvironmentPool` 接口。操作包括 `acquire(role, source=...)`、`release_env(environment)`、`release()` 和 `adopt(patch)`。

`acquire` 收到当前父任务或审阅任务的真实工作环境，返回已准备好执行工具的环境。提供器负责自己分配的资源，传入的源环境保持借用关系。清理等待在途的环境申请完成，并在失败时保留可恢复材料，由同一个池继续处理清理。工具沿用已有权限和工作区检查。

未知策略会返回错误。模型端点和请求并发采用独立配置。
