---
{
  "id": "workflow-programming-model",
  "title": "理解 Workflow 的编程模型与自由度",
  "category": "workflow",
  "summary": "明确普通 Python 代码与 ctx 操作各自承担的职责，设计由运行结果驱动的协作流程。",
  "aliases": [
    "工作流到底能写什么",
    "if 和 for 能直接用吗",
    "能写 while 循环吗",
    "可以动态决定下一步吗",
    "一定要固定流程图吗",
    "工作流是不是特殊语法"
  ],
  "keywords": [
    "Workflow 编程模型",
    "Python",
    "async def",
    "WorkflowContext"
  ],
  "questions": [
    "明确普通 Python 代码与 ctx 操作各自承担的职责，设计由运行结果驱动的协作流程？"
  ],
  "related": [
    "author-custom-workflow",
    "workflow-control-granularity",
    "workflow-dataflow-structured-io",
    "workflow-parallel-pipeline",
    "workflow-public-api-map",
    "workflow-capabilities-boundaries"
  ],
  "verified_ref": "e7946a42"
}
---

# 理解 Workflow 的编程模型与自由度

Workflow 的控制者是一个普通异步 Python 函数。函数按代码决定先做什么、根据什么继续、何时返回，Agent 处理被交给它的具体任务。`ctx` 提供建立会话和管理环境资源的方法，Python 本身提供变量、函数、条件、循环、异常处理和模块导入。编写流程时，用 Python 表达控制逻辑，用 ctx 调用会话并取得结果。

## 从普通计算到 Agent 调用

以下文件包含一个可直接传给 SDK 的异步函数。任务数由输入决定，后续解释是否执行由结果决定。

```python
async def explain_numbers(ctx, args):
    values = [int(value) for value in args['values']]
    total = sum(values)
    if total == 0:
        return {'total': 0, 'explanations': []}

    explanations = []
    for index, value in enumerate(values):
        if ctx.time_low() or ctx.tokens_remaining() < 2000:
            break
        answer = await ctx.agent(
            f'用一句话解释 {value} 在总和 {total} 中的作用。',
            label=f'item-{index}', tools=[], budget=2000,
        )
        explanations.append(answer)
    return {'total': total, 'explanations': explanations}
```

列表转换、求和、`if`、`for`、`break` 和最终字典都是 Python 行为。`ctx.agent` 创建模型会话，`ctx.tokens_remaining` 和 `ctx.time_low` 读取运行资源。输入改变时，实际调用数随之改变。同样的写法也适用于探索、修复和收集任务。

## 用结果形成反馈循环

`while` 可以根据检查结果继续推进。例如每轮让 Agent 修改文件，随后调用真实验证器，验证通过时返回，失败时把报告放入下一轮提示词。循环需要明确的结束条件，如轮数、剩余时间、额度和检查结果。固定次数的 `for` 循环适合重试或有限候选探索。

结构化输出可把 Agent 的判断变成可访问的字典字段，之后的分支仍由 Python 执行。模型返回了 `needs_fix=true` 时，只有代码读取该字段并调用修复步骤，才会真的进入修复。模型在回答中承诺运行检查时，后续还需看到工具执行与检查回执。

动态任务也可通过列表推导、输入分组和闭包创建。用 `ctx.parallel` 接收异步函数列表，用 `ctx.pipeline` 处理逐项阶段，具体依赖关系可以由自己的 Python 数据结构维护。DAG 的节点、就绪条件和失败策略由作者定义，Weave 提供了分组依赖规划这一现成实现。

## 函数与上下文的分工

```python
def normalize_goal(text):
    return ' '.join(text.split())

async def describe(ctx, args):
    goal = normalize_goal(args['goal'])
    return await ctx.agent(goal, label='describe', tools=[])

async def composite(ctx, args):
    first = await describe(ctx, args)
    if first is None:
        return {'summary': None}
    return {'summary': first, 'length': len(first)}
```

普通函数可以直接调用，异步函数用 `await` 等待。共享 `ctx` 的函数组合沿用同一环境和资源。需要完整候选环境时才选择 `candidate_workflow`，需要独立 Team 时由宿主明确调用 SDK 的 `team` 入口并管理其资源。这样的组合深度取决于具体调用路径和后端能力。

## 异步代码实际在哪里执行

`await` 在等待模型、工具或其他异步操作时让出执行机会，事件循环可继续推进其他任务。普通同步计算仍在当前进程执行。较长的阻塞网络、磁盘或 CPU 调用会占用事件循环，影响并发和取消响应，适合改用相应异步库、`asyncio.to_thread` 或由应用安排独立进程。

宿主直接运行的 Workflow，其 Python 编排位于调用方进程。配置 Docker 会话策略时，Agent 的文件和 shell 工具进入容器，普通 Python 文件操作仍在宿主解释器执行。Team 成员通过 `manage_workflow` 启动源文件时，该 Python 进程则位于成员执行环境。写文件或运行任意业务库前，应先确定代码所处的进程及路径含义。

## 声明与执行

`@workflow` 附着名称、说明和描述性阶段，便于按名称发现。运行时真正执行的是 `await fn(ctx, args)`。`phases` 元数据描述流程，`ctx.phase` 产生阶段事件，二者都由作者安排。

开发时，先把步骤写成可执行控制流，为每次会话选择工具和环境，再通过实际检查确认交付。需要更多控制时，可以逐步加入分支、循环和并行集合。

## 源码依据

[opencollab/application/workflow.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/workflow.py#L1)、[opencollab/application/workflow_registry.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/workflow_registry.py#L18)、[opencollab/bootstrap/_workflow_runtime_execution.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/_workflow_runtime_execution.py#L103)、[docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md#L251)。

## 相关章节

[编写第一个自定义 Workflow](author-custom-workflow.md)、[选择 Workflow 的入口与控制粒度](workflow-control-granularity.md)、[设计 Workflow 的输入输出与步骤间数据流](workflow-dataflow-structured-io.md)、[在 Workflow 中并行处理多项任务](workflow-parallel-pipeline.md)、[按开发任务查找 Workflow 公开 API](workflow-public-api-map.md)、[判断 Workflow 能实现的范围与运行边界](workflow-capabilities-boundaries.md)。
