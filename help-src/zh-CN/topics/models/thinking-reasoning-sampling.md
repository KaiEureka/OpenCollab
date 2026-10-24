---
{
  "id": "thinking-reasoning-sampling",
  "title": "配置深度思考与采样参数",
  "category": "models",
  "summary": "按当前 provider、协议和模型能力选择 thinking、reasoning effort 与采样设置。",
  "aliases": [
    "深度思考",
    "推理强度",
    "关闭思考",
    "reasoning",
    "enable_thinking",
    "budget_tokens",
    "adaptive",
    "output_config",
    "temperature",
    "top_p",
    "max_output_tokens"
  ],
  "keywords": [
    "OPENCOLLAB_THINKING",
    "OPENCOLLAB_THINKING_PARAMS",
    "OPENCOLLAB_REASONING_EFFORT",
    "thinking",
    "thinking_params",
    "reasoning_effort",
    "OPENCOLLAB_TEMPERATURE",
    "OPENCOLLAB_TOP_P"
  ],
  "questions": [
    "按当前 provider、协议和模型能力选择 thinking、reasoning effort 与采样设置？"
  ],
  "related": [
    "wire-protocol-streaming",
    "token-step-concurrency-limits",
    "configuration-diagnostics"
  ],
  "verified_ref": "e7946a42"
}
---

# 配置深度思考与采样参数

深度思考和采样参数通过客户端配置设置，团队角色还可覆盖自己的 `thinking` 与 `temperature`。OpenCollab 默认 `thinking=false`，实际请求字段由协议和模型能力决定。选择参数时先确认物理模型及协议，再设置该服务支持的原生格式。

## 查看默认行为

当前能力声明中，`qwen3.8-flash` 的 Chat 请求显式发送 `enable_thinking=false`。`deepseek-flash`、`deepseek-v4-pro` 和 `deepseek-v4-flash` 在 Chat 与 Anthropic 路径使用 disabled thinking，Responses 将 `reasoning` 的 `effort` 设为 `none`。显式 Responses reasoning effort 优先于该默认。普通模型的实际默认按 provider 能力处理。

可以先用 `inspect_model_runtime` 查看已安装运行时的模型能力，再用 `OpenCollab.configuration` 查看显式覆盖。能力检查查询本地表，服务端能否接受参数仍应结合所用端点。

## 为 Chat 和 Responses 设置推理

OpenAI 兼容 Chat 的 `thinking_params` 通过 `extra_body` 发送。下面是服务接受 `enable_thinking` 时可用的 env 写法。

```dotenv
OPENCOLLAB_THINKING=true
OPENCOLLAB_THINKING_PARAMS={"enable_thinking":true}
```

Responses 使用 `OPENCOLLAB_REASONING_EFFORT` 表达所支持的推理强度，接受 `none`、`minimal`、`low`、`medium`、`high`、`xhigh` 和 `max`。具体模型支持哪些值应以其服务能力为准。

```dotenv
OPENCOLLAB_WIRE_PROTOCOL=responses
OPENCOLLAB_REASONING_EFFORT=high
```

SDK 对应 `config` 中的小写字段 `thinking`、`thinking_params` 和 `reasoning_effort`。`thinking_params` 必须是可序列化为 JSON 的对象，环境变量也必须包含 JSON 对象。数组或无效 JSON 会在配置阶段失败。切换协议或物理模型后，重新核对这些原生参数。保留上一服务的字段会改变请求内容，服务可能直接拒绝，也可能按自身默认处理。

## 使用原生 Anthropic thinking

手动 thinking 至少需要 1,024 个 thinking token，并小于单次 `max_output_tokens`。下面预留 16,000 thinking token 和更大的总输出空间，模型名与凭据沿用已配置的服务。

```dotenv
OPENCOLLAB_PROVIDER=anthropic
OPENCOLLAB_MAX_OUTPUT_TOKENS=32768
OPENCOLLAB_THINKING=true
OPENCOLLAB_THINKING_PARAMS={"thinking":{"type":"enabled","budget_tokens":16000}}
```

支持 adaptive thinking 的模型可以使用原生 adaptive 结构和该模型认可的 effort。

```dotenv
OPENCOLLAB_THINKING_PARAMS={"thinking":{"type":"adaptive"},"output_config":{"effort":"high"}}
```

原生 thinking 请求会按能力省略 `temperature`。支持的较早 Claude 4 手动 thinking 模型、Claude Opus 4.6 与 Sonnet 4.6 的 adaptive thinking，以及已声明的 DeepSeek 原生 Anthropic 模型，可按能力使用 0.95 到 1.0 的 `top_p`。更新模型遵守各自采样要求。工具续轮需要保留原生 thinking blocks 和签名数据，框架的标准传输负责处理。

## 调整采样与角色覆盖

全局 `temperature` 默认 0.2，范围为 0 到 2，显式 0.0 有效。`top_p` 范围为 0 到 1，省略时使用 provider 默认。在支持采样的原生 Claude 4 普通请求里，显式 `top_p` 优先于温度。模型或 thinking 模式要求时，请求构造器会省略相应采样字段。

团队 YAML 的角色可把 `temperature` 设为 0.0，把 `thinking` 设为 true，未设置的字段继承全局值。更详细的原生 thinking 参数放在客户端模型设置中，角色配置字段应按团队 schema 使用。

## 留出会话额度

单次输出上限与整次会话 `budget` 分开控制。有限会话在确定输出空间前先保留输入 token，手动 thinking 还需要思考额度与回答空间。剩余额度低于组合最低需求时会以 budget-exceeded 原因停止，并保留已完成工具结果及用量。遇到请求前参数错误，先核对模型与格式，遇到额度停止，再检查会话预算、输入长度和单次输出上限。

## 来源

[configs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md#L140)　[configs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md#L162)　[configs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md#L192)　[opencollab/bootstrap/config.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/config.py#L176)

## 相关指南

[选择调用协议与流式响应](wire-protocol-streaming.md)　[设置 token 预算、步数与运行并发](../operations/token-step-concurrency-limits.md)　[检查有效配置与定位常见启动故障](../operations/configuration-diagnostics.md)
