---
{
  "id": "token-step-concurrency-limits",
  "title": "设置 token 预算、步数与运行并发",
  "category": "operations",
  "summary": "为单 agent、团队和工作流设置合适的执行额度，并解释预算拒绝、等待与停止。",
  "aliases": [
    "token 上限",
    "最多跑几步",
    "并发数",
    "分角色额度",
    "共享预算",
    "预算不够",
    "RunControl",
    "initial_soft_budget_tokens",
    "BudgetDecision",
    "OPENCOLLAB_UNBOUNDED_LIMITS",
    "serialize_turns",
    "final_prompt",
    "OPENCOLLAB_BUDGET_NUDGE_MODE",
    "OPENCOLLAB_WRITE_NUDGE_MODE",
    "一直读不改",
    "预算提示频率"
  ],
  "keywords": [
    "--budget",
    "OPENCOLLAB_BUDGET",
    "budget",
    "max_steps",
    "steps",
    "concurrency",
    "task_concurrency",
    "--concurrency",
    "--task-concurrency",
    "limit_mode"
  ],
  "questions": [
    "为单 agent、团队和工作流设置合适的执行额度，并解释预算拒绝、等待与停止？"
  ],
  "related": [
    "context-compaction",
    "timeouts-retries",
    "model-routing-request-capacity",
    "cancellation-cleanup",
    "token-cache-cost"
  ],
  "verified_ref": "e7946a42"
}
---

# 设置 token 预算、步数与运行并发

`budget` 控制累计输入与输出 token，`max_steps` 控制会话执行步数，`concurrency` 控制工作流同时运行的 Agent 会话数。先按任务规模设置有限额度，再根据实际用量和完成证据调整。token 预算与美元花费分别记录，单次输出上限也有独立配置。

## 给单 Agent 明确额度

下面是一个可直接运行的脚本。只读任务使用较小步数，模型设置沿用本地配置。

```python
import asyncio
from opencollab import OpenCollab

async def main() -> None:
    result = await OpenCollab(".").agent2(
        "阅读测试配置，说明应执行哪些验证以及依据。",
        tools="read",
        budget=80_000,
        max_steps=30,
        timeout=300,
    )
    print(result.status, result.reason, result.tokens)
    print(result.metrics.get("steps"))

asyncio.run(main())
```

`budget` 和 `max_steps` 接受正整数。旧参数 `steps` 与 `max_steps` 互斥。客户端的 `max_output_tokens` 默认 8,192，限制每次模型调用的输出，较大的整次预算会支持更多次调用。每次重复发送历史都会增加累计输入用量。

## 团队怎样分配 token

动态团队从同一个池中分配额度。创建子成员时同步保留至多 `min(total, max(10_000, total // 4))`，同时受尚未分配余额限制。入口 Agent 的当前轮次可以使用剩余池，成员尚未结算的请求继续占用相应保留量。

预建团队共享池时，每个已声明席位还有累计上限，当前按总池除以席位数分配。也可以在团队文件顶层或角色的 `budget` 对象中设置 `tokens`。一旦某个角色设置独立额度，全部角色都必须有额度，团队总额为各角色之和。此模式需要 `prebuild_team=True`，由文件提供额度，同时传 SDK `budget=` 会报错。

两种团队用法的示例放在已有 `async` 函数中，`client` 是已经构造的客户端。选择其中一种调用。

```python
result = await client.team(
    "完成指定实现并验证。",
    config="team.yaml",
    budget=300_000,
    max_steps=100,
)
```

```python
result = await client.team(
    "完成指定实现并验证。",
    config="team-with-role-budgets.yaml",
    prebuild_team=True,
    max_steps=100,
)
```

## 工作流有两类并发

`concurrency` 限制 Agent 会话，`task_concurrency` 独立限制活跃的 parallel 或 pipeline 单元，包括候选子工作流，省略时继承前者。混合 Agent 与任务工作时，峰值可以达到两个限制之和。共享后端请求数还可由模型请求池单独限制。下面的 Duo 命令显式允许宿主工作区执行 shell，按本次任务需要选择执行环境。

```bash
opencollab workflow run duo --workspace /path/to/application \
  --task '完成指定修改并运行验证。' \
  --args '{"allow_unisolated_shell":true}' \
  --budget 300000 --concurrency 2 --task-concurrency 2
```

Duo 按 A 再 B 的顺序运行候选，提高并发限制后仍保留这一次序。工作流调用在执行前从工作流池保留预算，实际授权可能因为其它调用占有份额而低于请求值。清理完成后返还未使用的保留量。预算拒绝表示无法取得执行额度，等待并发槽表示暂时没有可用席位。根据结果状态区分预算不足与并发等待。

## 按进展扩展软额度

