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

轨迹中常见信息包括运行标识、成员标识、角色、事件类型、模型用量和工具结果。按时间与会话串联事件，能看到请求开始、工具调用、通信和任务停止的关系。

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

回调应尽快返回。要写数据库或网络服务，可以在应用中把事件转交队列。回调错误会进入运行指标里的观察错误记录，读取输出后也应检查相关诊断。

## 从停止原因回查

先看结果状态，再找到同一运行的 `session_stopped`、错误或清理事件。检查最后一条模型请求是否完成，工具是否有对应回执，以及环境是否已静止。`context_shaping` 能显示压缩是否实际发生，`usage` 能区分普通生成、摘要与迟到响应。

模型请求开始或取消时可能尚未返回用量。结束后的迟到成功或错误仍可补记，分析费用时应读完整的后续清理记录。

## trace 与业务结论

一条“准备测试”的模型文本证明了计划。工具命令及实际结果才能说明执行行为。类似地，声明通信边与实际成功发送属于不同证据。先选择要回答的问题，再找对应事件，能减少把界面状态或模型描述当作完成证明的误解。

日志可能包含任务源码、用户输入和工具输出。分享给他人前应检查内容范围，并按项目要求处理凭据和私密数据。

## 源码与进一步阅读

[opencollab/adapters/trace.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/trace.py) · [opencollab/application/run_control.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/run_control.py) · [docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md) · [docs/runtime-design.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/runtime-design.md)
