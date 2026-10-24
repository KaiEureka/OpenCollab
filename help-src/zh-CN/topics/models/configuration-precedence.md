---
{
  "id": "configuration-precedence",
  "title": "配置从哪里读取以及哪一项生效",
  "category": "models",
  "summary": "解释某项配置为什么生效，并稳定地让 CLI 或 SDK 读取选定配置。",
  "aliases": [
    "改了配置没生效",
    "环境变量覆盖",
    "读错 env",
    "当前目录",
    "workspace",
    "空字符串",
    "unknown configuration"
  ],
  "keywords": [
    "配置优先级",
    "OPENCOLLAB_CONFIG_FILE",
    "configs/.env",
    ".env",
    "OpenCollab.configuration",
    "config",
    "build_config"
  ],
  "questions": [
    "解释某项配置为什么生效，并稳定地让 CLI 或 SDK 读取选定配置？"
  ],
  "related": [
    "configuration-reference",
    "provider-endpoint-credentials",
    "model-routing-request-capacity",
    "configuration-diagnostics"
  ],
  "verified_ref": "e7946a42"
}
---

# 配置从哪里读取以及哪一项生效

显式 CLI 选项和 SDK 构造参数优先，随后依次使用进程环境变量、配置文件值和内置默认值。定位某项设置为什么生效时，先核对启动方式，再核对每个变量的来源。`OpenCollab(...)` 在构造时解析配置，修改环境或文件后需要重新构造客户端。

## 工作区和当前目录共同参与搜索

常规文件搜索先看工作区的 `configs/.env`，再看工作区的兼容 `.env`。当前目录与工作区不同时，继续按相同顺序检查当前目录中的两个文件。每个变量由首次出现的非空值提供，后续文件补齐尚未设置的变量，因此一次运行可能组合多个文件中的不同设置。

例如从 OpenCollab 源码目录执行 `opencollab --workspace /path/to/application` 时，项目中的模型名可以与源码目录文件中的超时配置同时生效。shell 中已经导出的同名变量又有更高优先级。若修改文件后仍看到旧模型，先检查当前 shell 是否仍设置 `OPENCOLLAB_MODEL`。

需要明确选择单一文件时，指定一个已经存在的普通 UTF-8 文件。

```bash
OPENCOLLAB_CONFIG_FILE=/absolute/path/project.env \
  opencollab --workspace /path/to/application
```

该设置替代默认文件搜索，进程变量和显式调用设置仍按各自优先级参与解析。相对文件路径按进程当前目录解释，自动化程序使用绝对路径更容易核对。

## 在 SDK 中明确覆盖

`OpenCollab` 构造参数中的 `model`、`provider`、`api_key` 和 `base_url` 会覆盖对应的 `config` 值。其余运行设置可通过 `config` 映射传入。下面的代码只构造客户端并查看元数据，使用公开占位模型名，实际运行前应选择服务端支持的名称。

```python
from opencollab import OpenCollab

client = OpenCollab(
    ".",
    model="your-model-id",
    config={
        "temperature": 0.0,
        "budget": 100_000,
        "llm_timeout": 300,
    },
)
print(dict(client.configuration))
```

零温度是有效覆盖值。SDK 的 `config` 使用字段名，例如 `llm_timeout`，env 文件使用 `OPENCOLLAB_LLM_TIMEOUT`。把环境变量名直接放进 `config` 会触发未知字段错误。构造成功说明本地配置通过校验，端点能否接受请求还取决于服务端。

## 空值、类型和认证的特殊情况

空白凭据和空白 base URL 被视为未设置，文件中的空白变量也会跳过。`budget` 必须为正整数，模型请求超时为有限正数，`temperature` 范围为 0 到 2。SDK 配置和团队 YAML 拒绝未知字段，env 文件可以包含其它应用的变量，这些变量会被忽略。

认证有额外的 provider 专用顺序。普通 OpenAI 兼容端点依次选择通用 key 和 `OPENAI_API_KEY`，DashScope 优先 `DASHSCOPE_API_KEY`，原生 Anthropic 优先自己的 key 或 bearer token。专用凭据的变量顺序跨进程和文件来源生效，例如文件中的 `DASHSCOPE_API_KEY` 优先于 shell 中导出的通用 key。显式 `api_key` 的优先级最高，完整选择规则见端点认证指南。

## 查找参数名与默认值

[配置参数与环境变量速查](configuration-reference.md) 对照列出环境变量、SDK 字段和内置默认值，并指向对应运行入口的详细指南。设置角色工具、Team 上下文或一次任务期限时，从表中的具体入口继续配置。

## 查看最终结果

`configuration` 是只读元数据，嵌套值与客户端内部配置隔离。它显示模型、provider、协议、预算、窗口、采样和传输设置，凭据被筛除，端点用 `base_url_sha256` 标识。只打印需要比较的字段，更容易判断修改是否进入新客户端。逻辑路由存在时，还能看到逻辑名称到物理模型的公开映射。

## 来源

[configs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md#L19)　[configs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md#L47)　[configs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md#L367)　[opencollab/sdk/client.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/client.py#L110)

## 相关指南

[接入模型端点与认证凭据](provider-endpoint-credentials.md)　[逻辑模型路由与共享请求容量](model-routing-request-capacity.md)　[检查有效配置与定位常见启动故障](../operations/configuration-diagnostics.md)
