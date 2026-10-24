---
{
  "id": "run-registered-workflow",
  "title": "Workflow 完整指南与运行入口",
  "category": "workflow",
  "summary": "从 CLI 或 Python SDK 选择内置与工作区工作流，传入任务并获取运行结果。",
  "aliases": [
    "运行工作流",
    "工作流在哪",
    "--task",
    "--task-file",
    "--args",
    "OPENCOLLAB_WORKFLOWS_DIR",
    "@workflow",
    "工作流重名"
  ],
  "keywords": [
    "workflow list",
    "workflow run",
    "OpenCollab.workflow",
    "工作流运行"
  ],
  "questions": [
    "Workflow 是什么，怎样启动？",
    "自己写的工作流为什么没有出现在列表里？",
    "Duo、Weave 和 Reconstruct 分别适合什么任务？"
  ],
  "related": [
    "author-custom-workflow",
    "team-run-workflow",
    "duo-workflow",
    "weave-workflow",
    "reconstruct-workflow",
    "run-results"
  ],
  "verified_ref": "e7946a42"
}
---

# Workflow 完整指南与运行入口

Workflow 把一次任务写成可执行的 Python 程序。程序决定何时调用 Agent、怎样使用返回值、哪些分支同时执行，以及何时结束。每次 Agent 调用有自己的会话历史，普通 Python 变量负责在步骤之间传递信息。需要双候选比较时可以直接运行 Duo，需要分组执行并核验交付时可以选择 Weave，已有明确控制逻辑时可以编写自己的异步函数。

## 找到工作流

先在配置好模型的环境中运行以下命令。把示例工作区替换为已有目录。

```bash
opencollab workflow list --workspace /path/to/workspace
opencollab workflow run duo --help
```

名称查找合并已安装的内置工作流与工作区的 `workflows/` 目录。发现器加载目录顶层的 Python 模块，读取 `@workflow` 附着的名称和说明。`OPENCOLLAB_WORKFLOWS_DIR` 可以改用另一个目录，相对路径仍从工作区解析。自定义名称与内置名称重复会产生注册错误，修改装饰器的 `name` 即可区分。

## 交给 CLI 一项任务

```bash
opencollab workflow run duo \
  --workspace /path/to/workspace \
  --task '修复配置读取错误并运行相关测试' \
  --args '{"allow_unisolated_shell":true}' \
  --budget 100000 --concurrency 2 --task-concurrency 2
```

示例中的 `allow_unisolated_shell` 明确允许可信本地工作区使用宿主 shell。容器环境可保持默认的 shell 隔离要求。`--task` 把文字写进工作流输入的 `goal`，`--task-file task.md` 从 UTF-8 文件读取同一个字段，文件上限为 4 MiB。两种任务入口互斥，它们也与 `--args` 已有的 `goal` 互斥。`--args` 必须是 JSON 对象。

自定义工作流可以采用 `task`、`items` 等自己的输入字段，此时在 `--args` 中传入相应字段。`--task` 只填充 `goal`，字段如何解释由所选工作流决定。模型、工作区、额度和并发选项放在 `workflow run NAME` 后面，`--isolation` 选择角色执行环境，`--agent-profile` 选择所有角色采用的 Agent 配置。

CLI 省略 `--budget` 时会把配置中的额度提高到至少 1,000,000，显式传入额度可控制本次运行规模。`concurrency` 限制活跃 Agent 会话，`task_concurrency` 限制 `parallel` 与 `pipeline` 的活跃集合单元，后者默认等于前者。顺序流程的执行顺序由 Python 代码决定，提高并发上限后顺序步骤仍逐个执行。

## 从 Python 启动

以下是完整的 SDK 调用脚本。工作区应已经存在，模型配置由 `OpenCollab` 读取。

```python
import asyncio
from opencollab import OpenCollab

async def main():
    result = await OpenCollab('/path/to/workspace').workflow(
        'duo',
        {'goal': '修复配置读取错误并验证', 'allow_unisolated_shell': True},
        budget=100000,
        concurrency=2,
        timeout=900,
        artifacts='/path/to/new-run-artifacts',
        limit_mode='explicit',
    )
    print(result.status, result.reason, result.tokens)
    print(result.agent_failures)
    print(result.raise_for_status().output)

asyncio.run(main())
```

`flow` 接受名称、普通异步函数、装饰后的异步函数，也接受带有可调用 `fn` 的 `WorkflowSpec`。`inputs` 接受字符串键的 Mapping，并转成字典传给入口。`limit_mode='explicit'` 让本次额度遵循显式设置，环境中的无限模式开关由默认的 `environment` 模式读取。产物目录应为新目录或空目录。

## 读结果与记录

SDK 返回 `RunResult`，其中 `output` 是入口函数返回的值，`status` 为 `completed`、`stopped` 或 `failed`，`reason` 解释停止原因。`raise_for_status()` 在完成时返回原结果，其他状态抛出带有该结果的 `RunError`。流程可以在某次 Agent 失败后返回已有数据，这次失败会保存在 `agent_failures` 中。读取结果时，同时查看该字段、交付文件与实际测试。

CLI 打印工作流 `output` 的 JSON，并在工作流有停止原因时返回非零退出码。默认 `--save` 保存 `.opencollab/sessions/` 下的本次运行目录，包含会话和工作流清单。默认 `--trace` 在保存目录记录 `orchestration.jsonl`，便于对应阶段、日志和步骤消耗。`--no-save` 关闭保存，`--no-trace` 可关闭编排轨迹。SDK 的 `artifacts` 和 `trace` 则控制对应持久记录。保存的执行证据适合调试，跨进程继续运行所需的业务状态由工作流作者管理。

## 源码依据

[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md#L61)、[opencollab/adapters/cli/workflow.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/cli/workflow.py#L38)。

## 相关章节

[编写第一个自定义 Workflow](author-custom-workflow.md)、[让 Team 成员调用已注册 Workflow](team-run-workflow.md)、[使用 Duo 生成并选择双候选方案](duo-workflow.md)、[使用 Weave 分组执行并检查交付](weave-workflow.md)、[使用 Reconstruct 从输入输出重建程序](reconstruct-workflow.md)、[读取运行结果与判断任务是否完成](../observability/run-results.md)。
