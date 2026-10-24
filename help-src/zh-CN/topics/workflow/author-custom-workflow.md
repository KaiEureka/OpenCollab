---
{
  "id": "author-custom-workflow",
  "title": "编写第一个自定义 Workflow",
  "category": "workflow",
  "summary": "通过公开的 Python 接口编写一个可发现、可运行并返回结果的协作流程。",
  "aliases": [
    "写工作流",
    "工作流模板",
    "async def",
    "ctx",
    "inputs",
    "args",
    "workflows 目录",
    "phase",
    "log"
  ],
  "keywords": [
    "@workflow",
    "WorkflowContext",
    "自定义 Workflow",
    "Python 编排"
  ],
  "questions": [
    "通过公开的 Python 接口编写一个可发现、可运行并返回结果的协作流程？"
  ],
  "related": [
    "run-registered-workflow",
    "workflow-agent-calls",
    "workflow-parallel-pipeline",
    "compose-workflow-candidates",
    "workflow-programming-model",
    "workflow-dataflow-structured-io",
    "workflow-testing-debugging"
  ],
  "verified_ref": "e7946a42"
}
---

# 编写第一个自定义 Workflow

一个自定义 Workflow 可以从两个顺序步骤开始。第一个 Agent 完成修改，第二个 Agent 根据修改结果审阅并修复。Python 的 `await` 决定两次调用的先后，`ctx` 把会话连接到 OpenCollab 的环境、额度、并发和记录。变量负责把第一步的结论传给第二步。

## 写入工作区模块

把以下完整文件保存为工作区的 `workflows/implement_review.py`。示例默认使用文件工具，传入 `allow_unisolated_shell=true` 时还会配置允许宿主执行的 shell。

```python
from opencollab import workflow
from opencollab.tools import builtin_tools
from opencollab.workflows import WorkflowContext

@workflow(
    name='implement-review',
    description='实现任务后审阅改动并运行相关检查',
    phases=['implement', 'review'],
)
async def implement_review(ctx: WorkflowContext, args: dict):
    goal = args['goal']
    names = ['file_read', 'file_write', 'apply_patch', 'git_diff', 'grep']
    if args.get('allow_unisolated_shell') is True:
        names.append('bash')
    tools = builtin_tools(
        *names,
        headless=args.get('allow_unisolated_shell') is not True,
    )
    await ctx.phase('implement')
    draft = await ctx.agent(goal, label='implementer', tools=tools)
    if draft is None:
        return {'implemented': False, 'review': None}
    changes = await ctx.diff()
    await ctx.phase('review')
    review = await ctx.agent(
        f'审阅并修复任务中的遗漏，运行环境可用的相关检查。\n'
        f'任务\n{goal}\n实现说明\n{draft}\n改动\n{changes or ""}',
        label='reviewer',
        tools=tools,
    )
    await ctx.log(f'Used tokens {ctx.tokens_spent()}')
    return {'implemented': True, 'review': review}
```

`goal` 是这份文件自己定义的必填输入，缺失时普通 Python 字典访问会报错。`implemented` 表示第一步产生了返回值。第二步的检查结果和实际文件共同说明交付是否满足任务。第二次调用创建新会话，文件环境沿用当前策略，第一步的说明由提示词显式传入。

## 理解装饰器与参数

`@workflow` 给函数附上 `WorkflowSpec` 元数据，函数仍可直接调用。`name` 是发现和调用用的名称，`description` 供列表展示。省略名称时使用函数名，省略说明时先取 docstring 首行，再取函数名。`phases` 是描述性元数据，实际阶段事件由 `await ctx.phase(...)` 发出，执行顺序由函数体决定。

入口通常采用 `async def name(ctx, args)`。`args` 是字典，函数可以返回文本、字典、列表或其他适合调用方的值。跨 CLI、清单或子进程保存时，选择 JSON 可序列化的数据。校验输入、导入业务库、调用普通辅助函数都可以直接写在入口中。

## 从 CLI 发现并运行

```bash
opencollab workflow list --workspace /path/to/workspace
opencollab workflow run implement-review \
  --workspace /path/to/workspace \
  --task '修复配置读取错误并运行相关测试' \
  --args '{"allow_unisolated_shell":true}' \
  --budget 50000
```

发现器加载所选目录顶层的 `*.py` 模块。模块顶层语句会在导入时执行，适合放导入、定义与装饰器，具体任务操作放进异步函数。目录可用 `OPENCOLLAB_WORKFLOWS_DIR` 调整，工作流名称在合并注册表中保持唯一。

SDK 也可以导入 `implement_review` 后传给 `await OpenCollab(workspace).workflow(implement_review, inputs)`。普通异步函数同样可以经 SDK 启动，按名称发现则依靠装饰器元数据。

## 添加观察与工具

`builtin_tools` 返回工具对象集合，支持 `bash`、`file_read`、`file_write`、`apply_patch`、`git_diff`、`grep` 和 `submit`。自定义工具可实现公开的 `Tool` 协议后放入同一个集合。`headless=True` 要求 shell 具有进程隔离，具体权限应与实际环境对应。

`ctx.phase` 标记当前阶段，`ctx.log` 写入流程日志，`label` 帮助在会话记录中识别角色。`ctx.diff()` 返回可观察到的改动，`ctx.source_changed()` 返回改动探测结果，探测不可用时它们可返回 `None`。`tokens_remaining()`、`seconds_left()` 与 `time_low()` 可用于决定是否再开一轮。先运行并验证一项具体任务，再按需要添加分支和并行步骤。

## 源码依据

[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md#L290)、[opencollab/workflows.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/workflows.py#L14)、[opencollab/application/workflow_registry.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/workflow_registry.py#L18)。

## 相关章节

[Workflow 完整指南与运行入口](run-registered-workflow.md)、[在 Workflow 中调用 Agent 并处理结果](workflow-agent-calls.md)、[在 Workflow 中并行处理多项任务](workflow-parallel-pipeline.md)、[组合子 Workflow 并采用候选结果](compose-workflow-candidates.md)、[理解 Workflow 的编程模型与自由度](workflow-programming-model.md)、[设计 Workflow 的输入输出与步骤间数据流](workflow-dataflow-structured-io.md)、[验证与调试自定义 Workflow](workflow-testing-debugging.md)。
