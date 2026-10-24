---
{
  "id": "base-single2",
  "title": "使用 Base 单 Agent 与 Single2 配置",
  "category": "setup",
  "summary": "直接运行一个 Agent，理解 Base 默认配置并按任务选择工具和执行环境。",
  "aliases": [
    "单智能体",
    "single agent",
    "agent2",
    "一个人做任务"
  ],
  "keywords": [
    "Base",
    "Single2",
    "单Agent"
  ],
  "questions": [
    "直接运行一个 Agent，理解 Base 默认配置并按任务选择工具和执行环境？"
  ],
  "related": [
    "python-sdk-entrypoints",
    "tool-selection",
    "isolation-overview",
    "run-results"
  ],
  "verified_ref": "e7946a42"
}
---

# 使用 Base 单 Agent 与 Single2 配置

`OpenCollab.agent()` 可以直接运行一个 Agent。当前 Base 默认配置映射到 Single2，`agent2()` 则明确选择 Single2。选择 Base 适合跟随框架默认实现，指定 `profile="single2"` 适合需要在运行记录中固定实现名称的程序。这个映射自 OpenCollab 0.8 起采用，查看旧版本时应结合对应标签的文档。

## 先完成一次只读任务

安装并配置模型后，在希望 Agent 读取的项目根目录保存下面的脚本。`OpenCollab(".")` 把该目录作为工作区，模型设置沿用该客户端构造时解析到的配置。示例的 `tools="read"` 选择 `file_read`、`grep` 和 `git_diff`，适合先理解项目结构。

```python
import asyncio
from opencollab import OpenCollab

async def main() -> None:
    client = OpenCollab(".")
    result = await client.agent(
        "阅读项目入口和测试配置，说明主要模块及建议执行的测试命令。",
        tools="read",
        budget=100_000,
        max_steps=40,
        artifacts="artifacts/first-agent",
    )
    print(result.status, result.reason)
    print(result.output)
    print(result.metrics.get("agent_profile"))

asyncio.run(main())
```

在已安装 OpenCollab 的 Python 环境运行脚本后，应看到完成状态、回答和实际 profile。`artifacts` 目录必须为新目录或空目录。第二次运行时换一个目录名，便于把结果和会话对应起来。`completed` 表示执行正常结束，任务要求的测试结果仍应从实际命令输出中判断。若任务以 `stopped` 结束，先结合 `reason` 查看是步数、token 还是期限触发，再决定后续额度。`failed` 则应查看错误和已有产物。

## 需要修改和执行命令时

默认 `tools="coding"` 提供按顺序排列的 `bash`、`file_read`、`file_write`、`apply_patch`、`git_diff` 和 `grep`。Single2 默认最多运行 200 步。Bash 每个输出流保留最多 10,000 字符，较大的工具结果在上下文视图中保留头尾，总字符限额为 16,000。

默认 Bash 要求能够隔离操作系统进程的环境，例如 Docker。使用本地环境读写文件时，文件工具仍然可用。若任务确实需要直接在宿主项目中执行命令，可显式构造工具。下面的代码放在已有 `async` 函数中，`client` 沿用前面的客户端。

```python
from opencollab.tools import builtin_tools, profile_tool_limits

tools = builtin_tools(
    "bash", "file_read", "file_write", "apply_patch", "git_diff", "grep",
    headless=False,
    limits=profile_tool_limits("base"),
)
result = await client.agent2(
    "修复目标测试，运行仓库规定的验证命令，并说明改动。",
    tools=tools,
    budget=150_000,
    max_steps=80,
)
```

`headless=False` 允许这些工具使用宿主 shell，应在预期承载命令执行的工作区中使用。进程隔离和 Git 工作树解决不同问题，环境选择见相关指南。

## 调整提示与执行额度

`system_prompt=` 会替换这次调用的默认系统提示。明确的工具对象保留自己的配置，模型、采样与请求超时由客户端设置。`budget` 限制会话累计输入与输出 token，`max_steps` 限制执行步数，单次模型输出另由 `max_output_tokens` 控制。旧参数 `steps` 是步数别名，同一次调用同时提供它和 `max_steps` 会报错。

`OPENCOLLAB_UNBOUNDED_LIMITS=true` 时，Base 和 Single2 仍遵守调用中显式给出的正数预算和步数，省略的限制采用无限制设置。查看 `result.status`、`result.reason` 与保存记录可以区分完成、额度停止和失败。需要成员自行决定交接时进入 Team，需要 Python 明确控制阶段时进入 Workflow。

## 来源

[docs/single2.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/single2.md)　[opencollab/bootstrap/agent_profiles.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/agent_profiles.py)　[opencollab/sdk/client.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/client.py)

## 相关指南

[从 Python 调用 OpenCollab](python-sdk-entrypoints.md)　[选择 Agent 能使用的工具](../extensions/tool-selection.md)　[隔离怎么选](../isolation/isolation-overview.md)　[读取运行结果与判断任务是否完成](../observability/run-results.md)
