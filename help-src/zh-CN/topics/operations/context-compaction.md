---
{
  "id": "context-compaction",
  "title": "上下文窗口与自动压缩",
  "category": "operations",
  "summary": "理解长任务何时压缩上下文，如何调整有效窗口，并定位上下文溢出后的停止。",
  "aliases": [
    "自动总结",
    "爆上下文",
    "context overflow",
    "长对话",
    "工具输出截断",
    "记忆丢了",
    "RunControl",
    "context_tokens"
  ],
  "keywords": [
    "上下文压缩",
    "OPENCOLLAB_CONTEXT_WINDOW",
    "context_window",
    "context",
    "no_history_compaction",
    "tool_result_budget",
    "history_trigger_tokens",
    "session.history_compaction"
  ],
  "questions": [
    "理解长任务何时压缩上下文，如何调整有效窗口，并定位上下文溢出后的停止？"
  ],
  "related": [
    "thinking-reasoning-sampling",
    "token-step-concurrency-limits",
    "configuration-diagnostics",
    "session-save-resume",
    "token-cache-cost",
    "trace-and-events"
  ],
  "verified_ref": "e7946a42"
}
---

# 上下文窗口与自动压缩

长任务的完整会话保存在消息记录中，每次发送给模型的是按上下文窗口整理后的视图。默认策略在压力升高时缩减旧工具结果、移除适合移除的交换组，并按需要生成总结。排查模型遗漏信息或上下文溢出时，要同时查看持久化历史和本次请求的整理记录。

## 先确认实际窗口

运行时从模型能力表推断上下文窗口，显式 `context_window` 可以覆盖该值。这个值应匹配实际物理模型服务，较大的声明会让框架更晚整理历史，服务本身的窗口仍由后端决定。

```python
from opencollab import OpenCollab

client = OpenCollab(".", config={"context_window": 131072})
print(client.configuration["context_window"])
```

示例只构造客户端。env 的对应字段是 `OPENCOLLAB_CONTEXT_WINDOW`，逻辑路由也能为物理模型设置自己的窗口。`inspect_model_runtime` 显示安装版本内置的默认能力，客户端显式覆盖应读取 `configuration`。

## 默认整理怎样发生

先对单个工具结果设置大小限额，随后在上下文压力升高时清理旧的可压缩工具输出，再移除允许移除的旧工具交换组，仍需腾出空间时总结剩余的适合压缩区段。每一步处理前一步生成的视图，压力已经降低后，后面的处理可以保持原样。

模型调用与工具结果需要成对保留，近期证据也限制可移除范围。高优先级启动上下文受到保护，原生签名 thinking blocks 和加密 reasoning 按原样重放。运行时用 provider 报告的输入输出校准当前会话估算，新加入内容和恢复后的会话先使用相应估算方式。

已知窗口会扣除输出余量和安全空间，再确定触发阈值，目标通常为触发值的 75%。小窗口使用按窗口缩放的余量与近期证据保留数。未知窗口采用 120,000 和 90,000 token 的固定触发值与目标。轨迹中的 `session.history_compaction` 记录实际数值和来源，比较长任务时以该记录为准。

## 调整团队和单 Agent 策略

团队文件的 `context` 可以设置策略与单工具结果字符上限。下面的片段加入已有团队 YAML 顶层，保留该文件原有的角色和关系。

```yaml
context:
  policy: default
  tool_result_budget: 20000
```

`tool_result_budget` 的单位为字符，需为正整数。选择 `no_history_compaction` 会保留单工具结果大小限制，同时关闭历史压缩层。适合需要研究完整请求历史的任务，但仍应确保输入能放进后端窗口。默认策略在低压力时保留历史，通常更适合持续编辑和验证。

`RunControl.history_trigger_tokens` 可在已知窗口下进一步降低压缩触发值。以下是放在已有 `async` 函数中的片段，`client` 是先前构造的客户端。

```python
from opencollab import RunControl

result = await client.agent2(
    "阅读大型项目，逐步收集主要模块的证据并给出说明。",
    tools="read",
    budget=150_000,
    run_control=RunControl(history_trigger_tokens=60_000),
)
```

该值至少为 2，目标保持为有效触发值的 75%。未知窗口沿用固定默认阈值。

## 遇到溢出时看什么

被分类为上下文溢出的 provider 错误会触发强制压缩和一次重试，再次溢出后会保留原因并停止。过大的受保护输入仍可能占满窗口，此时应缩小任务输入或修正窗口声明。查看 `context_shaping` 事件和压缩轨迹，再结合 `reason` 判断。

总结本身也会消耗 token，并单独计入用量。`context_tokens` 表示单次调用输入大小，`used_tokens` 表示累计输入加输出，两者适合回答不同问题。需要追溯完整工具内容时，回看保存的会话与工具记录。

## 来源

[docs/runtime-design.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/runtime-design.md#L25)　[docs/runtime-design.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/runtime-design.md#L67)　[configs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md#L286)　[docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md#L101)

## 相关指南

[配置深度思考与采样参数](../models/thinking-reasoning-sampling.md)　[设置 token 预算、步数与运行并发](token-step-concurrency-limits.md)　[检查有效配置与定位常见启动故障](configuration-diagnostics.md)　[保存会话与恢复历史](session-save-resume.md)　[统计 token、缓存命中和费用](../observability/token-cache-cost.md)　[查看 Agent 做了什么](../observability/trace-and-events.md)
