---
{
  "id": "workflow-composition-hierarchy",
  "title": "理解 Agent、Team 与 Workflow 的调用层级",
  "category": "workflow",
  "summary": "根据所需的成员自治或程序控制选择调用层级，并为组合明确预算、环境和结果所有者。",
  "aliases": [
    "Workflow 里面可以再跑 Workflow 吗",
    "Workflow 可以调用 Team 吗",
    "Agent 能不能开团队",
    "Team 可以跑工作流吗",
    "子工作流能继续开 Agent 吗"
  ],
  "keywords": [
    "Agent Team Workflow",
    "调用层级",
    "Workflow 组合",
    "run_workflow"
  ],
  "questions": [
    "根据所需的成员自治或程序控制选择调用层级，并为组合明确预算、环境和结果所有者？"
  ],
  "related": [
    "choose-working-mode",
    "team-run-workflow",
    "member-authored-workflow",
    "compose-workflow-candidates",
    "workflow-failure-recovery-lifecycle"
  ],
  "verified_ref": "e7946a42"
}
---

# 理解 Agent、Team 与 Workflow 的调用层级

同一个 Python 应用可以通过 SDK 启动 Agent、Team 和 Workflow。三种入口都返回 `RunResult`，各自的控制方式有所区别。Agent 执行单次会话，Team 让成员按角色和通信关系协作，Workflow 由异步 Python 函数安排步骤。组合时先确定谁创建运行、谁支付额度、谁负责停止和交付。

## 在宿主使用三个入口

```python
from opencollab import OpenCollab

async def summarize(ctx, args):
    return await ctx.agent(args['goal'], label='summary', tools=[])

async def run_independent_examples(workspace, team_config):
    client = OpenCollab(workspace)
    single = await client.agent('概括工作区任务', tools=[])
    flow = await client.workflow(
        summarize, {'goal': '解释任务中需要验证的行为'}, budget=10000,
    )
    team = await client.team(
        '检查任务需求并提交协作结果', config=team_config, budget=30000,
    )
    return {'agent': single.status, 'workflow': flow.status, 'team': team.status}
```

这段函数展示独立运行的显式调用，`team_config` 应指向实际 Team 配置。各次运行分别返回状态、输出和产物。它们使用同一个 client 配置，预算仍按各次入口传入的值处理，工作区的先前改动会按环境策略影响后续运行。

## 在 Workflow 内组合步骤

`ctx.agent` 与 `ctx.agent_run` 建立属于当前 Workflow 的会话，共享该运行的额度和容量，并按当前环境策略执行工具。使用 `await helper(ctx, args)` 调用普通异步子函数时，资源归属也继续保持在当前 ctx。

如果整套子流程需要候选环境，可用 `ctx.candidate_workflow(fn, args, label=...)`。其子会话受父运行的会话容量、集合容量和绝对期限管理，返回 `CandidateRun`，供父流程检查后采用。当前候选子上下文未配备新候选后端，继续组合更深的普通函数适合复用逻辑。

## Team 成员怎样进入 Workflow

| 入口 | 适合的来源 | 返回时机 | 资源归属 |
| --- | --- | --- | --- |
| `run_workflow` | 启动时已经注册的名称 | 完整工作流结束后返回 | 借用调用成员的环境并计入其额度 |
| `manage_workflow launch` | 成员写出的 Python 文件 | 启动后立即返回 child id | 创建成员拥有子运行并管理生命期 |
| 普通异步子函数 | 源代码中已导入的函数 | 子函数返回时继续 | 沿用传入的 ctx |
| `candidate_workflow` | 源代码中的子流程 | 候选捕获与清理后返回 | 候选环境、父资源容量和授权 |

两类 Team 工具都应在角色配置中显式声明。`run_workflow` 适合成员调用已知流程并等待结果。`manage_workflow` 适合成员先编写代码、启动，再用 `status`、`wait`、`result` 和 `stop` 管理该 id。后者需要成员具备 bash 权限，并在目标环境拥有安装了 OpenCollab 的解释器。

`manage_workflow` 的有限子预算会预留父成员余额，省略时取得未预留余额的一半。父成员完成或停止时，会停止仍活跃的子运行。将某个 child id 发给其他成员供阅读，仍需由原创建成员执行对应管理操作。

## 在 Python 中启动独立 Team

公开 `ctx` 提供 Agent、集合与候选操作，当前没有 `ctx.team` 方法。需要在一套业务流程中运行 Team 时，可以从宿主代码显式调用 `OpenCollab.team`。如果把独立 SDK 调用放进自定义 Workflow 的函数体，它仍是一项独立运行，需要作者连接外层的额度和生命期。

例如外层 Workflow 有 50,000 token 预算，内层独立 `oc.team(..., budget=30000)` 的调用应由应用确认是否属于同一个业务总额。需要统一停止时，应用应拥有内层任务并传播取消，等待其清理后再释放环境。产物目录、模型客户端与调用方环境也应由明确的拥有者负责。

因此，复用小逻辑时把 ctx 传给普通函数，比较候选时采用候选接口，成员执行运行时源文件时采用管理工具，独立组织 Team 时在宿主显式管理运行。每个选择都对应一条实际的调用路径和清理责任。

## 源码依据

[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md#L121)、[opencollab/workflows.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/workflows.py#L21)、[examples/member-authored-workflow/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/examples/member-authored-workflow/README.md#L42)。

[opencollab/sdk/client.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/client.py)。

## 相关章节

[按任务选择默认 CLI、Base、Team 或 Workflow](../orientation/choose-working-mode.md)、[让 Team 成员调用已注册 Workflow](team-run-workflow.md)、[让成员编写并管理自己的子 Workflow](member-authored-workflow.md)、[组合子 Workflow 并采用候选结果](compose-workflow-candidates.md)、[处理 Workflow 的失败、重试与恢复](workflow-failure-recovery-lifecycle.md)。
