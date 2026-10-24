---
{
  "id": "configuration-diagnostics",
  "title": "检查有效配置与定位常见启动故障",
  "category": "operations",
  "summary": "查看有效配置、模型能力和错误记录，定位 CLI 参数与端点连接中的常见问题。",
  "aliases": [
    "怎么排错",
    "缺 API key",
    "模型不支持",
    "参数没生效",
    "unknown workflow",
    "unknown configuration",
    "连接测试",
    "context length exceeded",
    "exception_chain"
  ],
  "keywords": [
    "OpenCollab.configuration",
    "inspect_model_runtime",
    "profile_tool_names",
    "team show",
    "check_dashscope.py",
    "Cannot load configuration",
    "RunResult.agent_failures"
  ],
  "questions": [
    "用可核对的信息定位配置、模型能力、CLI 参数和端点连接中的常见问题？"
  ],
  "related": [
    "configuration-precedence",
    "provider-endpoint-credentials",
    "thinking-reasoning-sampling",
    "timeouts-retries",
    "trace-and-events",
    "run-results",
    "collaboration-adherence"
  ],
  "verified_ref": "e7946a42"
}
---

# 检查有效配置与定位常见启动故障

根据报错阶段选择检查入口。配置加载失败时查看生效设置和字段类型，命令解析失败时查看选项位置，找不到工作流时查看加载目录，模型请求失败时查看协议和错误分类。执行开始后的问题可结合 `RunResult.reason`、子 Agent 故障和轨迹定位。

## 先读取客户端生效设置

构造客户端会解析工作区、进程变量、env 文件和显式覆盖。下面的程序只查询本地配置，适合确认修改是否进入新客户端。

```python
from opencollab import OpenCollab

client = OpenCollab(".")
settings = client.configuration
for key in (
    "provider", "model", "wire_protocol", "context_window",
    "llm_timeout", "llm_max_retries", "provider_error_time_budget",
):
    print(key, settings.get(key))
```

`configuration` 提供经过筛选的只读元数据，凭据被排除，端点通过 `base_url_sha256` 标识。嵌套值与内部配置隔离。改了文件但值未改变时，检查进程同名变量、工作区搜索顺序，以及客户端是否仍是修改前构造的实例。

## 离线查看模型和角色能力

模型能力检查查询已安装运行时的本地表，还可以给错误样例运行现有分类器。以下脚本会显示窗口、历史阈值、错误分类和 Base 默认工具。

```python
from opencollab.models import inspect_model_runtime
from opencollab.tools import profile_tool_names

settings = inspect_model_runtime(
    "gpt-4o",
    overflow_samples=[{
        "label": "context",
        "message": "context length exceeded",
        "status": 400,
    }],
    retry_samples=[{
        "label": "concurrency",
        "class_name": "APIError",
        "message": "Concurrency limit exceeded for user",
    }],
)
print(settings["capabilities"])
print(settings["history"])
print(settings["overflow"])
print(settings["retry"])
print(profile_tool_names("base"))
```

将模型名换成自己的物理模型。能力字段描述安装版本的默认设置，显式窗口和本次 reasoning 配置仍应读取客户端。实际运行工具可从 `metrics["agent_tool_names"]` 核对。

团队角色和关系可先通过本地命令检查。工作流列表按工作区目录加载调用方模块，名字冲突会报错。

```bash
opencollab team show --team-config team.yaml
opencollab workflow list --workspace /path/to/application
opencollab workflow run duo --help
```

需要在自己的 Python 程序里读取团队声明时，使用 `opencollab.teams`。公开查询函数按团队加载规则返回角色名、声明工具、profile、角色额度和上下文策略，入口角色排在首位。配置编辑器可以用这些信息展示待运行的团队。实际运行后再从成员记录确认工具过滤与交接行为，完整示例见 [检查团队是否真的按分工协作](../observability/collaboration-adherence.md)。

## 处理常见格式与入口错误

SDK `config` 使用小写字段名，未知字段会被拒绝。团队 YAML 也按 schema 检查，字段拼写错误应直接修正。`thinking_params` 与 CLI `--args` 需要 JSON 对象，数组和残缺 JSON 会失败。预算为正整数，超时为有限正数，空凭据与空地址视为未设置。

模型与预算选项属于具体运行入口。`workflow run NAME --workspace PATH --model MODEL` 是有效位置，把这些选项放在 `workflow` 前会触发根选项位置错误。名称未知时，先用相同工作区执行 `workflow list`，再核对 `OPENCOLLAB_WORKFLOWS_DIR`。

## 显式执行一次连接检查

源码中的 `scripts/check_dashscope.py` 按常规配置执行一次真实 completion，适合已经准备好端点和凭据后确认连接。脚本名称来自最初用途，实际使用当前所选 provider。下面命令在 OpenCollab 源码根目录执行，`--workspace` 指向应读取配置的项目。

```bash
uv run python scripts/check_dashscope.py --workspace /path/to/application
```

它会输出服务返回的一段文本，并在结束时关闭客户端。该步骤会产生真实请求与用量，离线能力查询则只读取本地元数据。

## 保留故障记录

运行结束后保留 `status`、`reason`、`metrics` 与产物路径。`agent_failures` 保留已脱敏的子 provider 故障类型、HTTP 状态等信息。`exception_chain` 提供类型、模块、整数 errno 和状态码，适合定位嵌套或 OS 故障。利用同一 `run_id` 关联会话、manifest 和轨迹，再检查对应模型调用与清理记录。

## 来源

[docs/model-inspection.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/model-inspection.md#L1)　[opencollab/sdk/client.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/client.py#L156)　[scripts/check_dashscope.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/scripts/check_dashscope.py#L24)　[configs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md#L367)

## 相关指南

[配置从哪里读取以及哪一项生效](../models/configuration-precedence.md)　[接入模型端点与认证凭据](../models/provider-endpoint-credentials.md)　[配置深度思考与采样参数](../models/thinking-reasoning-sampling.md)　[超时、重试与请求卡住诊断](timeouts-retries.md)　[查看 Agent 做了什么](../observability/trace-and-events.md)　[读取运行结果与判断任务是否完成](../observability/run-results.md)
