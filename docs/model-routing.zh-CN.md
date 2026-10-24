# 共享模型路由与请求容量

[English](model-routing.md)

`model_routes` 将逻辑模型名映射到明确配置的后端。每条路由包含实际模型标识、服务商、端点和凭据。运行时先解析路由，再选择模型能力、推理参数、token 估算方式和上下文限制。请求及服务商用量记录保留实际模型标识。`OpenCollab.configuration` 提供逻辑名与实际模型的对应关系，以及服务商、协议、上下文和容量信息。

```python
import os
from pathlib import Path

from opencollab import OpenCollab

pool = Path(os.environ["MODEL_REQUEST_POOL"]).resolve()
pool.mkdir(parents=True, exist_ok=True)

client = OpenCollab(
    ".",
    model="worker",
    model_routes={
        "worker": {
            "model": "served-model-id",
            "provider": "openai",
            "wire_protocol": "chat_completions",
            "base_url": os.environ["MODEL_BASE_URL"],
            "api_key_env": "MODEL_API_KEY",  # pragma: allowlist secret
            "context_window": 131072,
            "request_pool": str(pool),
            "request_capacity": 2,
        },
    },
)
```

同一映射也可以通过 `config={"model_routes": ...}` 传入。CLI 和 SDK 均支持将 JSON 对象写入进程环境或[配置文件](../configs/README.md)中的 `OPENCOLLAB_MODEL_ROUTES`。SDK 构造参数优先。Team 角色的 `model` 可以选择逻辑路由，也可以填写普通模型标识。未出现在 `model_routes` 中的名称沿用已有的默认服务商配置。路由只解析一次，并使用其声明的实际模型，即使该标识同时也是另一个逻辑名称。

`provider` 默认为 `openai`，`wire_protocol` 默认为 `chat_completions`。Anthropic 原生路由使用 `provider="anthropic"`。路由凭据遵循已有的服务商和端点选择规则。`api_key_env` 指定从环境或所选配置文件中读取的密钥变量，变量缺失时会报错。路由中明确提供的 `api_key` 优先。`auth_token` 支持 Anthropic 原生 bearer 认证。每条路由持有自己的端点凭据。`OpenCollab(...)` 在构造时完成环境文件凭据解析。路由中的 `context_window` 设置作用于实际模型。采样、思考、重试、超时和输出设置继续经过现有服务商实现。Responses 请求保留响应条目、服务商身份信息和可选的 `response_session_id` 行为。

`request_pool` 和 `request_capacity` 需要成对提供。请求池使用已经存在的本地绝对路径目录，所有参与进程共享这个目录。运行时在取得请求名额前验证目录。每个名额通过文件上的操作系统锁持有，直到原生请求完成或取消清理结束。取消排队中的请求只释放该等待者自己的资源。进程结束时，操作系统会释放其文件锁。请求名额覆盖服务商重试，并在执行工具前释放，因此父任务等待子任务期间，子任务可以使用模型请求容量。

顶层 `OPENCOLLAB_REQUEST_POOL` 和 `OPENCOLLAB_REQUEST_CAPACITY`，以及对应的 SDK 配置字段，可以设置共用请求池。路由中成对提供的池配置优先。当多个路由消耗同一个后端的容量时，它们可以共享一个池。

池目录的 `capacity` 文件记录配置的容量。独立进程如果使用不同的名额数，就可能超过后端容量，因此冲突配置会报错。操作系统锁保证这些配置检查按顺序进行。参与进程运行期间，需要保留池目录及其锁文件。调整容量时，先停止这些进程，清空池目录，再使用新值启动。请求池依赖支持操作系统锁的本地文件系统。网络文件系统的锁语义，以及文件系统彼此独立的多台机器，需要由后端或网关管理请求容量。

容器需要将同一个主机目录挂载到配置中的绝对路径。目录缺失时，错误信息会提示挂载要求。各自创建的独立目录拥有独立容量，即使它们的路径字符串相同。池路径属于运行 Python 模型客户端的进程，其所在环境可能与工具执行环境不同。

多台主机共享容量时，将各条路由的 `base_url` 配置为同一个外部网关，并在网关中设置后端请求容量。网关通过已有服务商协议接收实际模型标识，负责跨主机分配名额。网关需要支持所选的 Chat Completions、Responses 或 Anthropic Messages 路径，并保留模型身份、推理参数和续接数据。HTTP 断开后仍在执行的后端工作由后端或网关的取消策略管理。本地请求池协调一台主机上的客户端请求生命周期。
