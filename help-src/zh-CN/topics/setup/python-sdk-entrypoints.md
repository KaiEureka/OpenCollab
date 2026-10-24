---
{
  "id": "python-sdk-entrypoints",
  "title": "从 Python 调用 OpenCollab",
  "category": "setup",
  "summary": "在现有 Python 程序中构造客户端，调用单 agent、团队或工作流，并接入程序的结果处理。",
  "aliases": [
    "代码调用",
    "嵌入自己的程序",
    "异步调用",
    "Base",
    "Single2",
    "config=",
    "model_routes=",
    "create_model_client"
  ],
  "keywords": [
    "Python SDK",
    "OpenCollab",
    "agent",
    "agent2",
    "team",
    "workflow",
    "asyncio.run",
    "configuration"
  ],
  "questions": [
    "在现有 Python 程序中构造客户端，调用单 agent、团队或工作流，并接入程序的结果处理？"
  ],
  "related": [
    "configuration-precedence",
    "token-step-concurrency-limits",
    "cancellation-cleanup",
    "configuration-diagnostics",
    "run-results",
    "artifacts-and-history",
    "tool-selection"
  ],
  "verified_ref": "e7946a42"
}
---

# 从 Python 调用 OpenCollab

在已有 Python 程序中导入 `OpenCollab`，构造一个指向项目目录的客户端，再 `await` 调用 `agent`、`team` 或 `workflow`，就能把执行结果接入自己的程序。三个入口返回统一的 `RunResult`，模型与传输配置在客户端构造时解析一次。

## 用一个完整脚本开始

以下程序适合先检查自己的项目。它沿用配置文件和进程变量中的模型设置，使用只读工具，并明确给出执行额度与整次任务期限。

```python
import asyncio
from opencollab import OpenCollab, RunError

async def main() -> None:
    client = OpenCollab(".")
    result = await client.agent(
        "阅读项目的测试配置，给出最小验证步骤和依据文件。",
        tools="read",
        budget=100_000,
        max_steps=40,
        timeout=300,
        artifacts="artifacts/sdk-first-run",
    )
    try:
        print(result.raise_for_status().output)
    except RunError as exc:
        outcome = exc.result
        if outcome is not None:
            print(outcome.status, outcome.reason, outcome.output)
        raise

asyncio.run(main())
```

把脚本放在项目根目录，在安装了 OpenCollab 的环境中运行。工作区必须已经存在。`artifacts` 必须是新目录或空目录，下一次任务应选新的位置。Web 服务、Notebook 或已有事件循环的程序直接 `await main()`，由宿主程序管理事件循环。

## 选择执行方式

`agent()` 适合一个 Agent 连续完成任务，当前默认 Base 采用 Single2。`agent2()` 是明确选择 Single2 的便捷入口。调用时可设置 `tools`、`system_prompt`、`budget`、`max_steps` 和 `run_control`。`steps` 是旧步数别名，与 `max_steps` 同时传入会报错。

选择入口时先看任务的控制方式。只需连续阅读、修改和测试时可先用单 Agent。希望角色自行安排协作时使用 Team，已有明确阶段和程序条件时选择 Workflow。三种方式都可以在应用里检查同一种状态结构，便于统一保存日志和展示结果。

`team()` 让成员按照团队工具和关系决定交接。它的 `config=` 是团队 YAML 文件路径，与构造客户端时 `config={...}` 的模型运行设置不同。先通过 `opencollab team init team.yaml` 准备团队文件，再检查角色工具和执行环境。下面是放在已有 `async` 函数中的调用，`client` 沿用前面的实例。

```python
result = await client.team(
    "分析目标问题，安排实现与验证，并提交结果。",
    config="team.yaml",
    budget=200_000,
    timeout=900,
)
```

`workflow()` 接受已安装或工作区发现的名称，也接受工作流函数和 spec。名称查找读取工作区的 `workflows/`，相对的 `OPENCOLLAB_WORKFLOWS_DIR` 也从工作区解析。装饰过的函数和普通 async 函数可直接交给此入口。下面的完整例子用 Python 明确安排一次角色调用。

```python
import asyncio
from opencollab import OpenCollab, workflow
from opencollab.tools import builtin_tools

@workflow(name="project-summary")
async def project_summary(ctx, inputs):
    tools = builtin_tools("file_read", "grep", "git_diff")
    return await ctx.agent(inputs["task"], tools=tools, label="reader")

async def main() -> None:
    result = await OpenCollab(".").workflow(
        project_summary,
        {"task": "阅读项目入口，说明主要模块之间的关系。"},
        budget=100_000,
        concurrency=1,
    )
    print(result.status, result.output)

asyncio.run(main())
```

## 接入结果和扩展

`status` 为 `completed`、`stopped` 或 `failed`，`reason` 保留停止原因，`output` 可保留部分工作。`raise_for_status()` 对完成结果返回自身，其他状态抛出带原始结果的 `RunError`。参数错误发生在执行前，生命周期或持久化故障也可能直接抛出 `RunError`，应用应在调用边界处理。

通过 `opencollab.tools`、`opencollab.environments`、`opencollab.workflows` 和 `opencollab.profiles` 使用公开扩展入口。`client.configuration` 提供只读且已筛除秘密的有效配置。`create_model_client()` 可创建同配置的原生模型客户端，调用方负责在使用后执行 `await model.close()`。这些接口适合诊断与集成，普通任务可直接使用三个运行入口。

## 来源

[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md#L96)　[opencollab/sdk/client.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/client.py#L107)　[opencollab/sdk/client.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/client.py#L213)　[opencollab/sdk/client.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/client.py#L408)

## 相关指南

[配置从哪里读取以及哪一项生效](../models/configuration-precedence.md)　[设置 token 预算、步数与运行并发](../operations/token-step-concurrency-limits.md)　[中断任务与检查资源清理](../operations/cancellation-cleanup.md)　[检查有效配置与定位常见启动故障](../operations/configuration-diagnostics.md)　[读取运行结果与判断任务是否完成](../observability/run-results.md)　[找到运行记录、会话和交付文件](../observability/artifacts-and-history.md)　[选择 Agent 能使用的工具](../extensions/tool-selection.md)
