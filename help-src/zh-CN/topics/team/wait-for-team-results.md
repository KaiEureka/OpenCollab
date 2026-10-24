---
{
  "id": "wait-for-team-results",
  "title": "等待队友结果并读懂团队状态",
  "category": "team",
  "summary": "区分派发结果、消息回复和团队结束条件，采用适合当前交接方式的等待操作。",
  "aliases": [
    "怎么等子 agent",
    "队友没回复",
    "busy",
    "queued",
    "awaiting",
    "idle",
    "完成后唤醒",
    "spawn_agent",
    "message_agent"
  ],
  "keywords": [
    "等待结果",
    "team_status",
    "submit",
    "协作状态"
  ],
  "questions": [
    "区分派发结果、消息回复和团队结束条件，采用适合当前交接方式的等待操作？"
  ],
  "related": [
    "dispatch-team-agents",
    "message-team-agents",
    "team-run-workflow",
    "member-authored-workflow"
  ],
  "verified_ref": "e7946a42"
}
---

# 等待队友结果并读懂团队状态

等待队友有两种方式，分别对应派发工具结果和后续消息。`spawn_agent` 的完成结果会填回原工具调用。`message_agent` 立即返回入队回执，队友的答复在后续回合作为消息到达，并能重新唤起已经结束当前回合的发送方。

## 派发时由运行时连接结果

父成员调用 `spawn_agent` 后，运行时保存工具调用与子成员 aid 的关联，进入等待事件状态。同一批可以派发多个子任务，整批待处理结果填齐后，父成员从保存点继续模型调用。因此，一次派发通常已经包含等待过程。

完成结果可能同时带回答、工作树变化和错误原因。读取它时应确认对应 aid 与最终状态，再决定采纳或修订。子成员的文件改动需要按 [采用队友改动并验证交付](adopt-team-changes.md) 处理，文字中的完成说明应结合实际产物确认。

## 消息交接通过后续回合完成

发送消息后，发送方可以做与回复无关的工作。暂时没有其他动作时，结束当前回合就是交出执行机会。接收方的回复会进入发送方对话，重新打开它的回合。配置串行回合时，这个结束动作尤其关键，因为队友只能在当前成员退出回合后开始。

例如入口向已有 coder 发出 `{"to_aid": 1, "summary": "补做测试", "content": "请执行目标测试，并回信给出退出码和失败摘要。"}`，之后提交自己的当前进展。coder 完成并通过消息回复后，入口会再次得到执行机会，继续阅读结果和安排后续动作。

## 怎样读团队状态

`team_status` 使用空参数 `{}`。名册显示成员的当前状态，可用于确认目标正在执行还是等待。

| 显示状态 | 实际含义 | 可以继续观察的内容 |
| --- | --- | --- |
| `busy` | 该成员的执行任务仍在运行 | 当前工具、模型请求或执行记录 |
| `queued` | 成员在等待串行回合机会 | 当前占用回合的成员是否已经结束 |
| `awaiting` | 成员正在等延迟事件结果 | 它派发的子成员及对应结果 |
| `idle` | 成员已完成当前回合 | 是否会收到新消息并开始下一轮 |
| `stopped` 或 `error` | 成员停止或发生故障 | 停止原因、可用改动与失败记录 |

所有活动任务与待投递消息都处理完后，团队进入静止状态。入口先给出一段交接说明，随后队友回复重新唤起入口，属于正常协作过程。消息队列中仍有内容时，团队会继续处理这些消息。

## 等待超出预期时

先核对等待的是哪个 aid，查看它是否存在及对应角色。接着确认通信方向、入队回执与接收方是否有回信能力，再查看它是在执行、排队还是等待自己的子任务。最后结合终止原因与执行轨迹判断是否需要修订任务、增加必要输入或恢复保留工作区。

Team 成员调用 Workflow 时，Workflow 的内部会话由自己的运行时等待与清理，Team 工具结果在子 Workflow 结束后返回。具体使用方式见 [让 Team 成员调用已注册 Workflow](../workflow/team-run-workflow.md) 和 [让成员编写并管理自己的子 Workflow](../workflow/member-authored-workflow.md)。

等待语义见[派发工具](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/tools/spawn.py)、[消息工具](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/tools/message.py)及[回复唤醒测试](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/tests/workflows/test_message_wait_semantics.py)。
