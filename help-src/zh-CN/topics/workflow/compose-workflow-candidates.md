---
{
  "id": "compose-workflow-candidates",
  "title": "组合子 Workflow 并采用候选结果",
  "category": "workflow",
  "summary": "把已有异步流程组合成更大的流程，按需要在独立候选环境中运行并采用其改动。",
  "aliases": [
    "嵌套工作流",
    "复用工作流",
    "ctx.candidate_agent",
    "候选方案",
    "候选工作区",
    "共享上下文",
    "隔离候选",
    "candidate workflow"
  ],
  "keywords": [
    "子 Workflow",
    "ctx.candidate_workflow",
    "CandidateRun",
    "ctx.adopt_candidate"
  ],
  "questions": [
    "把已有异步流程组合成更大的流程，按需要在独立候选环境中运行并采用其改动？"
  ],
  "related": [
    "author-custom-workflow",
    "team-run-workflow",
    "workflow-parallel-pipeline",
    "duo-workflow",
    "adopt-team-changes",
    "candidate-delivery",
    "workspace-recovery",
    "native-test-evidence"
  ],
  "verified_ref": "e7946a42"
}
---

# 组合子 Workflow 并采用候选结果

复用一段 Workflow 最直接的方法，是把当前 `ctx` 传给另一个异步函数。需要把整套方案放进独立环境后再比较时，可以调用 `candidate_workflow`。只需要一个候选 Agent 时，则使用 `candidate_agent`。选择时可按需要复用的范围和环境来决定。

## 共享上下文的函数组合

```python
async def summarize(ctx, args):
    return await ctx.agent(args['text'], label='summary', tools=[])

async def shared_flow(ctx, args):
    short = await summarize(ctx, {'text': args['goal']})
    if short is None:
        return {'summary': None, 'explanation': None}
    detail = await ctx.agent(
        f'解释这段摘要的含义\n{short}', label='detail', tools=[],
    )
    return {'summary': short, 'explanation': detail}
```

这两个函数可放在同一个模块，`shared_flow` 可直接传给 SDK。调用 `summarize` 就是普通 Python 函数调用，其内部会话沿用同一个上下文的环境、共享预算与容量。装饰器用于名称发现，函数组合时按可调用对象直接调用。输入和返回值由函数作者约定。

## 只运行一个候选会话

只需一次编码或检查会话时，可直接使用以下完整工作流函数。示例的 shell 默认要求进程隔离，在可信宿主工作区显式传入 `allow_unisolated_shell=true` 可采用对应权限。

```python
from opencollab.tools import evidence_tools

async def collect_single_candidate(ctx, args):
    candidate = await ctx.candidate_agent(
        args['goal'], label='single', budget=20000,
        tools=evidence_tools(
            'bash', 'file_read', 'file_write', 'apply_patch', 'git_diff',
            headless=args.get('allow_unisolated_shell') is not True,
        ),
    )
    return {
        'report': candidate.output,
        'diff': candidate.diff,
        'tests': list(candidate.test_records),
        'verified_targets': list(candidate.verified_targets),
    }
```

`evidence_tools` 在原生工具上收集可识别的测试执行记录，供候选比较使用。此函数准备候选并返回证据。要交付候选改动，在仍持有候选对象的函数内调用 `adopt_candidate`。

## 把完整子流程作为候选

下面的例子把一次修改及其报告放入候选环境，供调用方后续检查。`proposal` 可替换为包含多次 Agent 调用的流程。

```python
from opencollab.tools import builtin_tools

async def proposal(ctx, args):
    answer = await ctx.agent(
        args['goal'], label='implement',
        tools=builtin_tools('file_read', 'file_write', 'apply_patch', 'git_diff'),
    )
    return {'answer': answer}

async def prepare_candidate(ctx, args):
    candidate = await ctx.candidate_workflow(
        proposal, {'goal': args['goal']}, label='proposal', budget=20000,
    )
    await ctx.log(f'Candidate patch size {len(candidate.diff)}')
    return {
        'report': candidate.output,
        'diff': candidate.diff,
        'lifecycle_errors': list(candidate.lifecycle_errors),
    }
```

`candidate_workflow` 为完整子流程建立候选环境和子上下文，子函数里的多次会话在该环境中工作。候选与父运行共享 Agent 并发容量、集合任务容量及绝对期限，有限候选预算来自父运行的授权。默认候选子上下文的候选后端为空。子流程可以继续调用普通子函数，或为单次会话选择隔离策略。要再建立一层候选环境，需要集成方提供对应后端。

## 读取候选证据

`CandidateRun` 的 `label` 标识候选，`output` 是文本或子流程返回值，`diff` 是捕获的改动，`test_records` 与 `verified_targets` 保存可用的测试证据，`lifecycle_errors` 保存清理等生命周期问题。`source_revision` 在后端支持时记录源版本。`delivery_diff`、`delivery_paths` 和 `delivery_source_diff` 承载额外交付材料，`effective_diff` 按捕获关系选择有效差异。

单会话候选可通过证据工具收集测试记录。当前 `candidate_workflow` 返回的顶层 `test_records` 与 `verified_targets` 初始为空，子流程需要把自己的验证结果放入 `output` 或运行记录。选择候选时，结合实际测试记录、文件差异和报告判断其是否满足任务。

## 采用与恢复

做出选择后，在仍持有 `CandidateRun` 的异步函数里执行以下调用。这里的 `candidate` 是前述方法返回的对象。

```python
async def deliver_selected(ctx, candidate):
    await ctx.adopt_candidate(candidate, preserve_paths=('tests/reference.py',))
    return {'adopted': candidate.label}
```

`preserve_paths` 表达采用时需要保留的源路径，常用于集成方维护的测试文件。默认 Git 后端把候选增量应用到源工作区，保留独立用户修改与源索引。采用发生冲突时，应读取异常与恢复材料。全环境后端可接收完整候选对象，依据其身份交付文件和服务状态。

候选执行期间源工作区变化、源版本在采用前改变、改动捕获失败，都可能阻止交付。运行时会保留相关候选环境并报告恢复位置。Git 保留工作区中的 `refs/worktree/opencollab-source` 指向原始源树，可据此读取原文件。默认仓库补丁交付范围为父仓库文件，修改子模块内容会触发捕获错误并保留工作区，子模块交付应由适合的后端负责。

捕获候选后，调用采用接口交付改动，再执行任务检查。在最终返回值中记录各步结果，方便调用方查看交付进度。

## 源码依据

[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md#L280)、[opencollab/workflows.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/workflows.py#L83)、[opencollab/application/workflow_candidates.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/workflow_candidates.py#L471)、[docs/duo.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/duo.md#L193)。

[opencollab/tools.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/tools.py)。

## 相关章节

[编写第一个自定义 Workflow](author-custom-workflow.md)、[让 Team 成员调用已注册 Workflow](team-run-workflow.md)、[在 Workflow 中并行处理多项任务](workflow-parallel-pipeline.md)、[使用 Duo 生成并选择双候选方案](duo-workflow.md)、[采用队友改动并验证交付](../team/adopt-team-changes.md)、[把候选结果采纳到交付目录](../isolation/candidate-delivery.md)、[找回隔离任务的改动并清理资源](../isolation/workspace-recovery.md)、[用实际测试验证 Agent 的交付](../observability/native-test-evidence.md)。
