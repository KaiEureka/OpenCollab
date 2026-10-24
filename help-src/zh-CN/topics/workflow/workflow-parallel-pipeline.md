---
{
  "id": "workflow-parallel-pipeline",
  "title": "在 Workflow 中并行处理多项任务",
  "category": "workflow",
  "summary": "使用 parallel 或 pipeline 组织多项工作，同时限制活跃任务与 Agent 会话数量。",
  "aliases": [
    "批量任务",
    "扇出",
    "fan out",
    "parallel",
    "pipeline",
    "顺序阶段",
    "task_concurrency",
    "--task-concurrency",
    "嵌套并行"
  ],
  "keywords": [
    "ctx.parallel",
    "ctx.pipeline",
    "并行任务",
    "concurrency"
  ],
  "questions": [
    "使用 parallel 或 pipeline 组织多项工作，同时限制活跃任务与 Agent 会话数量？"
  ],
  "related": [
    "author-custom-workflow",
    "workflow-agent-calls",
    "compose-workflow-candidates"
  ],
  "verified_ref": "e7946a42"
}
---

# 在 Workflow 中并行处理多项任务

`ctx.parallel` 适合彼此独立的异步任务，`ctx.pipeline` 适合让每个输入依次经过多个阶段。二者都按输入次序返回结果，并在整个工作流范围限制活跃集合单元。任务何时创建、各阶段调用什么 Agent、怎样汇总输出，仍由普通 Python 函数定义。

## 并行执行独立任务

以下是可直接传给 SDK 的完整异步工作流函数。`parallel` 接收可调用的异步任务函数，因此先构造 thunk，再交给运行时调度。

```python
async def summarize_many(ctx, args):
    def make_task(text, index):
        async def run_one():
            return await ctx.agent(
                f'用一句话概括\n{text}',
                label=f'summary-{index}', tools=[],
            )
        return run_one

    tasks = [make_task(text, i) for i, text in enumerate(args['texts'])]
    answers = await ctx.parallel(tasks)
    return {'answers': answers}
```

用工厂函数绑定文本与索引，可避免循环闭包在执行时都读取最后一个值。返回列表的位置对应原输入，完成较早的任务也保留自己的输入位置。空任务列表返回空列表。对共享文件同时写入时，冲突仍取决于应用操作，可以按文件分工或为候选采用独立环境。

## 逐项经过多个阶段

```python
async def prepare_then_explain(ctx, args):
    async def prepare(previous, original, index):
        return {'text': original.strip(), 'index': index}

    async def explain(previous, original, index):
        if not previous['text']:
            return None
        return await ctx.agent(
            f'解释这一段文字\n{previous["text"]}',
            label=f'explain-{index}', tools=[],
        )

    return await ctx.pipeline(args['texts'], prepare, explain, stop_on_none=True)
```

每个阶段都接收前一阶段结果、原始输入与输入索引。首阶段的 `previous` 就是原始输入。同一输入的阶段顺序执行，不同输入可同时处于不同阶段。默认 `stop_on_none=True`，某阶段返回 `None` 时结束该输入，结果位置为 `None`。设为 `False` 后，下一阶段会收到这个空值，作者需要处理它。

## 调整两种容量

SDK 调用时使用 `concurrency=4, task_concurrency=2`，表示最多四个活跃 Agent 会话，同时最多两个集合单元。CLI 对应 `--concurrency` 与 `--task-concurrency`。省略后者时，它采用前者的数值。

集合单元可能执行普通 Python、一次 Agent 或多个顺序步骤，因此任务容量与会话容量各有用途。两种计数各自管理，混合运行的活跃工作可能达到二者之和。工作流应把大量输入交给集合方法，运行时按任务容量维持有限数量的 worker。自行对大量调用使用 `asyncio.gather` 时，宿主任务创建规模由作者负责，ctx 的会话容量仍继续生效。

## 预算、局部失败与取消

有限余额会按集合大小规划每个单元的额度，内部 Agent 调用从该单元和工作流的剩余额度消费。嵌套集合继续受父级额度和共享容量约束。单元可请求更小的显式 `budget`，实际授权还取决于共享余额及其他预留。

`parallel` 的普通分支异常转成该位置的 `None`，其他分支继续。`pipeline` 普通阶段异常同样使该输入返回 `None`，`stop_on_none=False` 只影响正常返回空值的情况。共享预算耗尽和环境被撤销会向集合外传播，外部取消也继续传播，并等待已拥有任务的取消与清理。

如果需要保留普通异常详情，可在分支自身用 `try` 返回包含错误类型和输入索引的字典，让结果形成可观察的局部失败。日志、label 与保存的会话能进一步定位失败发生在哪个输入。

## 嵌套与版本核对

集合可嵌套，候选子工作流也共享集合任务容量。运行时支持子任务借用父任务容量，`task_concurrency=1` 时嵌套集合仍能继续执行。嵌套结构会影响真正的并发程度，适合用小输入、可控延时与计数器测量自己的安排。

当前 `pipeline` 的实现位于 `workflow_collections.py`，已有 SDK 嵌套集合测试。公开 `WorkflowContext` Protocol 的注解尚漏列这一方法，编辑器可能提示属性缺失。开发时应同时核对实际实现与对应 SDK 用例，调用签名为 `pipeline(items, *stages, stop_on_none=True)`。

## 源码依据

[opencollab/application/workflow_collections.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/workflow_collections.py#L137)、[tests/interfaces/test_sdk_workflow_nested_collections.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/tests/interfaces/test_sdk_workflow_nested_collections.py#L10)、[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md#L136)。

## 相关章节

[编写第一个自定义 Workflow](author-custom-workflow.md)、[在 Workflow 中调用 Agent 并处理结果](workflow-agent-calls.md)、[组合子 Workflow 并采用候选结果](compose-workflow-candidates.md)。
