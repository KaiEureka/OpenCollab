---
{
  "id": "message-team-agents",
  "title": "向队友发消息并继续协作",
  "category": "team",
  "summary": "找到已有成员并发送补充信息、修改要求或协作回复。",
  "aliases": [
    "给队友发消息",
    "补充上下文",
    "追加任务",
    "回复队友",
    "to_role",
    "to_aid",
    "异步消息",
    "通信拓扑"
  ],
  "keywords": [
    "message_agent",
    "team_status",
    "Agent 消息",
    "队友沟通"
  ],
  "questions": [
    "找到已有成员并发送补充信息、修改要求或协作回复？"
  ],
  "related": [
    "configure-team",
    "dispatch-team-agents",
    "wait-for-team-results"
  ],
  "verified_ref": "e7946a42"
}
---

# 向队友发消息并继续协作

`message_agent` 给已存在的队友排入一条消息，并立即返回入队回执。接收方会在后续回合读到它，可以再发消息回复。需要创建新成员并取得一次任务结果时，使用 [派发 Agent 任务并安排审阅](dispatch-team-agents.md) 中的派发工具。

## 先确认队友身份

具备 `team_status` 的成员可以用空参数 `{}` 查看当前名册。结果提供每人的 `aid`、角色、父成员和状态。按需团队中，配置已声明但尚未创建的角色可能出现在界面组织信息里，此时还没有可直接消息寻址的活动成员。

消息可以用 `to_role` 或 `to_aid` 寻址，两者恰好提供一项。`to_aid` 是整数。角色名匹配会考虑规范化形式，同一角色有多个成员时，按角色寻址会列出相关 aid，随后应挑选具体成员。

```json
{
  "to_aid": 2,
  "summary": "补充边界输入",
  "content": "请把空列表和只有空格的输入加入回归测试，并回信报告实际执行命令与结果。"
}
```

如果名册中只有一个 coder，也可以使用 `{"to_role": "coder", "summary": "核对修复", "content": "请检查刚才提出的边界输入，并回信说明结果。"}`。工具要求 `summary` 和 `content`，摘要便于快速辨认，正文应包含接收方完成动作所需的信息。

## 工具集合和拓扑一起生效

发送方需要拥有 `message_agent`，通信图也要允许它到达接收方角色。反向回复需要另一条有向边。只写 `manager: [coder]` 时，manager 到 coder 的方向获准，coder 回信还需要 `coder: [manager]`。越界发送会返回错误，目标成员不存在也会返回寻址错误。

收到消息的成员会在后续安全交接点把它作为用户消息读入自己的对话。它可以完成手头工作后处理，也可以在任务中说明需要何种结果。派发结果、消息回执与队友回信可以用 aid 关联，避免把不同成员的说明混在一起。

## 发出后怎样继续

入队回执意味着消息已交给调度器。队友的工作产物通过后续消息到达，发送调用本身只返回回执。发送方可以继续独立工作。如果接下来需要等待队友，应结束当前回合。队友之后的回信会重新唤起发送方，即使它前一轮已经结束。

当 `serialize_turns=True` 时，同一时间只运行一个成员回合。发送方结束当前回合后，接收方才能运行。工具提示会建议调用 `submit` 交出当前进展。频繁查看名册会持续占用回合与预算，也会推迟真正的交接。

若一直没有回信，先检查目标 aid 与拓扑，再看名册中的 `queued`、`busy` 或终止状态。接收方任务应明确要求回信内容，必要时在下一次交互中补充所需材料。状态解释见 [等待队友结果并读懂团队状态](wait-for-team-results.md)。

完整字段与回执说明见[消息工具](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/tools/message.py)，消息唤醒行为有[运行测试](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/tests/workflows/test_message_wait_semantics.py)覆盖。
