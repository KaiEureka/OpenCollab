---
{
  "id": "configuration-reference",
  "title": "配置参数与环境变量速查",
  "category": "models",
  "summary": "按环境变量、SDK 字段和运行入口查找现有配置的默认值与详细用法。",
  "aliases": ["参数表", "配置项大全", "环境变量大全", "默认值", "config reference", "configuration reference"],
  "keywords": ["OpenCollabConfig", "OPENCOLLAB_CONFIG_FILE", "OPENCOLLAB_MODEL_ROUTES", "配置参数", "SDK config", "运行参数"],
  "questions": ["有哪些配置项以及默认值是什么", "环境变量和 SDK 参数怎样对应"],
  "related": ["configuration-precedence", "provider-endpoint-credentials", "model-routing-request-capacity", "thinking-reasoning-sampling", "configuration-diagnostics", "token-step-concurrency-limits"],
  "verified_ref": "e7946a42"
}
---

# 配置参数与环境变量速查

客户端配置、团队声明和一次运行的参数分别管理不同层级。模型连接与请求设置放在环境变量或 `OpenCollab(config=...)`，角色与拓扑放在 Team YAML，任务预算和超时等放在 `agent()`、`team()` 或 `workflow()` 的调用参数中。按这一层级查找设置，可以避免把合法参数放进错误入口。

下表列出当前源码的内置默认值。复制 `configs/.env.example` 后，示例文件里的模型、端点和预算会成为显式配置。具体取值可通过 [有效配置检查](../operations/configuration-diagnostics.md) 核对，配置来源顺序见 [配置读取与覆盖](configuration-precedence.md)。

## 模型与认证

| 环境变量 | SDK 配置字段 | 默认值与作用 |
| --- | --- | --- |
| `OPENCOLLAB_MODEL` | `model` | `gpt-4o`，可填物理模型名或已声明的逻辑路由名 |
| `OPENCOLLAB_PROVIDER` | `provider` | `openai`，原生 Messages 使用 `anthropic` |
| `OPENCOLLAB_BASE_URL` | `base_url` | 未设置时采用所选 provider 的默认端点 |
| `OPENCOLLAB_API_KEY` | `api_key` | 默认未设置，按 provider 与端点选择认证来源 |
| `ANTHROPIC_AUTH_TOKEN` | `auth_token` | 默认未设置，原生 Anthropic bearer 认证 |
| `OPENCOLLAB_WIRE_PROTOCOL` | `wire_protocol` | `chat_completions`，OpenAI 兼容端点也可选 `responses` |
| `OPENCOLLAB_MODEL_ROUTES` | `model_routes` | 默认空映射，声明逻辑模型到实际端点的路由 |
| `OPENCOLLAB_REQUEST_POOL` | `request_pool` | 默认未设置，同机共享请求池的绝对目录 |
| `OPENCOLLAB_REQUEST_CAPACITY` | `request_capacity` | 默认未设置，正整数，与请求池目录成对设置 |

`model`、`provider`、`base_url`、`api_key`、`model_routes` 也有直接构造器参数。同一项同时出现在构造器和 `config` 中时，显式构造器值优先。`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`DASHSCOPE_API_KEY` 等后备来源及其优先级见 [端点与认证](provider-endpoint-credentials.md)。逻辑路由的字段、容器目录共享和多主机安排见 [模型路由与请求容量](model-routing-request-capacity.md)。

## 生成与上下文

| 环境变量 | SDK 配置字段 | 内置默认值 |
| --- | --- | --- |
| `OPENCOLLAB_BUDGET` | `budget` | `1000000` tokens |
| `OPENCOLLAB_MAX_OUTPUT_TOKENS` | `max_output_tokens` | 每次模型调用 `8192` tokens |
| `OPENCOLLAB_CONTEXT_WINDOW` | `context_window` | 未显式覆盖，按模型能力与运行时回退规则确定 |
| `OPENCOLLAB_TEMPERATURE` | `temperature` | `0.2`，取值范围 `0.0` 到 `2.0` |
| `OPENCOLLAB_TOP_P` | `top_p` | 未设置，采用服务端默认值，显式值范围 `0.0` 到 `1.0` |
| `OPENCOLLAB_THINKING` | `thinking` | `false` |
| `OPENCOLLAB_THINKING_PARAMS` | `thinking_params` | 默认对象 `{"enable_thinking": true}`，按 thinking 与协议条件使用 |
| `OPENCOLLAB_REASONING_EFFORT` | `reasoning_effort` | 未设置，支持 `none`、`minimal`、`low`、`medium`、`high`、`xhigh`、`max` |
| `OPENCOLLAB_LLM_STREAM_CHAT` | `llm_stream_chat` | `false`，控制 Chat Completions 流式接收 |

服务端接收的字段还取决于协议、模型能力和原生 thinking 约束，具体组合见 [深度思考与采样](thinking-reasoning-sampling.md)。`budget` 约束会话用量，`max_output_tokens` 约束一次响应，`context_window` 影响历史容量与压缩。三者的实际关系见 [预算与并发](../operations/token-step-concurrency-limits.md) 和 [上下文压缩](../operations/context-compaction.md)。

## 模型请求的超时与重试

| 环境变量 | SDK 配置字段 | 内置默认值与单位 |
| --- | --- | --- |
| `OPENCOLLAB_LLM_TIMEOUT` | `llm_timeout` | `600` 秒 |
| `OPENCOLLAB_LLM_CONNECT_TIMEOUT` | `llm_connect_timeout` | `30` 秒 |
| `OPENCOLLAB_LLM_FIRST_EVENT_TIMEOUT` | `llm_first_event_timeout` | `180` 秒 |
| `OPENCOLLAB_LLM_STREAM_IDLE_TIMEOUT` | `llm_stream_idle_timeout` | `180` 秒 |
| `OPENCOLLAB_LLM_MAX_RETRIES` | `llm_max_retries` | `3` 次重试 |
| `OPENCOLLAB_PROVIDER_ERROR_TIME_BUDGET` | `provider_error_time_budget` | `0`，启用正数时按秒限制失败尝试和重试等待 |

请求超时、整次任务期限和工具执行时限有各自的调用位置。流式请求的等待阶段、停止原因和工具清理参数见 [超时与重试诊断](../operations/timeouts-retries.md)。

## 创建客户端并核对设置

```python
from opencollab import OpenCollab

