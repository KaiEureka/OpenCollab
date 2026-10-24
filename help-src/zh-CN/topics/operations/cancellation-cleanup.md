---
{
  "id": "cancellation-cleanup",
  "title": "中断任务与检查资源清理",
  "category": "operations",
  "summary": "从终端或 Python 中停止正在运行的任务，并确认部分工作、用量和环境的最终状态。",
  "aliases": [
    "取消任务",
    "停止 agent",
    "超时退出",
    "强制中断",
    "清理没结束",
    "延迟用量",
    "工作区恢复",
    "保留 worktree",
    "OPENCOLLAB_TOOL_CANCELLATION_CLEANUP_TIMEOUT",
    "OPENCOLLAB_TOOL_CANCELLATION_FORCE_TIMEOUT",
    "OPENCOLLAB_TOOL_ENVIRONMENT_ABORT_TIMEOUT"
  ],
  "keywords": [
    "Ctrl+C",
    "asyncio.CancelledError",
    "cleanup_timeout",
    "session_quiesced",
    "environment_quiesced",
    "cleanup_quiesced",
    "execution_quiesced",
    "workspace_ready"
  ],
  "questions": [
    "从终端或 Python 中停止正在运行的任务，并确认部分工作、用量和环境的最终状态？"
  ],
  "related": [
    "session-save-resume",
    "timeouts-retries",
    "model-routing-request-capacity",
    "token-step-concurrency-limits",
    "workspace-recovery",
    "run-results"
  ],
  "verified_ref": "e7946a42"
}
---

# 中断任务与检查资源清理

终端中用 Ctrl+C 中断当前轮次，Python 中取消运行任务，或给 SDK 调用设置 `timeout`，都能让执行进入停止与清理。随后应检查保存记录、最终用量和环境状态，再继续操作同一工作区。SDK 返回的 `metrics` 把会话结束与环境清理分别表示。

## 终端怎样中断

运行期间 Ctrl+C 会中断当前轮次并清空等待执行的输入，存在待回答问题时也会拒绝该问题。若会话已经空闲，Ctrl+C 退出。普通退出还可使用 `/exit` 或 `/quit`。停止后终端保留可用的部分回答，并显示停止或失败原因。

需要限制单次运行时间时，可以设置 SDK 的 `timeout=`，并从返回结果读取停止状态。运行期限到达时返回 `status="stopped"` 和 `reason="timeout"`。调用方主动取消则在框架拥有的清理阶段结束后传播 `asyncio.CancelledError`。

## 在 Python 中连接取消操作

下面展示一个由应用主动取消的完整任务。实际界面可以把 `task.cancel()` 接到停止按钮。示例只使用读取工具，模型设置沿用已准备的配置。

```python
import asyncio
from opencollab import OpenCollab, RunControl

async def main() -> None:
    events = []
    client = OpenCollab(".")
    task = asyncio.create_task(client.agent(
        "阅读项目入口并整理主要模块。",
        tools="read",
        budget=100_000,
        cleanup_timeout=10,
        artifacts="artifacts/cancel-example",
        run_control=RunControl(on_event=events.append),
    ))
    await asyncio.sleep(1)
    if not task.done():
        task.cancel()
    try:
        result = await task
        print(result.status, result.reason)
    except asyncio.CancelledError:
        print("应用已收到取消结果")
    print([event.type for event in events])

asyncio.run(main())
```

`cleanup_timeout` 接受有限正数，用于限制执行结束后框架清理自身资源的等待时间。单独的 `RunControl.tool_cancellation_cleanup_timeout` 可以调整工具取消清理等待。事件接收器需要尽快同步返回，可先保存事件，再由应用的其他部分处理。

## 工具取消怎样等待

工具超时或调用方取消后，运行时先请求工具协程取消，并等待 `OPENCOLLAB_TOOL_CANCELLATION_CLEANUP_TIMEOUT`，默认 1 秒。工具仍未停止或清理返回错误时，环境会被撤销，再执行环境中止。`OPENCOLLAB_TOOL_ENVIRONMENT_ABORT_TIMEOUT` 默认给中止操作 1 秒，之后取消中止协程，再由 `OPENCOLLAB_TOOL_CANCELLATION_FORCE_TIMEOUT` 默认等待 0.5 秒。仍在结束的任务继续归该运行追踪，工具结果会带出清理失败信息。

这些进程变量在模块导入时读取，调整时在启动 Python 或 CLI 前导出有限正数。单 Agent 或 `ctx.agent_run` 中的 `RunControl.tool_cancellation_cleanup_timeout` 可单独覆盖本次会话第一段工具取消等待。SDK 的 `cleanup_timeout` 限制运行结束后的资源清理时间，调用方应根据返回的清理状态安排下一次操作。具体顺序见[工具取消与环境中止实现](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/tool_execution_runtime.py)。

## 核对停止后的状态

`session_quiesced` 表示会话及持久化阶段已确认结束。框架创建并拥有执行环境时，还会报告 `environment_quiesced`，并汇总到 `cleanup_quiesced` 与 `execution_quiesced`。通过 `result.metrics.get(...)` 读取这些字段。

调用方传入的环境继续由调用方管理。返回结果中的 `environment_quiesced`、`cleanup_quiesced` 和 `execution_quiesced` 均为 `None`。调用方完成自己的环境清理后，检查并另行记录环境是否已停止。再次写入前，应同时确认会话停止和工作区可用。

Workflow 的 `ctx.agent_run` 返回受控调用结果，其中 `workspace_ready` 直接用于决定共享上下文是否可以开始后续工作。超时后仍有 provider 或工具工作在清理时，调用会等待自己的资源，清理未完成时返回失败并阻止共享上下文继续执行。待结束任务及其保留预算继续由工作流管理。

## 保存用量与部分改动

取消发生时已经显示的 token 统计可能还会变化。迟到的 provider 成功或错误用量在框架拥有的清理阶段结算一次，`usage` 事件的 `late` 与 `purpose` 可帮助识别来源，`cleanup_completed` 标记该接收器对应运行的清理结束。最终花费从保存的用量记录读取。

失败或停止的 Team 子成员仍可交回已有改动。补丁捕获失败，或返回消息容纳不下完整补丁时，原生工作树会保留完整文件并报告恢复位置。先导出所需改动，再按工作区恢复指南释放工作树。开始下一次任务前，确认会话清理结果以及自己负责的环境状态，随后检查工作区差异。

## 来源

[opencollab/adapters/cli/main.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/cli/main.py#L182)　[docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md#L121)　[docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md#L149)　[docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md#L165)

## 相关指南

[保存会话与恢复历史](session-save-resume.md)　[超时、重试与请求卡住诊断](timeouts-retries.md)　[逻辑模型路由与共享请求容量](../models/model-routing-request-capacity.md)　[设置 token 预算、步数与运行并发](token-step-concurrency-limits.md)　[找回隔离任务的改动并清理资源](../isolation/workspace-recovery.md)　[读取运行结果与判断任务是否完成](../observability/run-results.md)
