---
{
  "id": "timeouts-retries",
  "title": "超时、重试与请求卡住诊断",
  "category": "operations",
  "summary": "按卡住的位置调整模型请求或整次任务的时间限制，并理解重试如何停止。",
  "aliases": [
    "请求卡住",
    "首包慢",
    "半路没输出",
    "连接超时",
    "429",
    "503",
    "Retry-After",
    "llm_timeout",
    "llm_max_retries",
    "provider_error_time_budget"
  ],
  "keywords": [
    "OPENCOLLAB_LLM_TIMEOUT",
    "OPENCOLLAB_LLM_CONNECT_TIMEOUT",
    "OPENCOLLAB_LLM_FIRST_EVENT_TIMEOUT",
    "OPENCOLLAB_LLM_STREAM_IDLE_TIMEOUT",
    "OPENCOLLAB_LLM_MAX_RETRIES",
    "OPENCOLLAB_PROVIDER_ERROR_TIME_BUDGET",
    "timeout",
    "cleanup_timeout"
  ],
  "questions": [
    "按卡住的位置调整模型请求或整次任务的时间限制，并理解重试如何停止？"
  ],
  "related": [
    "wire-protocol-streaming",
    "cancellation-cleanup",
    "configuration-diagnostics",
    "token-step-concurrency-limits"
  ],
  "verified_ref": "e7946a42"
}
---

# 超时、重试与请求卡住诊断

请求等待在哪个阶段发生，就调整该阶段的超时。模型连接、首个流事件、流事件之间的空闲和 provider 请求各有独立设置。整次任务的期限通过 SDK `timeout=` 设置，执行结束后的资源清理由 `cleanup_timeout=` 另行限制。

## 根据停顿位置选设置

以下变量单位都是秒，默认值适合先运行再根据服务表现调整。

| 设置 | 默认值 | 覆盖阶段 |
| --- | --- | --- |
| `OPENCOLLAB_LLM_CONNECT_TIMEOUT` | 30 | 建立连接 |
| `OPENCOLLAB_LLM_FIRST_EVENT_TIMEOUT` | 180 | 等待第一个流事件 |
| `OPENCOLLAB_LLM_STREAM_IDLE_TIMEOUT` | 180 | 相邻流事件之间的等待 |
| `OPENCOLLAB_LLM_TIMEOUT` | 600 | provider 请求超时，包含流式传输的单次 socket 读取 |

服务连接困难时，先排查地址与网络，再看连接限制。已经建立连接但生成很慢时，可调整首事件等待。输出到一半长时间停顿时，检查流空闲限制和尾部完成事件。单纯增加整次任务期限，仍会保留这些请求阶段的限制。

下面是一组可以写进选定 env 文件的设置。数值需要按实际端点表现选择，修改文件后重新构造 SDK 客户端。

```dotenv
OPENCOLLAB_LLM_CONNECT_TIMEOUT=30
OPENCOLLAB_LLM_FIRST_EVENT_TIMEOUT=240
OPENCOLLAB_LLM_STREAM_IDLE_TIMEOUT=180
OPENCOLLAB_LLM_TIMEOUT=600
OPENCOLLAB_LLM_MAX_RETRIES=3
OPENCOLLAB_PROVIDER_ERROR_TIME_BUDGET=120
```

## 给整个任务设置期限

以下脚本为一次 Agent 执行设置五分钟期限，另给清理十秒。达到运行期限后，SDK 返回 `stopped` 且 `reason="timeout"`。调用方主动取消任务时则在资源清理后收到 `asyncio.CancelledError`。

```python
import asyncio
from opencollab import OpenCollab

async def main() -> None:
    client = OpenCollab(".")
    result = await client.agent(
        "阅读项目结构，并给出最小测试步骤。",
        tools="read",
        timeout=300,
        cleanup_timeout=10,
        budget=100_000,
    )
    print(result.status, result.reason)
    print(result.metrics.get("session_quiesced"))

asyncio.run(main())
```

工作流角色的 `timeout=` 限制该角色，SDK `workflow(timeout=...)` 限制整次工作流。`cleanup_timeout` 应为有限正数，描述执行结束后框架等待自己拥有的资源停止的时段。没有 token 或步数上限的运行也保留这些时间限制。

## 重试次数和失败时间额度

`llm_max_retries` 默认 3，表示被分类为可重试 provider 故障后的重试次数。框架使用退避，并在服务提供时考虑 `Retry-After`。普通配置错误和上下文溢出有各自处理路径，查看错误分类可判断请求进入哪条路径。

`provider_error_time_budget` 默认 0，此时只按重试次数控制。设置正数后，失败尝试及重试延迟还共同受到该时间额度限制，次数或时间任一先达到上限都会结束重试。`llm_max_retries=0` 关闭这类额外尝试。它们与请求超时共同决定一次失败调用可能占用的时间。

同一工作流中的会话、总结与候选共享该工作流的 provider 重试时间额度。由 `create_model_client()` 创建的客户端用于新的 SDK Agent 或 Workflow 运行时，会得到该次运行的新额度。

## 记录停止时的最终状态

看到 timeout 后，先保留状态、原因和 `metrics`，再检查轨迹、会话与可用文件。部分输出可能已经保存，迟到的 provider usage 会在框架拥有的清理阶段结算一次。开始和取消事件的零已知 token 只是当时观测值，最终用量应结合完成记录和清理结果。

复查故障时记录有效协议、四类超时、重试次数、失败时间额度和错误发生阶段。`agent_failures` 中的类型、HTTP 状态与 `exception_chain` 可以帮助区分连接、provider 与执行环境问题，完整轨迹则用来找到对应调用。

## 来源

[configs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md#L94)　[opencollab/bootstrap/config.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/config.py#L199)　[docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md#L34)　[docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md#L121)

## 相关指南

[选择调用协议与流式响应](../models/wire-protocol-streaming.md)　[中断任务与检查资源清理](cancellation-cleanup.md)　[检查有效配置与定位常见启动故障](configuration-diagnostics.md)　[设置 token 预算、步数与运行并发](token-step-concurrency-limits.md)
