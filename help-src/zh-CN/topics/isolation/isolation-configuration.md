---
{
  "id": "isolation-configuration",
  "title": "默认隔离与单次覆盖怎么配",
  "category": "isolation",
  "summary": "让 Team 角色和 Workflow 调用使用预期的执行环境。",
  "aliases": [
    "角色覆盖",
    "单次覆盖",
    "use_worktrees",
    "--no-worktrees",
    "--isolation",
    "ctx.agent",
    "ctx.agent_run",
    "OpenCollab.team",
    "OpenCollab.workflow"
  ],
  "keywords": [
    "隔离配置",
    "默认策略",
    "isolation"
  ],
  "questions": [
    "让 Team 角色和 Workflow 调用使用预期的执行环境？"
  ],
  "related": [
    "isolation-overview",
    "worktree-workspaces",
    "candidate-delivery"
  ],
  "verified_ref": "e7946a42"
}
---

# 默认隔离与单次覆盖怎么配

Workflow 可以设置一次运行的隔离默认值，再由单次 Agent 调用覆盖。Team 可以在团队文件里设置默认值，由 SDK 的运行参数覆盖，再由角色配置决定该角色最终使用的策略。先明确自己正在设置运行、角色还是单次调用，就能判断任务会落到哪个环境。

## Workflow 的运行默认值与单次调用

```python
import asyncio
from opencollab import OpenCollab
from opencollab.tools import builtin_tools

async def inspect(ctx, inputs):
    code = await ctx.agent("阅读项目入口", tools=builtin_tools("file_read", "grep"))
    note = await ctx.agent(
        f"根据分析写一段执行摘要\n{code}", tools=[], isolation="shared"
    )
    return {"code": code, "note": note}

async def main():
    result = await OpenCollab(".").workflow(
        inspect, {}, isolation="worktree"
    )
    print(result.status, result.output)

asyncio.run(main())
```

第一次 `ctx.agent` 继承运行的 `worktree`。第二次显式选 `shared`，复用父环境，并在提示词中接收第一次输出。`ctx.agent_run` 也使用这套选择过程，并额外返回执行与清理状态。布尔值 `True` 对应 `worktree`，`False` 对应 `shared`。全部省略时，Workflow 延续共享环境的历史行为。

## Team 的优先关系

```yaml
entry: manager
isolation: worktree
roles:
  manager:
    prompt: Coordinate the task and summarize the results.
    isolation: shared
    tools: [spawn_agent, file_read]
  coder:
    prompt: Implement the requested change.
    tools: [file_read, apply_patch]
topology:
  manager: [coder]
```

运行 `await client.team(task, config="team.yaml", isolation="docker-session")` 时，`coder` 使用运行指定的 `docker-session`，`manager` 继续使用角色声明的 `shared`。角色值优先于 SDK 运行默认值，SDK 值优先于 Team 文件默认值。这也说明入口角色可以单独选择环境。显式隔离入口后，其文件变化通过完整入口补丁返回，查看 [把候选结果采纳到交付目录](candidate-delivery.md)。

新隔离选项都省略时，Team 继续按 `use_worktrees` 处理子成员，默认值为 `True`，入口 Agent 使用原工作目录。即使仅子角色声明隔离，入口也保留这项历史行为。`--no-worktrees` 对应旧式子成员目录选择，团队文件中的显式策略应按新优先关系理解。

## 设置后怎样确认落点

可以先派发一个范围很小的读取任务，要求它报告实际工作目录与目标文件路径。容器策略再执行一条镜像内已有命令，工作树策略则检查新目录中的文件内容。随后运行一次所需的项目测试，确认依赖和相对路径在这个环境中可用。

运行默认值只负责提供省略参数时的选择。若某个角色已经写了自己的隔离策略，调整运行默认值后仍会继续使用角色设置。因此，排查时应同时阅读调用代码和团队文件，并把任务实际报告的路径与两处配置对应起来。

## CLI 与错误定位

Workflow CLI 的 `workflow run NAME` 后可以写 `--isolation shared` 或 `--isolation worktree`。Team 根命令读取 `--team-config team.yaml` 中的隔离配置。需要 DockerOptions 或自定义环境提供器时，使用 SDK 组合，具体见 [在容器中运行任务](docker-isolation.md)。

策略名允许字母、数字、连字符和下划线，首尾空格会报错。一个名字格式正确，却尚未注册对应提供器时，会提示该策略需要 environment provider。环境申请成功后仍可能在工作区准备阶段失败，例如传统工作树源目录存在未提交改动。这时应检查工作区内容与所走的创建方式，处理方法见 [多个任务怎样在独立工作区改代码](worktree-workspaces.md)。应在实际调用中核对工作目录和产生的文件，再判断配置是否符合预期。

这些规则分别来自[入口策略解析](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/isolation.py)、[策略名解析](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/isolation.py)和[隔离指南](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/session-isolation.zh-CN.md)。
