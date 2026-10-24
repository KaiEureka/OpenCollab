---
{
  "id": "model-routing-request-capacity",
  "title": "逻辑模型路由与共享请求容量",
  "category": "models",
  "summary": "让不同角色或进程选择明确后端，并协调同一台机器上的模型请求容量。",
  "aliases": [
    "模型别名",
    "角色用不同模型",
    "多个本地模型",
    "共享并发",
    "多进程限流",
    "请求池",
    "多机网关",
    "容量冲突",
    "容器挂载"
  ],
  "keywords": [
    "model_routes",
    "OPENCOLLAB_MODEL_ROUTES",
    "logical_model",
    "api_key_env",
    "request_pool",
    "request_capacity",
    "OPENCOLLAB_REQUEST_POOL",
    "OPENCOLLAB_REQUEST_CAPACITY"
  ],
  "questions": [
    "让不同角色或进程选择明确后端，并协调同一台机器上的模型请求容量？"
  ],
  "related": [
    "provider-endpoint-credentials",
    "configuration-precedence",
    "token-step-concurrency-limits",
    "cancellation-cleanup"
  ],
  "verified_ref": "e7946a42"
}
---

# 逻辑模型路由与共享请求容量

`model_routes` 用逻辑名称选择完整的模型后端，适合让不同角色使用不同服务，或让多个独立 Team 和 Workflow 进程共享同一后端的请求容量。每条路由声明物理模型、provider、协议、地址和认证。请求与用量记录保留物理模型名称，便于核对真正调用的服务。

## 定义一条逻辑路由

下面的代码构造名为 `worker` 的路由，并准备同机请求池目录。`MODEL_BASE_URL`、`MODEL_NAME` 和 `MODEL_API_KEY` 由调用方提前配置，示例构造客户端时完成凭据解析。

```python
import os
from pathlib import Path
from opencollab import OpenCollab

pool = Path(".opencollab/request-pool").resolve()
pool.mkdir(parents=True, exist_ok=True)
client = OpenCollab(
    ".",
    model="worker",
    model_routes={
        "worker": {
            "model": os.environ["MODEL_NAME"],
            "provider": "openai",
            "wire_protocol": "chat_completions",
            "base_url": os.environ["MODEL_BASE_URL"],
            "api_key_env": "MODEL_API_KEY",  # pragma: allowlist secret
            "context_window": 131072,
            "request_pool": str(pool),
            "request_capacity": 2,
        }
    },
)
print(client.configuration["model_routes"])
```

`context_window` 应填后端实际支持的 token 窗口，示例数值需按服务调整。`api_key_env` 从进程环境或选中的 env 文件读取指定变量，缺失会报错。显式路由 `api_key` 优先于它，原生 Anthropic 路由还可使用 `auth_token`。每条路由独立拥有端点凭据。

相同映射也可以放进 SDK 构造配置的 `model_routes` 字段，或将 JSON 对象写入 `OPENCOLLAB_MODEL_ROUTES`。构造参数优先。Team 角色的 `model` 可填写逻辑名称，未出现在映射中的名称按普通物理模型处理，使用默认 provider 配置。路由只解析一次，即使其物理名称恰好也是另一条逻辑名称，也保持当前声明。

## 限制同机后端请求

`request_pool` 与 `request_capacity` 必须成对提供。池目录必须是已存在的绝对本地目录，所有参与进程访问同一个目录，才能共享操作系统文件锁。`request_capacity=2` 允许同时占有两个模型请求槽。等待名额的调用尚未进入后端请求阶段。

请求获得名额后，在 provider 重试期间继续占用，原生请求完成或取消清理结束后释放。执行工具前会释放名额，所以父 Agent 等待子任务时可把模型容量留给子任务。进程终止时操作系统释放其锁。

顶层可使用 `OPENCOLLAB_REQUEST_POOL` 和 `OPENCOLLAB_REQUEST_CAPACITY`，或 SDK 同名小写字段。路由自己的成对设置优先。多个模型名称指向同一后端容量时，可以共享一个池。Workflow 的 `concurrency` 限制 Agent 会话数，请求池限制同时进入后端的请求，两者应分别按任务和服务能力设置。

## 改容量和使用容器

池内 `capacity` 文件记录容量，独立进程给同一池配置不同容量时会报冲突。运行期间保留池目录及锁文件。需要改容量时，先停止全部参与进程，清空池目录内容，再以新容量重新启动。

容器中的模型客户端必须把同一宿主目录挂载到配置的绝对路径。两个容器分别创建同名目录会形成独立容量。池路径属于运行 Python 模型客户端的进程，工具所在容器可能是另一处环境，排错时应先确认客户端在哪里运行。

## 多台机器共同访问服务

多机协调由公共后端或外部网关负责。让各路由的 `base_url` 指向同一网关，并在网关设置后端容量。网关需要支持所选 Chat Completions、Responses 或 Messages，保留模型身份、推理参数和续轮数据。网络断开后仍继续运行的服务端任务，由后端的取消策略管理。本地池适用于具备可靠 OS 文件锁的同机文件系统。

## 来源

[docs/model-routing.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/model-routing.md#L5)　[docs/model-routing.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/model-routing.md#L39)　[docs/model-routing.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/model-routing.md#L59)　[opencollab/bootstrap/config.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/config.py#L124)

## 相关指南

[接入模型端点与认证凭据](provider-endpoint-credentials.md)　[配置从哪里读取以及哪一项生效](configuration-precedence.md)　[设置 token 预算、步数与运行并发](../operations/token-step-concurrency-limits.md)　[中断任务与检查资源清理](../operations/cancellation-cleanup.md)
