---
{
  "id": "workflow-failure-recovery-lifecycle",
  "title": "处理 Workflow 的失败、重试与恢复",
  "category": "workflow",
  "summary": "明确各层失败如何返回或传播，在重试与继续执行前检查会话、工作区和产物状态。",
  "aliases": [
    "一步失败会怎样",
    "怎么重试工作流",
    "进程退出后会自动接着跑吗",
    "超时后代码还会继续吗",
    "能从中间步骤恢复吗",
    "保存会话等于保存进度吗"
  ],
  "keywords": [
    "Workflow 失败恢复",
    "异常传播",
    "重试",
    "生命周期"
  ],
  "questions": [
    "明确各层失败如何返回或传播，在重试与继续执行前检查会话、工作区和产物状态？"
  ],
  "related": [
    "workflow-testing-debugging",
    "workflow-agent-calls",
    "compose-workflow-candidates",
    "weave-workflow",
    "member-authored-workflow"
  ],
  "verified_ref": "e7946a42"
}
---

# 处理 Workflow 的失败、重试与恢复

处理失败时，先判断失败发生在一次会话、一个集合单元、候选交付，还是整个运行。不同层级会返回不同信息。把结果和环境状态读清楚后，再决定返回部分成果、重试某个可重复步骤，或停止并保留恢复材料。

## 先读取对应结果

| 位置 | 可观察结果 | 后续关注 |
| --- | --- | --- |
| `ctx.agent` | 文本、字典或 None | 当前步骤是否得到可用输出 |
| `ctx.agent_run` | 状态、原因、用量和清理快照 | 是否能继续使用环境 |
| `parallel` 或 `pipeline` | 普通局部错误为对应项 None | 哪个输入需要补做 |
| 候选捕获与采用 | 候选证据或抛出异常 | 源变化与保留工作区 |
| SDK 顶层 | `RunResult` 或证据持久化相关 `RunError` | 执行状态、原因、产物和异常 |

`RunResult.agent_failures` 保留内部角色的脱敏失败信息。入口决定在角色失败后返回其他数据时，外层仍可能 completed，因此要同时读取该字段。正常业务异常在执行证据完成后可形成 failed 结果，基础设施或证据保存无法完成时可能抛出 `RunError`。参数校验错误也可能在运行开始前直接抛出。

## 给可重复步骤安排有限重试

下面的完整工作流只读取文件，可按会话结果最多尝试两次。外层运行应提供合适预算，且在失败后仍留有可用额度。

```python
from opencollab.tools import builtin_tools

async def read_with_retry(ctx, args):
    attempts = []
    for index in range(2):
        run = await ctx.agent_run(
            args['goal'],
            label=f'inspect-{index}',
            tools=builtin_tools('file_read', 'grep'),
            budget=4000,
            timeout=60,
        )
        attempts.append({'status': run.status, 'reason': run.reason})
        if run.status == 'completed':
            return {'answer': run.output, 'attempts': attempts}
        if not run.workspace_ready or ctx.time_low():
            break
    return {'answer': None, 'attempts': attempts}
```

带写入或外部操作的步骤需要依据其实际副作用设计重试。例如一次文件更新已完成，后续模型响应失败，可以先读取文件和检查结果，再决定从哪里继续。普通业务步骤由 Python 决定重试。模型客户端会针对分类后的临时传输错误重试。业务重试重新执行步骤，传输重试重新发送模型请求，两者分别计数。

## 集合失败与环境撤销

集合中的普通异常转成单元 None，其他输入继续。需要错误详情时，在单元内部捕获具体异常并返回包含索引、错误类型和部分结果的字典。预算耗尽和执行环境撤销会向集合外传播，外部取消也保留传播行为。

环境撤销后，Workflow 在新工作启动前和等待会话容量后检查环境状态，排队角色会停止继续工作。已保存的会话、用量与候选恢复材料仍可用于排查。候选环境与父环境各有自己的归属，故应对照具体失败发生的环境。

## 等待清理再继续

调用 `timeout` 到达时，模型或工具工作可能仍在结束。`agent_run` 在 `cleanup_timeout` 范围内等待它启动的任务，清理未完成时返回 failed，并将共享上下文标为不可继续。`cleanup_complete` 为真意味着本次快照已稳定，`workspace_ready` 表示后续是否适合继续依赖这个环境。

外部取消以 `asyncio.CancelledError` 传播，运行时继续管理并清理已启动任务。晚到的模型用量会在清理期间计入。最终成本和退出状态应结合持久用量、轨迹以及清理指标读取。调用方传入的环境仍由调用方清理，相关 environment 指标可能保持未知，直到调用方确认清理结束。

## 保留候选和继续业务

候选改动捕获失败、源工作区变化或导出不完整时，运行时会保留环境并报告恢复位置。应先从那里取得完整文件与差异，再决定如何处理源工作区。Git 候选的原始源树和当前候选文件可帮助定位冲突与增量。

会话、清单和轨迹保存了执行证据。普通 Python 调用栈、任意库对象和外部副作用的跨进程恢复，需要应用保存已完成业务步骤、输入与结果，再从明确入口继续。Weave 通过显式状态快照提供已有继续方式，配合原工作区、相同分组定义与 `run_id` 恢复生成和检查进度。其他 Workflow 可以按自身任务设计同样清楚的业务状态。

## 源码依据

[opencollab/application/workflow_collections.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/workflow_collections.py#L245)、[docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md#L121)、[docs/weave.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/weave.md#L184)。

[opencollab/bootstrap/programmatic.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/programmatic.py)。

## 相关章节

[验证与调试自定义 Workflow](workflow-testing-debugging.md)、[在 Workflow 中调用 Agent 并处理结果](workflow-agent-calls.md)、[组合子 Workflow 并采用候选结果](compose-workflow-candidates.md)、[使用 Weave 分组执行并检查交付](weave-workflow.md)、[让成员编写并管理自己的子 Workflow](member-authored-workflow.md)。