`RunControl` 支持在硬授权内先给较小软额度，再由 `decide_budget` 返回 `BudgetDecision` 保持或增加额度。回调可读取实际消耗、步数、输入保留量和最低输出需求，并结合应用中的执行证据决定是否继续。增加值必须覆盖实际消耗且不超过硬授权，减少现有软额度会导致策略错误。硬额度耗尽后按预算限制停止。下面的片段放在已有 `async` 函数中，沿用 `client`，按消耗阈值扩展一次软额度。应用可根据实际执行和验证进展设置触发条件。

```python
from opencollab import BudgetDecision, RunControl

def decide(snapshot):
    allowance = snapshot.soft_budget_tokens
    if snapshot.used_tokens >= 25_000:
        allowance = snapshot.hard_budget_tokens
    return BudgetDecision(allowance)

result = await client.agent2(
    "阅读项目并解释可执行的验证步骤。",
    tools="read",
    budget=100_000,
    run_control=RunControl(
        initial_soft_budget_tokens=50_000,
        decide_budget=decide,
    ),
)
```

`OPENCOLLAB_UNBOUNDED_LIMITS=true` 对入口有不同影响。Base 和 Single2 的显式预算与步数继续生效，省略的限制变为无限制。`workflow()` 默认 `limit_mode="environment"` 会采用无限 token 和角色步数，即使传入了数值。需要保留明确限制时用 `limit_mode="explicit"`。`team()` 继续使用共享或声明额度及其 `max_steps`。时间和清理限制继续生效。

额度检查发生在调用边界，已经进行中的请求可能超过剩余额度。停止后查看 `reason`、部分产物和最终用量，再判断是否继续任务。

## 在额度内安排最后一次交接

预算回调也可以通过 `BudgetDecision(..., final_prompt=...)` 给当前模型请求补充交接要求。该提示会重新计入输入预留量，因此应在尚有输出空间时发出。下面的回调沿用上例的 `BudgetDecision` 导入，并可替换 `RunControl` 中的 `decide_budget`。它保留软额度，接近额度时请 Agent 交付已有证据。

```python
def finish_when_low(snapshot):
    allowance = snapshot.soft_budget_tokens
    reserve = snapshot.reserved_input_tokens + snapshot.minimum_output_tokens
    final_prompt = None
    if allowance - snapshot.used_tokens < reserve + 5_000:
        final_prompt = "完成当前验证并交付已有改动、实际测试结果和待处理事项。"
    return BudgetDecision(allowance, final_prompt=final_prompt)
```

这段回调用于已经设置有限软额度的调用。回调可以同步返回，也可以异步等待应用中的进展判断。运行时会在执行检查和请求输入估算时调用回调。单次响应超过软额度后，运行时也可能以 `after_response` 为原因再次调用回调。硬额度耗尽时按预算限制停止。最终提示会加入当前请求，任务完成程度从保存的文件与验证结果判断。

## 调整预算状态与行动提示

运行时默认每步向模型提供预算状态。进程变量 `OPENCOLLAB_BUDGET_NUDGE_MODE` 可以设为 `every-step`、`thresholds` 或 `off`。`thresholds` 在累计消耗跨过有限预算的 20%、40%、60% 和 80% 时提示，恢复会话后从已有消耗继续比较。阈值提示用于有限预算，`every-step` 还会在无限预算运行中显示累计用量。

`OPENCOLLAB_WRITE_NUDGE_MODE` 默认 `on`。Agent 具备写入工具且持续读取时，运行时会在 8 次读取后建议修改，在 16 次后要求一次工具调用。具备结构化提交工具且适合提交时，8 次读取后可以要求结构化提交。设为 `off` 可用于观察模型自行选择阅读、修改和提交时机的任务。预算状态与行动提示分别设置，实际 token 和步数限制继续按运行参数执行。

以下命令在启动进程前选择按阈值提示预算，保留默认行动提示。模型配置继续沿用该工作区的设置。

```bash
OPENCOLLAB_BUDGET_NUDGE_MODE=thresholds \
  opencollab --workspace /path/to/application --budget 200000
```

提示模式由进程环境读取，未知值分别回到 `every-step` 和 `on`。实现与阈值见[运行提示策略](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/steering.py)，预算回调和最终提示见[运行控制](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/run_control.py)。

## 来源

[docs/runtime-design.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/runtime-design.md#L113)　[configs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md#L286)　[docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md#L18)　[opencollab/sdk/client.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/client.py#L408)

## 相关指南

[上下文窗口与自动压缩](context-compaction.md)　[超时、重试与请求卡住诊断](timeouts-retries.md)　[逻辑模型路由与共享请求容量](../models/model-routing-request-capacity.md)　[中断任务与检查资源清理](cancellation-cleanup.md)　[统计 token、缓存命中和费用](../observability/token-cache-cost.md)
