---
{
  "id": "provider-endpoint-credentials",
  "title": "接入模型端点与认证凭据",
  "category": "models",
  "summary": "将 OpenAI 兼容或原生 Anthropic 端点接入 OpenCollab，并选对模型和认证方式。",
  "aliases": [
    "换模型",
    "第三方 API",
    "本地模型",
    "中转地址",
    "OpenAI compatible",
    "Anthropic",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "DASHSCOPE_API_KEY",
    "--model",
    "--provider",
    "--base-url"
  ],
  "keywords": [
    "OPENCOLLAB_PROVIDER",
    "OPENCOLLAB_BASE_URL",
    "OPENCOLLAB_MODEL",
    "OPENCOLLAB_API_KEY",
    "api_key",
    "auth_token",
    "base_url"
  ],
  "questions": [
    "将 OpenAI 兼容或原生 Anthropic 端点接入 OpenCollab，并选对模型和认证方式？"
  ],
  "related": [
    "configuration-precedence",
    "wire-protocol-streaming",
    "model-routing-request-capacity",
    "configuration-diagnostics"
  ],
  "verified_ref": "e7946a42"
}
---

# 接入模型端点与认证凭据

支持 OpenAI 协议的服务使用 `provider="openai"` 和兼容端点地址，原生 Anthropic Messages 服务使用 `provider="anthropic"`。模型名称必须由目标服务提供，OpenCollab 按所选 provider、协议和模型能力构造请求。先确认服务的接口协议，再填写地址、模型和认证信息。

## 配置一个端点

把以下结构写入已选择的 `configs/.env`，占位值替换为自己的服务设置。`base_url` 应采用服务文档给出的 API 基地址，物理模型名与该端点实际开放的名称一致。

```dotenv
OPENCOLLAB_PROVIDER=openai
OPENCOLLAB_BASE_URL=https://your-endpoint.example/v1
OPENCOLLAB_MODEL=your-model-id
OPENCOLLAB_API_KEY=your-api-key
```

省略 base URL 时，OpenAI 路径默认使用 [OpenAI API 基地址](https://api.openai.com/v1)，原生 Anthropic 默认使用 [Anthropic API 基地址](https://api.anthropic.com)。配置也接受所选 provider 的 `OPENAI_BASE_URL` 或 `ANTHROPIC_BASE_URL` 作为后备变量，通用 `OPENCOLLAB_BASE_URL` 优先。

原生 Anthropic 可把 provider 改成 `anthropic`，填写服务支持的 Anthropic 模型名，并按下表配置凭据。Responses 需要 OpenAI 兼容端点明确提供该 API，协议设置见相关指南。

## 选定认证来源

凭据优先顺序随端点变化。空字符串和空白值视为未设置。同一个变量存在于 shell 和文件时，shell 值优先，专用变量与通用变量之间则先比较下表的变量顺序。

| 使用路径 | 自动选择顺序 |
| --- | --- |
| 普通 OpenAI 兼容端点 | `OPENCOLLAB_API_KEY`，再 `OPENAI_API_KEY` |
| DashScope 兼容地址 | `DASHSCOPE_API_KEY`，再 `OPENCOLLAB_API_KEY` |
| 原生 Anthropic | `ANTHROPIC_API_KEY`，再 `ANTHROPIC_AUTH_TOKEN`，再 `OPENCOLLAB_API_KEY` |

原生 bearer token 放进 `Authorization` 请求头。表中的优先级跨配置来源生效，因此文件中的 provider 专用 key 可以优先于进程环境里的通用 key。切换服务后出现认证异常时，先检查旧的专用变量是否仍然存在。

显式 CLI `--api-key` 或 SDK `api_key=` 优先于自动选择，SDK `config` 中显式 `auth_token` 随后优先于环境凭据。`auth_token` 用于原生 Anthropic bearer 认证。凭据文件应保持在本地，终端诊断使用筛除秘密的有效配置。

## 在程序里连接本地兼容服务

本地服务沿用相同的 OpenAI 兼容配置。先启动服务并准备环境变量，再用以下代码构造客户端。地址、模型名和认证信息应与服务部署一致。

```python
import os
from opencollab import OpenCollab

client = OpenCollab(
    ".",
    provider="openai",
    base_url=os.environ["MODEL_BASE_URL"],
    model=os.environ["MODEL_NAME"],
    api_key=os.environ.get("MODEL_API_KEY"),
)
print(client.configuration["provider"])
print(client.configuration["model"])
```

需要多个服务时，使用 `model_routes` 为每条逻辑路由声明独立的端点和凭据。角色可选逻辑名称，实际请求仍发送该路由声明的物理模型名。请求容量也可按后端协调。

## 遇到启动和请求错误

CLI 会提示所选 provider 缺少的凭据。返回 401 时，检查实际选择的认证变量是否符合服务要求。返回模型未知或接口不存在时，检查模型名和协议路径。客户端构造会检查配置类型，离线模型检查会读取内置能力表，连接情况则通过实际请求观察。排查故障时，记录状态码、发生阶段及筛除凭据后的有效配置。

## 来源

[configs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md#L31)　[opencollab/adapters/cli/main.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/cli/main.py#L61)　[opencollab/bootstrap/config.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/config.py#L124)　[docs/model-routing.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/model-routing.md#L47)

## 相关指南

[配置从哪里读取以及哪一项生效](configuration-precedence.md)　[选择调用协议与流式响应](wire-protocol-streaming.md)　[逻辑模型路由与共享请求容量](model-routing-request-capacity.md)　[检查有效配置与定位常见启动故障](../operations/configuration-diagnostics.md)
