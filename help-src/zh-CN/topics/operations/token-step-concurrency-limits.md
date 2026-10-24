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
    "serialize_turns"
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

动态团队从同一个池中分配额度。创建子成员时同步保留至多 `min(total, max(10_000, total // 4))`，同时受尚未分配余额限制。入口 Agent 的当前轮次可以租用剩余池，成员的未结算请求继续拥有相应保留量。

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

Duo 按 A 再 B 的顺序运行候选，提高并发限制后仍保留这一次序。工作流调用在执行前从工作流池保留预算，实际授权可能因为其它调用占有份额而低于请求值。清理完成后返还未使用的保留量。预算拒绝表示无法取得执行额度，等待并发槽表示暂时没有运行席位，应分开判断。

## 按进展扩展软额度

`RunControl` 支持在硬授权内先给较小软额度，再由 `decide_budget` 返回 `BudgetDecision` 保持或增加额度。回调可读取实际消耗、步数、输入保留量和最低输出需求，并结合应用中的执行证据决定是否继续。增加值必须覆盖实际消耗且不超过硬授权，减少现有软额度会导致策略错误。硬额度耗尽后进入普通额度停止。下面的片段放在已有 `async` 函数中，沿用 `client`，按消耗阈值扩展一次软额度。应用可把触发条件接到实际执行检查的进展信号。

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

## 来源

[docs/runtime-design.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/runtime-design.md#L113)　[configs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md#L286)　[docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md#L18)　[opencollab/sdk/client.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/client.py#L408)

## 相关指南

[上下文窗口与自动压缩](context-compaction.md)　[超时、重试与请求卡住诊断](timeouts-retries.md)　[逻辑模型路由与共享请求容量](../models/model-routing-request-capacity.md)　[中断任务与检查资源清理](cancellation-cleanup.md)　[统计 token、缓存命中和费用](../observability/token-cache-cost.md)
