---
{
  "id": "workflow-dataflow-structured-io",
  "title": "设计 Workflow 的输入输出与步骤间数据流",
  "category": "workflow",
  "summary": "清楚传递任务输入、Agent 输出、结构化数据和文件产物，形成可检查的最终结果。",
  "aliases": [
    "args 和 inputs 有什么区别",
    "上一步结果怎么交给下一步",
    "Agent 怎么返回 JSON",
    "文件和返回值怎么传",
    "怎么检查工作流最终结果"
  ],
  "keywords": [
    "Workflow 输入输出",
    "数据流",
    "schema",
    "RunResult"
  ],
  "questions": [
    "清楚传递任务输入、Agent 输出、结构化数据和文件产物，形成可检查的最终结果？"
  ],
  "related": [
    "workflow-programming-model",
    "workflow-agent-calls",
    "compose-workflow-candidates",
    "reconstruct-workflow"
  ],
  "verified_ref": "e7946a42"
}
---

# 设计 Workflow 的输入输出与步骤间数据流

Workflow 的输入、步骤结果、工作区文件和最终输出各自承担不同用途。输入告诉入口要做什么，普通 Python 变量连接步骤，文件保存真实产物，函数返回值成为 `RunResult.output`。字段与类型定义清楚后，后续步骤可以直接读取结果，调试时也容易定位数据来自哪里。

## 定义运行输入

SDK 的 `workflow(flow, inputs)` 接受字符串键 Mapping，运行时转为普通字典后调用 `flow(ctx, args)`。字段名称与业务含义由工作流作者定义。CLI 的 `--args` 提供同一个字典，`--task` 与 `--task-file` 则专门填入 `goal`。

下面是完整的异步函数，可直接传给 SDK。它先检查输入，再把结构化结果作为下一次提示词的一部分。

```python
import json

async def classify_and_describe(ctx, args):
    text = args.get('text')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('text must be non-empty text')
    schema = {
        'type': 'object',
        'properties': {
            'category': {'type': 'string', 'enum': ['question', 'statement']},
            'summary': {'type': 'string'},
        },
        'required': ['category', 'summary'],
        'additionalProperties': False,
    }
    item = await ctx.agent(
        f'归类并概括这段文字\n{text}',
        schema=schema, tools=[], label='classify',
    )
    if item is None:
        return {'item': None, 'description': None}
    description = await ctx.agent(
        '解释以下分类结果\n' + json.dumps(item, ensure_ascii=False),
        tools=[], label='describe',
    )
    return {'item': item, 'description': description}
```

入口输入可以用普通 Python 检查，也可以采用应用已有的数据模型校验。JSON Schema 参数用于一次 Agent 输出的结构捕获，工作流整体输入的校验由入口负责。字符串拼接、字典字段访问和 `json.dumps` 都是普通 Python 数据处理。

## 理解结构化捕获

`ctx.agent(schema=...)` 要求角色调用结构化输出工具，工具校验捕获对象，成功后返回字典。初次没有得到满足 schema 的对象时，运行时带上已有历史作一次纠正尝试。最终仍未满足要求时返回 `None`，因此业务代码应检查空值。

JSON Schema 可表达字段类型、必填字段、枚举及附加字段限制。它校验字段结构，字段内容是否符合任务则由提示词、工具结果及业务验证共同确认。需要从结果决定下一步时，优先使用明确字段，例如布尔值、枚举或对象列表，再用 Python 分支读取。

自由文本结果适合报告、摘要与下一步提示。将文本当作命令、路径或状态使用前，应用应依据实际用途解释它。每次 Agent 会话历史独立，下一会话需要的文字应显式传入，或让它通过工具读取指定文件。

## 文件、差异和候选

Agent 通过 `file_write`、`apply_patch` 和 `bash` 等工具操作工作区。用工具或应用检查读取实际文件，可以确认模型所述的写入操作及其内容。`ctx.diff()` 读取可观察差异，`ctx.source_changed()` 读取变化探测，探测不可用时结果为 `None`，应保留这种未知状态。

Python 入口直接用 `Path.write_text` 时，操作发生在当前编排进程。Docker 会话工具使用容器路径，两种路径的对应关系由环境决定。希望在绑定环境操作文件时，选择对应工具或环境接口，便于同时保留执行回执。

候选结果以 `CandidateRun` 表示，内部 `output` 是子会话或子函数返回值，`diff` 是捕获的文件变化。调用 `adopt_candidate` 后，选定变化才进入源工作区。需要交付活服务或整个环境时，候选后端负责与这些状态对应的交付方式。

## 最终输出与持久保存

入口 `return` 的对象成为 `RunResult.output`，而 `status`、`reason`、`tokens` 和 `agent_failures` 记录执行状况。工作流可以在局部失败后返回部分数据，因而调用方应同时读取业务字段、失败记录与实际交付检查。

开启产物保存时，输入必须能序列化为 JSON。跨 `manage_workflow` 子进程的输入与输出也采用可序列化数据，适合传路径、业务 id、数字和普通集合。活工具对象、数据库连接或函数留在所属进程，由入口通过依赖连接取得。

Reconstruct 的 `register_task` 展示了这种分工。活 oracle、执行器和工具保存在当前进程，输入只传单次句柄。CLI 则用可导入的工厂名称与 `task_args` 重建对象。跨进程恢复业务时，应保存能重新建立依赖的输入和已经完成步骤的结果。

## 源码依据

[opencollab/sdk/client.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/client.py#L408)、[opencollab/application/workflow.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/workflow.py#L503)、[opencollab/sdk/result.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/result.py#L18)、[docs/reconstruct.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/reconstruct.md#L76)。

## 相关章节

[理解 Workflow 的编程模型与自由度](workflow-programming-model.md)、[在 Workflow 中调用 Agent 并处理结果](workflow-agent-calls.md)、[组合子 Workflow 并采用候选结果](compose-workflow-candidates.md)、[使用 Reconstruct 从输入输出重建程序](reconstruct-workflow.md)。
