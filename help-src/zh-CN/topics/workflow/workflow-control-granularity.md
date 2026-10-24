---
{
  "id": "workflow-control-granularity",
  "title": "选择 Workflow 的入口与控制粒度",
  "category": "workflow",
  "summary": "按需要选择 CLI、SDK、ctx 单次调用与回调参数，理解每层可控制的具体行为。",
  "aliases": [
    "有哪些开发档位",
    "高级和低级怎么选",
    "什么参数能控制工作流",
    "什么时候需要 Python SDK",
    "能控制每个 Agent 吗",
    "能接管每次模型请求吗"
  ],
  "keywords": [
    "Workflow 控制粒度",
    "CLI",
    "SDK",
    "RunControl"
  ],
  "questions": [
    "按需要选择 CLI、SDK、ctx 单次调用与回调参数，理解每层可控制的具体行为？"
  ],
  "related": [
    "workflow-programming-model",
    "run-registered-workflow",
    "workflow-agent-calls",
    "workflow-public-api-map"
  ],
  "verified_ref": "e7946a42"
}
---

# 选择 Workflow 的入口与控制粒度

调整 Workflow 时，可以从现成流程的输入参数开始，逐步进入 SDK 运行设置、自定义 Python 编排、单次 Agent 调用及运行回调。各入口提供不同范围的参数。决定把一个要求放在哪里时，先看它影响整次运行、某个角色，还是一次具体工具或模型请求。

## 使用现成流程输入

CLI 通过名称选择 Workflow，通过 `--args` 传入该流程理解的 JSON 字段。Duo 的 `submission_mode` 决定候选交付方式，Weave 的 `groups` 与 `check_commands` 描述分组任务和检查，Reconstruct 的 `max_iterations` 控制探索迭代。

模型、预算、并发和环境策略属于运行选项，放在 `workflow run NAME` 后面。`--budget` 控制整次共享额度，`--concurrency` 控制 Agent 会话容量，`--task-concurrency` 控制集合单元容量，`--isolation` 选择默认角色环境。流程输入应按所选内置流程的字段使用。

## 使用 SDK 控制完整运行

```python
from opencollab import OpenCollab

async def limited_flow(ctx, args):
    return await ctx.agent(args['goal'], tools=[], label='answer', budget=6000)

async def launch(workspace):
    return await OpenCollab(workspace).workflow(
        limited_flow,
        {'goal': '解释给定问题'},
        budget=20000,
        concurrency=2,
        task_concurrency=2,
        timeout=180,
        max_steps=20,
        cleanup_timeout=3,
        isolation='shared',
        limit_mode='explicit',
    )
```

SDK 还可设置 `artifacts`、`trace`、`run_id`、`agent_profile`、`system_prompt`、`candidate_workspace` 与 `llm`。自定义函数体则决定分支、循环、动态任务创建和结果处理，例如根据结构化审阅结果进入修复轮，或在时间不足时返回已有产物。

## 控制单次角色

`ctx.agent` 可以给当前角色选择工具、额度、调用时限、结构化 schema 和环境。`ctx.agent_run` 进一步暴露本次 `max_steps`、`system_prompt`、`run_control` 与 `cleanup_timeout`，返回可检查的执行快照。

预算申请会受共享池及其他预留影响，实际硬授权可能比申请小。角色硬额度限制总体用量，软额度用于会话继续或结束的决策。`agent_run` 的显式系统提示会替换本次默认系统提示，Agent profile 仍保留工具配置、上下文处理及安全行为。

## 在授权内参与决策

下面的完整工作流使用回调扩展软额度，并同步记录 usage 事件。它可以直接传给 SDK，外层预算应覆盖本次角色的实际申请。

```python
from opencollab import BudgetDecision, RunControl

async def controlled_answer(ctx, args):
    usage = []

    def on_event(event):
        if event.type == 'usage':
            usage.append(event.data['total_tokens'])

    def decide(snapshot):
        current = snapshot.soft_budget_tokens or max(1, snapshot.used_tokens)
        proposed = current + 2000
        if snapshot.hard_budget_tokens is not None:
            proposed = min(proposed, snapshot.hard_budget_tokens)
        return BudgetDecision(soft_budget_tokens=proposed)

    run = await ctx.agent_run(
        args['goal'], tools=[], label='controlled', budget=8000,
        run_control=RunControl(
            initial_soft_budget_tokens=4000,
            decide_budget=decide,
            on_event=on_event,
        ),
    )
    return {'answer': run.output, 'status': run.status, 'usage_events': usage}
```

`decide_budget` 可以是同步函数或异步函数，收到 `BudgetSnapshot` 后返回 `BudgetDecision`。软额度应保持有效且不递减，并受实际硬授权限制。`on_event` 在记录用量时同步执行，适合收集简短事件，耗时工作由应用另行处理。观察回调异常会进入 `observation_errors`，预算策略错误则影响会话执行。

## 对照时限和资源层级

| 设置位置 | 主要参数 | 影响范围 |
| --- | --- | --- |
| SDK Workflow | `budget`、`timeout`、两种 concurrency | 整次运行及共享资源 |
| 角色调用 | `budget`、`timeout`、`max_steps` | 一次新会话 |
| RunControl | 软额度、预算回调、事件回调 | 授权内的角色决策和观察 |
| 模型配置 | 输出上限、请求时限、连接与流等待设置 | 模型请求 |
| 清理设置 | `cleanup_timeout` 等 | 已停止任务的结束与持久化 |

运行剩余时间可用 `ctx.seconds_left()` 读取，`ctx.time_low()` 表示是否进入预留范围。某次模型请求超时、整次运行到期和调用清理超时分别记录在相应层级。调试时，先从具体停止原因和清理状态确定限制发生在哪一层，再调整对应参数。

## 源码依据

[opencollab/adapters/cli/workflow.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/cli/workflow.py#L76)、[opencollab/sdk/client.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/client.py#L408)、[opencollab/workflows.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/workflows.py#L21)、[docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md#L18)。

## 相关章节

[理解 Workflow 的编程模型与自由度](workflow-programming-model.md)、[Workflow 完整指南与运行入口](run-registered-workflow.md)、[在 Workflow 中调用 Agent 并处理结果](workflow-agent-calls.md)、[按开发任务查找 Workflow 公开 API](workflow-public-api-map.md)。
