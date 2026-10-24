---
{
  "id": "workflow-agent-calls",
  "title": "在 Workflow 中调用 Agent 并处理结果",
  "category": "workflow",
  "summary": "为工作流中的每个角色建立会话，选择文本或结构化输出并处理预算、停止与失败。",
  "aliases": [
    "工作流角色",
    "结构化输出",
    "schema",
    "fresh session",
    "RunControl",
    "max_steps",
    "budget",
    "timeout",
    "cleanup_complete"
  ],
  "keywords": [
    "ctx.agent",
    "ctx.agent_run",
    "WorkflowAgentResult",
    "Agent 调用"
  ],
  "questions": [
    "为工作流中的每个角色建立会话，选择文本或结构化输出并处理预算、停止与失败？"
  ],
  "related": [
    "author-custom-workflow",
    "workflow-parallel-pipeline",
    "compose-workflow-candidates",
    "weave-workflow"
  ],
  "verified_ref": "e7946a42"
}
---

# 在 Workflow 中调用 Agent 并处理结果

`ctx.agent` 适合取得角色回答或结构化对象，`ctx.agent_run` 适合读取一次会话的完整执行快照。每次调用建立新会话，角色之间通过显式提示词、Python 变量和工作区文件交换信息。选择接口时先判断后续代码需要的是任务输出，还是停止原因、用量与清理状态。

## 用回答推动下一步

以下代码是完整的异步工作流函数，可传入 SDK 的 `workflow`。它使用 JSON Schema 收集判断，再按结果选择后续步骤。

```python
async def inspect_and_explain(ctx, args):
    schema = {
        'type': 'object',
        'properties': {
            'needs_detail': {'type': 'boolean'},
            'summary': {'type': 'string'},
        },
        'required': ['needs_detail', 'summary'],
        'additionalProperties': False,
    }
    finding = await ctx.agent(
        args['goal'], schema=schema, label='inspect', tools=[], budget=8000,
    )
    if finding is None:
        return {'finding': None, 'detail': None}
    detail = None
    if finding['needs_detail']:
        detail = await ctx.agent(
            f'解释这一结论的依据\n{finding["summary"]}',
            label='explain', tools=[], budget=8000,
        )
    return {'finding': finding, 'detail': detail}
```

`schema` 通过结构化捕获工具取得并校验字典。捕获未满足要求时，运行时会带着已有历史作一次纠正尝试。schema 校验返回数据的结构，字段内容和业务判断则需要结合任务依据与调用方规则验证。自由文本路径返回字符串，常见会话构建、运行失败或调用时限到达时可返回 `None`。

`prompt` 是本次任务，`label` 用于记录，`tools` 决定会话能调用的工具。使用空集合适合只处理给定文字的角色。`budget` 从共享余额申请一次调用额度，未用预留在清理后释放。额度耗尽可能在会话创建前抛出预算异常，调用方应结合顶层结果处理。

## 取得执行快照

```python
from opencollab import RunControl
from opencollab.tools import builtin_tools

async def inspect_files(ctx, args):
    run = await ctx.agent_run(
        args['goal'],
        label='inspector',
        tools=builtin_tools('file_read', 'grep'),
        budget=12000,
        timeout=120,
        max_steps=12,
        system_prompt='阅读任务指定文件，说明观察到的事实。',
        run_control=RunControl(initial_soft_budget_tokens=8000),
        cleanup_timeout=2.0,
    )
    return {
        'answer': run.output,
        'status': run.status,
        'reason': run.reason,
        'tokens': run.tokens,
        'steps': run.steps,
        'session_id': run.session_id,
        'workspace_ready': run.workspace_ready,
        'cleanup_complete': run.cleanup_complete,
    }
```

`agent_run` 的结果为 `WorkflowAgentResult`，还包含实际硬额度 `hard_budget_tokens`、软额度 `soft_budget_tokens` 及 `observation_errors`。申请值会受共享余额和其他预留影响，回调看到的是实际授权。`RunControl` 可在硬额度内决定软额度扩展，也可同步接收事件。

`system_prompt` 显式设置时替换本次角色默认系统提示词。所选 Agent profile 仍提供工具配置、上下文处理及安全行为。profile 在 SDK 的 `agent_profile` 选择，省略时保留工作流角色自身配置。`agent_run` 还接受工具配置名称或工具对象序列。要明确指定每项工具时，传入工具对象序列。

## 时限、环境与失败

`timeout` 限制本次会话运行，`max_steps` 限制会话步数，工作流外层的 `timeout` 则限制整次运行。清理时限单独控制已取消的工具与模型任务怎样结束。`agent_run` 清理未完成时返回 `failed` 和 `cleanup incomplete`，并阻止共享上下文继续启动工作。只有 `cleanup_complete` 为真时，快照中的用量才可按稳定结果理解。后续依赖同一环境的步骤还应检查 `workspace_ready`。

省略 `isolation` 继承运行策略，`False` 选择 `shared`，`True` 选择 `worktree`，字符串可选 `shared`、`worktree` 与已配置的容器或自定义策略。会话历史在每次调用时重新建立，文件是否共享由隔离策略决定。Docker 策略把工具放进容器，普通 Workflow 的 Python 编排继续在调用方进程执行。

`ctx.agent` 的 `tool_choice` 与 `thinking` 用于自由文本路径，结构化路径遵循捕获阶段的请求策略。`agent_run` 直接提供状态和回调，两种方法参数应按各自签名使用。顶层 `RunResult.agent_failures` 保留内部失败，外部取消会继续向调用方传播。交付结果通过应用真实入口、测试或验证器检查。

## 源码依据

[opencollab/workflows.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/workflows.py#L21)、[opencollab/application/workflow.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/workflow.py#L503)、[opencollab/application/workflow_agent_run.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/workflow_agent_run.py#L17)。

## 相关章节

[编写第一个自定义 Workflow](author-custom-workflow.md)、[在 Workflow 中并行处理多项任务](workflow-parallel-pipeline.md)、[组合子 Workflow 并采用候选结果](compose-workflow-candidates.md)、[使用 Weave 分组执行并检查交付](weave-workflow.md)。
