---
{
  "id": "artifacts-and-history",
  "title": "找到运行记录、会话和交付文件",
  "category": "observability",
  "summary": "运行结束后定位对应目录，区分产物、原始会话、轨迹和可再生缓存。",
  "aliases": [
    "artifacts",
    "会话文件",
    "日志目录",
    "workflow.json",
    "team.json",
    "no-save",
    "no-trace"
  ],
  "keywords": [
    "运行文件",
    "产物目录",
    "记录在哪里"
  ],
  "questions": [
    "运行结束后定位对应目录，区分产物、原始会话、轨迹和可再生缓存？"
  ],
  "related": [],
  "verified_ref": "e7946a42"
}
---

# 找到运行记录、会话和交付文件

按运行标识查找记录，可以还原一次任务的执行过程。会话保存模型与工具历史，轨迹记录执行事件，工作流清单和结果记录成员组织与交付。

## 指定产物目录

SDK 的 `artifacts=` 接受用于当前运行的新目录或空目录。建议应用根据任务标识分配独立目录，避免两个运行共用结果文件。运行结束后，从 `RunResult.artifacts` 获取实际位置。

```python
result = await oc.agent(
    "阅读项目并给出修改建议",
    artifacts="artifacts/review-parser",
)
print(result.artifacts)
```

示例放在异步函数中。重复使用目录前先处理已有内容，保留正式结果与待恢复材料。

## 保存选项的区别

Workflow 默认保存会话并记录编排过程。CLI 的 `--no-trace` 保留会话与工作流清单，`--no-save` 关闭通常的运行产物保存。交互 CLI 的 `--trace` 开启轨迹，`/save` 保存主会话。

隔离入口或候选交付失败时，恢复机制会按需要单独保留候选，与普通轨迹的开关分别处理。恢复目录的位置以结果和错误中返回的实际路径为准。

## 文件之间怎样对应

| 入口 | 常见保存文件 | 读取用途 |
| --- | --- | --- |
| SDK Agent | `agent.json`，启用轨迹时的 `trajectory.jsonl` | 单次会话快照与执行事件 |
| Team | `team.json`、`agent_<aid>_<role>.json`，SDK 启用轨迹时的 `trajectory.jsonl` | 实际成员名册、逐成员会话与协作事件 |
| Workflow | `workflow.json`、`<seq>_<role>.json`，启用轨迹时的 `orchestration.jsonl` | 入口与参数、逐步骤会话与编排事件 |

角色或步骤标签会转换为适合文件名的形式。完整会话可能另有持久化日志，读取时可用公开的 `OpenCollab.read_session_snapshot(path)` 合并快照和已完成日志条目，示例见 [Session 如何保存进度并继续执行](../architecture/session-state-lifecycle.md)。

先找到运行的 `run_id`，再按 Agent 或 Session 标识关联模型调用和工具回执。Team 清单保留所选文件与实际成员，完整通信拓扑从对应 YAML 读取。Workflow 清单描述工作流及其执行记录。动态子 Workflow 的产物归入父运行，并保留子运行身份。

`workflow.json` 还保存运行状态、原因、已用 token、预算和 `evidence_complete`，并通过 `trace_enabled`、`tracer_write_error`、`tracer_dropped_steps` 与 `tracer_failure` 描述轨迹保存情况。遇到记录缺失时，先读取这些字段，再检查对应会话与原始工具结果。`evidence_complete` 描述运行记录与清理的完成情况。验收项目时，再读取实际测试或验证器结果。

查看同一角色的多次运行时，用会话标识区分每次执行，角色名称用于说明职责。汇总费用时同样应避免重复累计一份记录的不同展示形式。

## 该保留什么

源码、候选补丁、正式测试结果与解释结论所依赖的原始记录具有恢复和复核价值。`.pytest_cache`、`.ruff_cache`、有对应源码的字节码，以及能够从锁文件重建的旧依赖环境通常属于可再生文件。

清理时先确认运行已经结束、目录没有使用句柄，并限定到明确的路径。隔离资源仍在保留状态时，先导出恢复补丁或完成必要核对，再执行清理。具体生命周期步骤见恢复与清理指南。

## 源码与进一步阅读

[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md) · [docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md) · [opencollab/sdk/result.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/result.py) · [opencollab/bootstrap/workflow_runtime.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/workflow_runtime.py)
