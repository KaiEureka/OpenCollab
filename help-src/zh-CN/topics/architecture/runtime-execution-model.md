---
{
  "id": "runtime-execution-model",
  "title": "一次任务如何从输入走到工具执行",
  "category": "architecture",
  "summary": "理解模型、Session、调度器和执行环境怎样合作，并找到实际行为由谁控制。",
  "aliases": [
    "Harness",
    "Agent loop",
    "SessionRunUseCase",
    "ToolExecutionUseCase",
    "tool_calls",
    "tool_choice",
    "ContextBuilder"
  ],
  "keywords": [
    "执行原理",
    "运行时",
    "runtime"
  ],
  "questions": [
    "理解模型、Session、调度器和执行环境怎样合作，并找到实际行为由谁控制？"
  ],
  "related": [
    "session-state-lifecycle",
    "extension-and-evaluation-boundaries",
    "shell-permissions-execution",
    "candidate-delivery"
  ],
  "verified_ref": "e7946a42"
}
---

# 一次任务如何从输入走到工具执行

一次 OpenCollab 任务会先组装配置、会话与执行环境，再把消息和工具说明发给模型。模型返回工具调用后，运行时校验参数、检查权限并执行工具，工具结果随后进入会话，供下一次模型请求使用。最终回答与实际工具执行可以通过运行记录分别观察。

## 从入口到模型请求

`OpenCollab.agent` 运行单个 Agent，`team` 建立成员和调度关系，`workflow` 运行调用方定义的 Python 编排。三种入口都围绕 Session 执行模型与工具交互。Session 持有消息、计数与阶段，environment 指向工具操作的真实工作区。

上下文由有序片段构成。角色采用 profile 时，先加入 profile 的基础提示词，再加入角色职责。团队信息、技能目录和项目概览按适用条件加入，派发任务作为启动用户内容。工具 schema 单独传给模型服务，其中描述工具名称、参数与用途。保存的完整会话与经过长度处理的请求视图承担不同职责。

## 模型怎样发起真实操作

模型可以返回普通内容，也可以返回工具调用。收到调用后，运行时先解析名称和 JSON 参数，按工具 schema 校验，再从当前角色可用工具中查找实现。执行时传入带 Agent 身份与环境的运行信息，文件工具读取对应工作区，Bash 在对应环境中启动进程。

控制工具行为有多个位置。提示词说明任务与约束，`tool_choice` 引导当前请求选择工具，配置的工具集合决定可调用能力，运行时权限检查决定这次操作是否获准，执行环境则决定文件、进程和网络的实际范围。项目希望只读分析时，可以直接只给读取工具。

```python
import asyncio
from opencollab import OpenCollab
from opencollab.tools import builtin_tools

async def main():
    result = await OpenCollab(".").agent(
        "读取 README.md，概括项目目标，并引用文件中的一段短句。",
        tools=builtin_tools("file_read"),
        budget=30_000,
        artifacts="artifacts/readme-inspection-001",
    )
    print(result.status, result.reason)
    print(result.output)
    print(result.artifacts)

asyncio.run(main())
```

示例中的产物目录需要新建或为空。运行后可以在轨迹中确认模型请求提供了 `file_read`，随后查找工具调用及实际返回片段，再对应最终回答中的引用。这样能看见模型选择、工具读取与回答形成之间的关系。项目里没有该文件时，读取结果也会为模型提供可见的错误。

## 工具结果与任务结束

工具输出与原工具调用配对保存，然后进入后续请求。派发子成员属于延迟结果，Session 会等整批关联事件填齐后继续。消息交接则会在接收方后续回合加入用户消息，具体见 [Session 如何保存进度并继续执行](session-state-lifecycle.md)。

模型给出最终回答时，Session 可以进入 `DONE`。受预算、步数或其他运行限制影响时，会进入 `STOPPED` 并保留原因。未处理故障进入 `ERROR`。SDK 再将这些过程整理为运行结果。任务是否满足业务要求，还要查看实际文件、命令结果或外部系统状态。修改交付见 [把候选结果采纳到交付目录](../isolation/candidate-delivery.md)，执行权限见 [管理终端权限并确认命令结果](../extensions/shell-permissions-execution.md)。

实现路径见[会话执行](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/session_run.py)、[工具执行](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/tool_execution.py)和[上下文设计](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/runtime-design.md)。
