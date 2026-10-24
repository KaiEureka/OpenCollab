---
{
  "id": "dispatch-team-agents",
  "title": "派发 Agent 任务并安排审阅",
  "category": "team",
  "summary": "向合适角色派发清晰任务，理解并行子任务和带审阅的执行过程。",
  "aliases": [
    "分配任务",
    "叫队友干活",
    "委派",
    "delegate",
    "specialist",
    "coder",
    "reviewer",
    "重复派发",
    "DeferredCall"
  ],
  "keywords": [
    "spawn_agent",
    "spawn_with_review",
    "派发任务",
    "子 Agent"
  ],
  "questions": [
    "怎样派一个子 Agent 去改代码？",
    "spawn_agent 与发消息有什么区别？",
    "子 Agent 的改动怎样回到父任务？"
  ],
  "related": [
    "configure-team",
    "wait-for-team-results",
    "adopt-team-changes",
    "message-team-agents",
    "team-run-workflow",
    "member-authored-workflow",
    "isolation-overview"
  ],
  "verified_ref": "e7946a42"
}
---

# 派发 Agent 任务并安排审阅

具备 `spawn_agent` 的成员可以派发一个专门任务，并在子成员完成后拿到结果继续处理。任务能发给谁由角色配置和有向拓扑共同决定。把输入、交付位置与验证目标写进任务，可以让子成员在独立对话中直接开工。

## 传清任务与已知信息

工具调用使用 JSON 参数，`role` 和 `task` 必填，`context` 可省略。下面是允许派发 coder 的入口成员可发出的合法调用。

```json
{
  "role": "coder",
  "task": "修复 parser 对空输入的处理，补充对应回归测试，并报告修改文件和执行结果。",
  "context": "入口位于 src/parser.py，项目测试命令为 python -m pytest -q。保留现有公开函数签名。"
}
```

`context` 应提供已经确认的线索、必要片段和约束。子成员有自己的消息历史，显式传递的信息会进入它的启动任务。它能使用哪些文件和命令取决于该角色的工具与执行环境。默认团队允许 Analyst 派发 Coder 和 Tester，新增角色需要同时声明职责、工具和拓扑边。

工具以延迟调用连接父子结果。父成员会暂停等待，同一批发出的多个 spawn 可以并行执行，整批结果齐备后再继续。需要并行处理两个对象时，应分别派发两个任务，并明确各自修改的文件范围与最终汇总要求。实际并发仍受预算、调度与模型请求能力约束。

## 重复任务与固定名册

同一任务正在被处理时，重复派发会返回原有 `aid` 和说明，原任务完成结果仍沿原工具调用交付。应继续使用该任务的结果，再决定修订。把同一任务略改措辞连续重发，会增加资源消耗并让交付关系变复杂。

`prebuild_team=True` 使用运行开始时的固定成员。此时继续创建成员会被拒绝，应先用 `team_status` 找到现有成员，再通过 `message_agent` 交接工作。派发和消息的等待方式见 [等待队友结果并读懂团队状态](wait-for-team-results.md)。

## 安排实现与审阅循环

需要实现后自动审阅的场景，可以给入口配置 `spawn_with_review`，并允许它到达 `coder` 和 `reviewer`。内置默认团队的角色与工具需要相应扩展。

```json
{
  "task": "修复 parser 的空输入问题，并让审阅者核对回归测试和兼容性。",
  "context": "以项目现有测试约定为准，报告实际执行的测试。",
  "max_iterations": 2
}
```

工具先创建 Coder，待其成功完成后创建 Reviewer，并从审阅返回的 verdict 决定是否继续。默认最多三轮，`max_iterations` 接受 1 到 3 的整数。未通过时，新一轮 Coder 根据先前实现和反馈在新工作区继续。Coder 异常停止或审阅创建失败会保留具体失败阶段与已有产物。

完成结果中的 `aid` 可以关联成员、回答与文件变化。入口声明采纳工具时，应采用成功直接 Coder 的结果，再派发新的 Tester 检查交付目录。实现与审阅循环的通过结果仍需对应到实际交付文件，具体见 [采用队友改动并验证交付](adopt-team-changes.md)。

参数与延迟行为见[派发工具](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/tools/spawn.py)，角色组织方法见 [Team 完整指南与团队配置](configure-team.md)。
