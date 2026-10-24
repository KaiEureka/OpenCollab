---
{
  "id": "candidate-delivery",
  "title": "把候选结果采纳到交付目录",
  "category": "isolation",
  "summary": "找到 Agent 的实际改动，选择要保留的候选，并在交付目录验证结果。",
  "aliases": [
    "改动去哪了",
    "结果没有写回",
    "candidate_agent",
    "candidate_workflow",
    "CandidateRun",
    "adopt_candidate",
    "adopt_agent",
    "entry_patch"
  ],
  "keywords": [
    "候选结果",
    "采纳改动",
    "补丁交付"
  ],
  "questions": [
    "找到 Agent 的实际改动，选择要保留的候选，并在交付目录验证结果？"
  ],
  "related": [
    "worktree-workspaces",
    "workspace-recovery",
    "shell-permissions-execution",
    "adopt-team-changes",
    "compose-workflow-candidates"
  ],
  "verified_ref": "e7946a42"
}
---

# 把候选结果采纳到交付目录

候选的回答、完整文件改动和最终交付目录分别承担不同用途。Workflow 可以先在候选工作区运行，再显式采纳一个候选。Team 的入口成员可以采纳当前轮成功完成的直接 Coder 子任务。最终应在交付目录执行项目验证，确认用户实际拿到的文件。

## Workflow 先运行再采纳

`ctx.candidate_agent` 返回 `CandidateRun`，其中有 `output`、完整 `diff`、`test_records` 和 `verified_targets`，也包含生命周期错误与来源版本等信息。它适合把一次独立实现留作选择。`ctx.candidate_workflow` 可以把整个异步子工作流放进一份候选工作区。

```python
from opencollab import workflow
from opencollab.tools import builtin_tools

@workflow(name="inspect-and-adopt")
async def inspect_and_adopt(ctx, inputs):
    candidate = await ctx.candidate_agent(
        inputs["task"],
        label="implementation",
        tools=builtin_tools("file_read", "apply_patch", "grep"),
        budget=40_000,
    )
    await ctx.log(candidate.diff)
    if candidate.lifecycle_errors or candidate.output is None:
        return {"adopted": False, "output": candidate.output}
    await ctx.adopt_candidate(candidate)
    return {"adopted": True, "output": candidate.output}
```

这段示例展示交付动作。业务可以把选取条件改为实际测试记录、人工选择或多个候选的比较结果。记录中的 `verified_targets` 来自支持验证观察的工具，空集合表示没有记录到相应验证。普通回答文本的可信程度需要结合命令与产物判断。

采纳时可以传 `preserve_paths=("notes/local.txt",)` 保留指定路径。候选执行会跟踪源工作区，采纳也会检查相关来源版本。运行期间改写源目录可能触发 source tracking 错误，并保留候选位置，便于恢复。存在多个候选时，最好先完成比较，再按选定次序处理交付。

## 怎样比较候选证据

两个候选可能给出相似的回答，却修改了不同文件，也可能文件补丁相同而执行环境不同。比较时应保留候选对象，读取它的完整差异、测试观察和生命周期信息，再把选中的那个对象交给采纳接口。候选工作流默认返回的测试字段为空时，可以让业务工作流在 output 中同时返回它实际执行的检查结果。

如果候选来源已经变化，应先比较当前源文件与候选起点，再决定重跑或重新准备候选。跳过来源变化会让一次看似正确的采纳覆盖后续修改，因此运行时会保留相关错误与恢复材料。

## Team 如何写回交付目录

入口角色需要声明 `adopt_agent`。成功 Coder 的完成结果带 `aid`，例如结果显示 `Completed coder aid=3` 时，入口可调用 `adopt_agent` 并传 `{"aid": 3}`。工具只采用当前入口轮次中成功完成的直接 Coder 子任务。采用会保留源索引与独立用户修改，冲突时保持交付文件完整。

采用后再派发新的 Tester，使测试从已经更新的交付内容开始。让旧 Tester 重述之前的结果，无法观察新采纳的文件。完整流程与修订处理见 [采用队友改动并验证交付](../team/adopt-team-changes.md)。

## 显式隔离入口时怎样拿到改动

入口 Agent 自身也可以运行在工作树或容器中。它的改动会在资源回收前保存，SDK 结果通过 `result.metrics["entry_patch"]` 返回补丁位置。关闭轨迹记录仍会保存入口补丁，未指定产物目录时会创建恢复目录。调用方取得补丁后，应先阅读完整变化，再在源仓库显式应用并执行项目测试。

显示用 diff 有长度限制，完整采纳和恢复使用另行捕获的完整材料。出现捕获错误时，按 [找回隔离任务的改动并清理资源](workspace-recovery.md) 找到保留目录或容器。候选 API 见[Workflow 候选实现](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/workflow_candidates.py)，Team 交付见[调度器采纳逻辑](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/_scheduler_team.py)。