client = OpenCollab(
    ".",
    model="gpt-4o",
    config={
        "budget": 100000,
        "max_output_tokens": 8192,
        "temperature": 0.2,
        "llm_timeout": 300,
        "llm_max_retries": 2,
    },
)
for name in ("model", "budget", "max_output_tokens", "llm_timeout"):
    print(name, client.configuration[name])
```

这段代码解析本地配置并读取选定字段。调用 `agent()`、`team()` 或 `workflow()` 才开始对应的任务。SDK `config` 使用表中的小写字段名，env 文件使用大写变量名。配置值在构造客户端时确定，修改文件后通过新实例读取新设置。

## 其他设置的入口

| 要调整的内容 | 配置位置 | 详细指南 |
| --- | --- | --- |
| 选择 env 文件 | 进程变量 `OPENCOLLAB_CONFIG_FILE` | [配置读取与覆盖](configuration-precedence.md) |
| 选择团队 | CLI `--team-config`、SDK `team(config=...)`、进程变量 `OPENCOLLAB_TEAM_FILE` | [团队配置](../team/configure-team.md) |
| 角色模型、工具与提示词 | Team YAML 的 `roles` | [团队配置](../team/configure-team.md) |
| 角色额度、串行执行和并发 | Team YAML 的 `budget`，以及 SDK 运行参数 | [预算与并发](../operations/token-step-concurrency-limits.md) |
| 上下文清理策略 | Team YAML 的 `context` | [上下文压缩](../operations/context-compaction.md) |
| 工作流发现目录 | 进程变量 `OPENCOLLAB_WORKFLOWS_DIR` | [工作流运行入口](../workflow/run-registered-workflow.md) |
| 工作流输入、Agent 和集合并发 | `workflow(inputs=..., concurrency=..., task_concurrency=...)` | [Workflow API](../workflow/workflow-public-api-map.md) |
| 执行环境和隔离策略 | 客户端 `environment`、`environment_providers`，角色或工作流 `isolation` | [隔离配置](../isolation/isolation-configuration.md) |
| 任务期限、外部取消和清理 | 运行参数 `timeout`、`run_control`、`cleanup_timeout`，按入口签名使用 | [中断与清理](../operations/cancellation-cleanup.md) |
| 无限预算与显式额度 | 进程变量 `OPENCOLLAB_UNBOUNDED_LIMITS`、Workflow `limit_mode` | [预算与并发](../operations/token-step-concurrency-limits.md) |
| 历史预算提示与写入提示 | `OPENCOLLAB_BUDGET_NUDGE_MODE`、`OPENCOLLAB_WRITE_NUDGE_MODE` | [预算与并发](../operations/token-step-concurrency-limits.md) |
| 逐请求用量记录与价格 | `OPENCOLLAB_API_USAGE_LOG` 与 `OPENCOLLAB_*_USD_PER_MTOK` | [用量与费用](../observability/token-cache-cost.md) |
| 运行文件与轨迹 | SDK `artifacts`、`trace`、`run_id` | [运行记录与产物](../observability/artifacts-and-history.md) |
| 旧版显示兼容项 | `OPENCOLLAB_FILTER_MESSAGES`，默认 `false` | [默认 CLI](../setup/interactive-cli-controls.md) |

运行时控制变量中有一部分直接由进程读取，设置位置应遵循各篇指南。`filter_messages` 是旧版兼容字段，当前终端为每个 Agent 保留完整历史。`logical_model` 和 `thinking_params_explicit` 记录运行时解析状态，普通配置通过模型路由与 thinking 参数表达意图。

## 来源

[客户端配置与校验](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/config.py) · [公开客户端参数](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/client.py) · [配置指南](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md)
