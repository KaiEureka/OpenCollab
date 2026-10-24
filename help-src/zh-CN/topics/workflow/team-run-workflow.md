---
{
  "id": "team-run-workflow",
  "title": "让 Team 成员调用已注册 Workflow",
  "category": "workflow",
  "summary": "给指定角色授予 run_workflow，让成员在自己的执行环境中运行注册工作流并获得结果。",
  "aliases": [
    "队友跑工作流",
    "Team → Workflow",
    "工作流工具",
    "调用 Duo",
    "调用 Weave",
    "PR237",
    "嵌套工作流"
  ],
  "keywords": [
    "run_workflow",
    "Team 调用 Workflow",
    "子 Workflow",
    "注册工作流"
  ],
  "questions": [
    "Team 成员怎样调用 Duo？",
    "run_workflow 会等到运行结束吗？",
    "子 Workflow 花费算到谁的预算？"
  ],
  "related": [
    "run-registered-workflow",
    "member-authored-workflow",
    "compose-workflow-candidates",
    "wait-for-team-results"
  ],
  "verified_ref": "e7946a42"
}
---

# 让 Team 成员调用已注册 Workflow

`run_workflow` 让 Team 成员在处理任务时调用一个已经注册的 Workflow。成员提交名称和输入后，当前工具调用等待整个工作流结束，再一次返回结果。它适合把稳定的协作流程交给某个角色，例如让修复负责人按需要启动 Duo，再阅读交付结果。

## 给角色配置工具

把工具写进需要调用它的角色。下面的 YAML 可作为 Team 配置的一部分，保存为 `team.yaml` 后通过 Team 运行入口使用。

```yaml
entry: coordinator
roles:
  coordinator:
    prompt: 按任务需要调用注册工作流，并核对返回的交付结果。
    tools: [file_read, bash, run_workflow]
```

运行时根据已发现的工作流生成 `name` 的可选值和说明。注册来源与普通 Workflow 入口一致，包含内置工作流和工作区目录。运行前应先用 `opencollab workflow list --workspace ...` 确认名称。成员新写出的 Python 文件可使用另一项工具 `manage_workflow` 启动，具体生命周期见相关章节。

成员使用下面的 JSON 参数调用工具。

```json
{
  "name": "duo",
  "args": {"goal": "修复配置读取错误并验证"},
  "budget": 50000,
  "concurrency": 2
}
```

## 参数与环境

| 字段 | 输入要求 | 作用 |
| --- | --- | --- |
| `name` | 必填，注册名称 | 指定执行函数 |
| `args` | 必填，JSON 对象 | 原样作为工作流输入 |
| `budget` | 可选，正整数 | 在成员剩余额度内设置本次上限 |
| `concurrency` | 可选，正整数，默认 4 | 限制工作流内部活跃 Agent 会话 |

工具执行使用调用成员当前的执行环境、工作目录、有效模型配置和权限策略。它借用成员环境，由 Team 运行继续管理环境的创建和释放。工作流内部会话的 token 消耗随 usage 事件记入该成员，后续成员请求看到相应的剩余额度。省略 `budget` 时使用成员剩余额度，显式值会与剩余额度取较小值。成员额度已经耗尽时，工具直接返回额度耗尽错误。

工作流作者为每次 `ctx.agent` 选择工具，成员的权限策略继续参与执行检查。工作流也继承父成员的步骤上限。运行选项的可配置范围以此工具的四个字段为准，SDK 的其他参数应在宿主调用或工作流源代码中设置。

## 等待和读取返回值

`run_workflow` 的工具执行贯穿整个子工作流运行。成员在它返回之后再处理结果。需要启动后继续协调、随后查询进度的任务，可采用 `manage_workflow` 的子运行方式。

返回内容是 JSON 文本，`output` 保存入口返回值，`tokens` 是本次工作流消耗，`stop_reason` 表示额度等停止原因，`agent_failures` 保留内部会话失败，`artifacts` 指向保存目录。工具成功返回时仍应读取工作流自己的交付字段，例如 Duo 的 `adopted` 或 Weave 的 `delivery_ok`，并检查实际文件或应用验证结果。

Team 有保存目录时，子工作流记录位于该目录下独立的 `workflow-agent-...` 子目录。会话、工作流清单和可选轨迹可帮助定位某个成员调用的具体角色。模型输出可以帮助理解决策过程，工具回执和交付检查记录实际执行结果。

## 继续组合与取消

被调用的工作流可把同一个 `ctx` 传给异步子函数，顺序或并行组织更多会话。需要独立候选环境时可使用 `ctx.candidate_workflow`。这些调用继续处于当前工作流的预算和环境管理之下。

Team 的成员创建操作仍属于同一个 Team。工作流上下文公开的组合方法集中在 Agent 会话、集合和候选操作，独立 Team 的 SDK 入口为 `OpenCollab.team`。宿主另行启动 Team 时，需要管理该运行的额度、取消信号和产物。

用户停止父成员时，取消信号会中断正在等待的工作流，工具等待已有 Workflow 清理后返回中断提示。定位停止问题时应一并查看父成员记录与子工作流产物，确认停止原因、已经写入的文件以及清理结果。

## 源码依据

[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md#L263)、[opencollab/bootstrap/team_workflow.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/team_workflow.py#L25)、[tests/interfaces/test_team_workflow.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/tests/interfaces/test_team_workflow.py#L24)。

## 相关章节

[Workflow 完整指南与运行入口](run-registered-workflow.md)、[让成员编写并管理自己的子 Workflow](member-authored-workflow.md)、[组合子 Workflow 并采用候选结果](compose-workflow-candidates.md)、[等待队友结果并读懂团队状态](../team/wait-for-team-results.md)。
