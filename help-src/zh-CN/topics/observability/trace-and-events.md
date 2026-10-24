---
{
  "id": "trace-and-events",
  "title": "查看 Agent 做了什么",
  "category": "observability",
  "summary": "通过轨迹和事件定位模型调用、工具执行、交接与停止原因。",
  "aliases": [
    "JSONL",
    "Tracer",
    "on_event",
    "RunEvent",
    "llm_call",
    "tool_exec",
    "response_session_id",
    "observation_errors",
    "执行过程",
    "日志怎么看"
  ],
  "keywords": [
    "轨迹",
    "运行事件",
    "trace"
  ],
  "questions": [
    "通过轨迹和事件定位模型调用、工具执行、交接与停止原因？"
  ],
  "related": [],
  "verified_ref": "e7946a42"
}
---

# 查看 Agent 做了什么

想知道 Agent 实际做了什么，可以看持久化 trace，也可以订阅运行事件。trace 适合事后分析，`RunEvent` 适合应用界面、进度显示和实时决策。

## 启用轨迹

交互 CLI 使用 `--trace`。Workflow 默认记录编排轨迹，保存目录由运行配置确定。SDK 调用可以设置 `artifacts` 并保留 trace，之后从结果目录定位 JSONL 记录。

轨迹中常见信息包括运行标识、成员标识、角色、事件类型、模型用量和工具结果。按时间与会话串联事件，可以还原请求、工具调用、通信与停止的先后顺序。

## 离线读取 JSONL

每条 trace 记录的顶层包含 `timestamp`、`run_id`、`step`、`aid`、`role`、`type`、`payload` 和 `metrics`。`step` 表示这份轨迹里的记录次序，`metrics.tokens` 和 `metrics.latency_s` 对应当前记录。Session 事件带实际成员身份，调度器自身的记录可以将 `aid` 和 `role` 留空。

下面脚本统计一份已完成轨迹中的成员事件数量。把路径替换为该运行的 `trajectory.jsonl` 或 `orchestration.jsonl`。

```python
import json
from collections import Counter
from pathlib import Path

trace_path = Path("artifacts/example/trajectory.jsonl")
counts = Counter()
with trace_path.open(encoding="utf-8") as stream:
    for line in stream:
        if line.strip():
            row = json.loads(line)
            counts[(row["run_id"], row.get("aid"), row.get("role"), row["type"])] += 1
for identity, count in counts.items():
    print(identity, count)
```

定位一次模型请求时，将 `llm_call_started`、`llm_call` 与 `llm_call_cancelled` 的 payload 按 `response_session_id` 和 `session_step` 对应起来。完成记录里的 `request_tool_names` 与 `request_tool_choice` 表示应用准备交给模型的工具视图，`tool_exec` 则记录实际工具操作。`context_shaping` 展示上下文处理，`message_sent` 与 `message_refused` 展示通信决策。需要查看具体调用时，继续读取 payload 中的输入和结果。

## 订阅运行事件

`RunControl` 可用于单 Agent 和 `ctx.agent_run`。以下代码在已有异步函数中执行，`on_event` 是同步回调。

```python
from opencollab import RunControl

events = []
result = await oc.agent(
    "说明当前项目的入口",
    run_control=RunControl(on_event=events.append),
)
for event in events:
    print(event.type, event.aid, event.used_tokens)
```

`on_event` 与持久化 trace 独立，设置 `trace=False` 仍可以接收回调。`RunEvent` 包含 `type`、`run_id`、`session_id`、`aid`、`used_tokens`、`steps` 和独立复制的 `data`。其中 `used_tokens` 是会话累计值，`usage` 事件的 `data["total_tokens"]` 是该次已记账用量。事件适合观察用量、预算决策、上下文处理、错误、停止和清理过程，工具执行细节从 trace 或完整会话读取。

回调应尽快同步返回。要写数据库或网络服务，可以在应用中把事件转交队列。回调错误会保留在 `result.metrics["observation_errors"]`，`ctx.agent_run` 则通过返回值的 `observation_errors` 提供。回调持续接收原运行的事件，直到迟到用量结算、清理结束，并收到 `cleanup_completed`。

## 从停止原因回查

先看结果状态，再找到同一运行的 `session_stopped`、错误或清理事件。检查最后一条模型请求是否完成，工具是否有对应回执，以及环境是否已静止。`context_shaping` 能显示压缩是否实际发生，`usage` 能区分普通生成、摘要与迟到响应。

模型请求开始或取消时可能尚未返回用量。结束后的迟到成功或错误仍可补记，分析费用时应读完整的后续清理记录。

## trace 与业务结论

查看测试计划时读模型文本，确认测试执行时读工具命令与实际结果。分析通信时，先从声明读取允许的通信方向，再从发送记录确认实际消息。根据问题选择对应事件，便能找到支撑结论的记录。

日志可能包含任务源码、用户输入和工具输出。分享给他人前应检查内容范围，并按项目要求处理凭据和私密数据。

## 源码与进一步阅读

[opencollab/adapters/trace.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/trace.py) · [opencollab/application/run_control.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/run_control.py) · [docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md) · [docs/runtime-design.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/runtime-design.md)
