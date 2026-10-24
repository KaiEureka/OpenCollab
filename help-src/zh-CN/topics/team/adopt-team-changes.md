---
{
  "id": "adopt-team-changes",
  "title": "采用队友改动并验证交付",
  "category": "team",
  "summary": "将已完成 Coder 的改动交付到团队工作区，再让新的 Tester 验证采用后的结果。",
  "aliases": [
    "合入子 agent 改动",
    "应用补丁",
    "队友代码在哪",
    "worktree",
    "增量采用",
    "冲突恢复",
    "Tester"
  ],
  "keywords": [
    "adopt_agent",
    "采用改动",
    "团队交付",
    "Coder"
  ],
  "questions": [
    "将已完成 Coder 的改动交付到团队工作区，再让新的 Tester 验证采用后的结果？"
  ],
  "related": [
    "dispatch-team-agents",
    "wait-for-team-results",
    "compose-workflow-candidates"
  ],
  "verified_ref": "e7946a42"
}
---

# 采用队友改动并验证交付

入口成员可以用 `adopt_agent` 把当前轮成功完成的直接 Coder 子任务改动写入团队交付目录。采用后应创建新的 Tester，让它验证用户最终会得到的文件。独立工作树中的成功回答与源目录更新之间，由这个显式动作连接。

## 从成功完成结果取得 aid

入口角色的 `tools` 要包含 `adopt_agent`。Coder 成功结束后，完成结果会附带可采用的 aid。例如看到 `Completed coder aid=3`，入口可以发出下列工具调用。

```json
{"aid": 3}
```

工具只接收 `aid`，值为正整数。运行时会确认调用者是拥有该能力的入口成员、对象是它的直接 coder 子成员，并且对象已经在当前入口轮次成功完成。成员角色名按规范化形式检查。失败、停止、其他角色或先前轮次的结果需要先处理其状态，才能进入对应的交付过程。

## 独立实现怎样进入交付目录

声明采纳工具的 Team 会从当前源文件内容准备子任务，包括用户尚未提交的修改和已采纳的实现。Coder 在自己的环境中修改，完成后捕获整份变化。采用时保留源仓库索引，将独立增量合入交付目录。

同一文件发生冲突时，采纳操作会拒绝应用并保持交付文件完整。错误会指出完整候选材料的恢复位置。应先导出补丁，比较源目录与候选的实际差异，再安排冲突处理。让新任务基于已更新交付目录继续修改，通常比反复应用同一冲突补丁更容易理解。

共享环境中，Coder 已经直接修改交付目录，工具会说明这些变化已经存在。独立环境中，工具成功返回采用结果后，才能在交付目录观察到相应修改。入口自身也被显式隔离时，最终还需要取出入口补丁，具体见 [把候选结果采纳到交付目录](../isolation/candidate-delivery.md)。

## 修订和重复采用

在同一个活动入口轮次内，同一 Coder 后续完成新修订时，运行时会计算它相对上次成功采用结果的增量。再次采用只应用新增变化。重复采用完全相同的完成结果，会返回 already adopted，并保持交付文件状态。

如果采用失败，完整候选补丁会保存在当前运行产物中。未启用产物目录时，会使用私有临时恢复目录。应在释放运行资源前保存这些材料，恢复步骤见 [找回隔离任务的改动并清理资源](../isolation/workspace-recovery.md)。

## 让 Tester 检查已经采用的内容

采用成功后，入口可以派发 `{"role": "tester", "task": "验证刚采用的 parser 修复，执行相关回归测试并报告命令、退出码和失败信息。", "context": "检查当前交付目录中的空输入处理。"}`。新 Tester 从已经更新的交付内容准备环境，测试结果应对应这次实际交付。

最终报告应给出改变了哪些文件、采用是否成功以及执行了哪些项目验证。测试命令的退出码和目标执行情况共同说明验证结果，完整回答可关联本次运行的产物记录。

工具参数见[采纳工具](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/tools/adopt_agent.py)，当前轮次与增量处理见[调度器实现](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/_scheduler_team.py)，团队整体配置见 [Team 完整指南与团队配置](configure-team.md)。
