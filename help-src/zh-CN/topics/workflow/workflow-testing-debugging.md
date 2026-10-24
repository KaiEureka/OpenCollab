---
{
  "id": "workflow-testing-debugging",
  "title": "验证与调试自定义 Workflow",
  "category": "workflow",
  "summary": "用可观察的最小运行核对控制流、数据传递、工具执行和最终任务结果。",
  "aliases": [
    "怎么调试自己写的工作流",
    "怎么确认分支走对了",
    "如何用离线模型测试流程",
    "日志和会话保存在哪里",
    "工作流完成了为什么任务还没完成"
  ],
  "keywords": [
    "Workflow 调试",
    "Workflow 验证",
    "trace",
    "运行记录"
  ],
  "questions": [
    "用可观察的最小运行核对控制流、数据传递、工具执行和最终任务结果？"
  ],
  "related": [
    "workflow-public-api-map",
    "workflow-failure-recovery-lifecycle",
    "duo-workflow",
    "weave-workflow"
  ],
  "verified_ref": "e7946a42"
}
---

# 验证与调试自定义 Workflow

调试 Workflow 时，可以先验证普通 Python 控制流，再检查 ctx 调用与真实工具路径，最后验证任务交付。检查分支执行记录、工具回执和实际文件内容，可以定位问题来自编排、会话、环境还是应用行为。

## 先执行普通 Python

以下完整脚本用一个最小替身记录会话调用，验证分支与结果传递。它只使用 Python 标准库，可先在本地运行。

```python
import asyncio

async def flow(ctx, args):
    total = sum(args['values'])
    if total == 0:
        return {'total': total, 'answer': None}
    answer = await ctx.agent(str(total), tools=[], label='explain')
    return {'total': total, 'answer': answer}

class FakeContext:
    def __init__(self):
        self.prompts = []

    async def agent(self, prompt, **kwargs):
        self.prompts.append((prompt, kwargs['label']))
        return f'Observed {prompt}'

async def main():
    ctx = FakeContext()
    assert await flow(ctx, {'values': [0]}) == {'total': 0, 'answer': None}
    assert ctx.prompts == []
    result = await flow(ctx, {'values': [2, 3]})
    assert result == {'total': 5, 'answer': 'Observed 5'}
    assert ctx.prompts == [('5', 'explain')]
    print('CONTROL_FLOW_PASSED')

asyncio.run(main())
```

替身提供可控输出，适合验证条件、迭代上限、参数拼接和 None 处理。涉及文件和副作用的步骤，可以检查实际文件内容或由应用测试环境提供实现。观察关键行为后再增加复杂度，通常能减少定位范围。

## 再执行 SDK 路径

以下 SDK 脚本会真实建立 Workflow 上下文并执行集合，流程由普通 Python 和集合方法完成，用来验证阶段函数参数、集合输出与最终运行状态。

```python
import asyncio
from tempfile import TemporaryDirectory
from opencollab import OpenCollab

async def collection_flow(ctx, args):
    async def double(previous, original, index):
        return {'index': index, 'value': previous * 2}
    await ctx.phase('calculate')
    values = await ctx.pipeline(args['values'], double)
    await ctx.log(f'Produced {len(values)} rows')
    return values

async def main():
    with TemporaryDirectory() as workspace:
        result = await OpenCollab(workspace, model='offline').workflow(
            collection_flow, {'values': [2, 3]},
            concurrency=1, task_concurrency=1, trace=False,
        )
        assert result.status == 'completed', result.reason
        assert result.output == [{'index': 0, 'value': 4}, {'index': 1, 'value': 6}]
        print('SDK_COLLECTION_PASSED')

asyncio.run(main())
```

模型标签 `offline` 在这份脚本中用于标识测试配置，流程完全由普通 Python 和集合方法完成。验证 Agent 路径时，再接入可控的协议兼容 `llm` 替身或真实模型，并检查实际工具调用。`workflow(..., llm=...)` 提供模型注入入口，仓库中的 SDK 测试可作为已有用例。

## 观察阶段、会话与文件

用 `phase` 标记业务阶段，`label` 标识角色，用 `log` 写输入索引、输出规模和下一步决策。共享阶段名会关联随后开始的会话，并行任务还应依靠 label 区分。日志内容应让读者知道发生了哪项行动。

CLI 默认保存会话和 `orchestration.jsonl`，SDK 用 `artifacts` 指定新目录或空目录，并通过 `trace` 选择轨迹。工作流清单中的运行 id 对应 `RunResult.metrics['run_id']`，可关联会话与编排记录。`trace=False` 时仍可保留清单和原生会话证据。

工具路径验证应检查真正的文件或工具输出。让角色写入一个已知内容的文件，再从绑定环境读取。让角色运行任务原生检查，再读取退出状态和测试记录。Docker 工具使用容器工作区，普通 Workflow Python 留在调用方进程，验证路径前先确认执行位置。

## 验证失败与最终交付

有额度、时限和写入行为的流程应实际触发相关失败，观察部分文件是否保留、结果是否包含对应停止原因，以及清理是否完成。`agent_run` 的 `cleanup_complete` 和 `workspace_ready` 帮助判断能否继续，`RunResult.agent_failures` 保留内部失败，候选错误中的恢复位置帮助保全未交付工作。

最终检查从任务要求的入口进入。库修改调用公开函数，命令任务运行真实命令并比较输出，数据任务读取实际产物，服务任务检查接口行为。Duo 的采用结果说明选定变化已经交付，Weave 的 `delivery_ok` 依赖实际执行检查。从运行状态查看流程是否完成，从工具回执查看操作是否执行，再从实际检查结果确认任务是否通过验证。

## 源码依据

[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md#L91)、[opencollab/sdk/result.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/result.py#L18)、[opencollab/application/workflow.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/workflow.py#L734)、[tests/interfaces/test_sdk_workflow_nested_collections.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/tests/interfaces/test_sdk_workflow_nested_collections.py#L10)。

## 相关章节

[按开发任务查找 Workflow 公开 API](workflow-public-api-map.md)、[处理 Workflow 的失败、重试与恢复](workflow-failure-recovery-lifecycle.md)、[使用 Duo 生成并选择双候选方案](duo-workflow.md)、[使用 Weave 分组执行并检查交付](weave-workflow.md)。
