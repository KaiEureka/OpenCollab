---
{
  "id": "wire-protocol-streaming",
  "title": "选择调用协议与流式响应",
  "category": "models",
  "summary": "为端点选择正确的 Chat Completions、Responses 或 Anthropic Messages 路径，并配置流式接收。",
  "aliases": [
    "协议选哪个",
    "Responses API",
    "Messages API",
    "流式输出",
    "stream_options",
    "reasoning_content",
    "finish_reason",
    "首包超时"
  ],
  "keywords": [
    "OPENCOLLAB_WIRE_PROTOCOL",
    "wire_protocol",
    "chat_completions",
    "responses",
    "OPENCOLLAB_LLM_STREAM_CHAT",
    "llm_stream_chat"
  ],
  "questions": [
    "为端点选择正确的 Chat Completions、Responses 或 Anthropic Messages 路径，并配置流式接收？"
  ],
  "related": [
    "provider-endpoint-credentials",
    "thinking-reasoning-sampling",
    "timeouts-retries",
    "configuration-diagnostics"
  ],
  "verified_ref": "e7946a42"
}
---

# 选择调用协议与流式响应

OpenAI 兼容服务默认使用 `chat_completions`，提供 Responses API 的兼容服务可选择 `responses`，原生 Anthropic 使用 Messages。协议应与端点提供的接口一致。Chat 流式接收默认关闭，需要 reasoning 增量或流事件时可通过独立开关启用。

## 按服务选择协议

Chat Completions 路径可以在 env 文件中明确写出默认值。模型名和地址均替换为所用服务提供的值。

```dotenv
OPENCOLLAB_PROVIDER=openai
OPENCOLLAB_WIRE_PROTOCOL=chat_completions
OPENCOLLAB_BASE_URL=https://your-endpoint.example/v1
OPENCOLLAB_MODEL=your-model-id
```

切换 Responses 时，把共享协议字段改成 `responses`，并确认端点支持对应 API。原生 Anthropic 则使用 `provider=anthropic`，共享 `wire_protocol` 字段保持 `chat_completions`，框架据 provider 选择 Messages 传输。把原生 Anthropic 与 `responses` 组合会在配置校验时失败。

SDK 可通过构造配置选择协议。下面的代码构造客户端并打印所选协议，调用运行入口时使用已配置的凭据。

```python
from opencollab import OpenCollab

client = OpenCollab(
    ".",
    provider="openai",
    config={"wire_protocol": "responses"},
)
print(client.configuration["wire_protocol"])
```

逻辑路由也有 `wire_protocol` 字段，每条路由可选择自己的接口。模型能力在物理模型解析后决定，端点实际支持范围还需根据服务配置核对。若服务返回接口不存在，先检查 API 基地址和协议选择。若错误指出某个请求字段无效，进一步检查该模型在目标端点上的能力声明与覆盖设置。

## 开启 Chat 流式接收

需要接收流式 reasoning 文本时，添加下列设置。这个开关让 Chat 请求携带 `stream` 和 `stream_options`，服务需要接受这些字段。

```dotenv
OPENCOLLAB_LLM_STREAM_CHAT=true
OPENCOLLAB_LLM_FIRST_EVENT_TIMEOUT=180
OPENCOLLAB_LLM_STREAM_IDLE_TIMEOUT=180
```

收到的 reasoning 文本会进入轨迹。Chat 流需要同时收到终止 `finish_reason` 和 token usage 才算正常完成。终端已经显示文字时仍可能在等待尾部统计，最终应结合运行状态判断。Responses 的流式行为由选定模型的能力设置控制，Chat 开关仅作用于 Chat Completions。

## 保留工具调用之间的续轮信息

Chat 会在启用 thinking 续轮，或模型协议要求回传推理内容时，保留历史 `reasoning_content`。这包括已声明要求回传推理内容的 DeepSeek 模型，关闭新一轮 thinking 后仍按协议保留历史内容。其它 Chat 请求使用普通 assistant 内容和工具调用。

Responses 路径保留 response items、provider 身份和相应 response session 信息。原生 Anthropic 保留签名 thinking blocks 及原始顺序，工具返回后继续按协议重放。自建传输包装器或网关也需要完整保留这些数据，模型才能在工具返回后继续生成。

## 识别流式中断的位置

连接阶段一直无结果时，先看连接超时。已连接但没有首事件时，查看 `llm_first_event_timeout`。已经收到部分内容又停止推进时，查看 `llm_stream_idle_timeout`，同时保留请求级 `llm_timeout`。各值单位为秒，整次 SDK 任务的 `timeout` 另行设置。

离线模型检查可以查看安装版本中的能力默认值，`configuration` 显示客户端生效协议与传输参数。若启用流式后才出现错误，检查服务对 `stream_options`、尾部 usage 和终止事件的支持，再结合停止原因与轨迹继续定位。

## 来源

[configs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md#L70)　[configs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md#L115)　[docs/model-inspection.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/model-inspection.md#L34)　[docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md#L182)

## 相关指南

[接入模型端点与认证凭据](provider-endpoint-credentials.md)　[配置深度思考与采样参数](thinking-reasoning-sampling.md)　[超时、重试与请求卡住诊断](../operations/timeouts-retries.md)　[检查有效配置与定位常见启动故障](../operations/configuration-diagnostics.md)
