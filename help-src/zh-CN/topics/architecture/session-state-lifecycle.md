---
{
  "id": "session-state-lifecycle",
  "title": "Session 如何保存进度并继续执行",
  "category": "architecture",
  "summary": "读懂会话状态、停止原因和恢复方式，判断继续运行会从哪里开始。",
  "aliases": [
    "状态机",
    "SessionState",
    "SessionPhase",
    "DONE",
    "STOPPED",
    "ERROR",
    "AWAITING_EVENTS",
    "read_session_snapshot"
  ],
  "keywords": [
    "Session",
    "会话状态",
    "恢复执行"
  ],
  "questions": [
    "读懂会话状态、停止原因和恢复方式，判断继续运行会从哪里开始？"
  ],
  "related": [
    "runtime-execution-model",
    "service-integration-lifecycle",
    "workspace-recovery"
  ],
  "verified_ref": "e7946a42"
}
---

# Session 如何保存进度并继续执行

Session 保存 Agent 对话中的消息、累计用量、当前阶段和待处理事件，以便后续继续执行。等待子任务时，它可以停在事件等待阶段，结果到齐后继续。新用户轮次会重新开始本轮执行，累计 token 与步数仍属于同一个会话生命周期。

## 会话里保存了什么

`SessionState.messages` 保存完整对话，包括用户输入、模型回复、工具调用与结果。`used_tokens` 记录累计输入加输出，`step_count` 记录会话累计步骤，`context_tokens` 反映最近一次请求的输入大小。阶段与 `terminal_reason` 共同说明当前进展和终止原因。

待处理事件表关联延迟工具调用与结果。调度器已排队、等待加入模型请求的用户消息会单独保存。新一轮外部用户输入也会记录从接收到开始执行的状态，以便在保存、等待和恢复时保留消息顺序。

## 一轮如何推进

| 阶段 | 当前执行的工作 |
| --- | --- |
| `IDLE` | 等待开始或恢复执行 |
| `PRECHECK` | 检查取消、预算、步数与运行条件 |
| `CALLING_LLM` | 等待模型响应 |
| `HANDLING_RESPONSE` | 处理回复及工具调用 |
| `EXECUTING_TOOLS` | 执行已选工具 |
| `AWAITING_EVENTS` | 等待延迟子任务结果 |
| `AUTOSAVING` | 保存本步状态，再继续或结束 |

普通工具执行结束后会保存，再回到前置检查。派发工具产生延迟结果时，Session 进入 `AWAITING_EVENTS`，待整批结果到齐并保存后继续执行。某一步包含 `submit` 时，也会先完成本步保存，再结束当前回合。

`DONE` 保存最终回答，`STOPPED` 保存停止原因和可用进展，`ERROR` 保存未处理故障。新消息可以让已结束当前回合的成员重新执行。Team 的整体结束还需等待活动成员与待投递消息处理完毕，具体见 [等待队友结果并读懂团队状态](../team/wait-for-team-results.md)。

## 新轮次与进程重启

同一 Session 接受新用户输入时，会重置本轮的循环检测、进度信号和临时工具选择等状态，累计额度继续保留。独立的 SDK `agent` 调用通常创建新会话，Workflow 的多次 `ctx.agent` 也分别创建对话。要让后续调用使用先前结论，应显式传入输出或共享需要的文件。

从保存记录恢复时，累计用量、结束状态和可恢复的等待关系会被保留。模型请求和工具执行中的进程内协程已经结束，恢复会从 `IDLE` 重新进入运行过程。排队消息、工具结果配对和延迟用量仍需按保存内容继续处理。

读取保存材料可以使用公开接口。

```python
from opencollab import OpenCollab

snapshot = OpenCollab.read_session_snapshot("/path/to/session.json")
print(sorted(snapshot))
```

文件路径应替换为实际会话快照。接口读取快照与已经完成的日志条目，适合先查看保存内容，再决定下一步恢复操作。终端保存和恢复入口见 [保存会话与恢复历史](../operations/session-save-resume.md)。

## 完整记录与模型视图

完整消息用于追踪与恢复，发给模型的视图会按上下文压力裁剪和压缩。当前上下文来源包括 profile、角色、团队、技能、派发任务与项目概览，通用外部记忆来源需要另行接入。状态定义见[会话状态实现](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/domain/session.py)，持久化与上下文关系见[运行设计](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/runtime-design.md)。
