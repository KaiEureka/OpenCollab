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

运行记录需要按一次运行归档。会话保存模型与工具历史，轨迹描述执行事件，工作流清单和结果描述组织与交付。这些文件用途不同，结合运行标识阅读才能还原一次任务。

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

关闭普通轨迹与发生恢复故障时需要保留候选，是两个独立行为。隔离入口或候选交付失败时可能生成恢复目录，应以结果和错误中返回的真实位置为准。

## 文件之间怎样对应

先找到运行的 `run_id`，再按 Agent 或 Session 标识关联模型调用和工具回执。Team 清单描述角色和拓扑，Workflow 清单描述工作流及其执行记录。动态子 Workflow 的产物归入父运行，并保留子运行身份。

查看同一角色多次运行时，角色名称只能描述职责，具体会话标识才能区分每次实际执行。汇总费用时同样应避免重复累计一份记录的不同展示形式。

## 该保留什么

源码、候选补丁、正式测试结果与解释结论所依赖的原始记录具有恢复和复核价值。`.pytest_cache`、`.ruff_cache`、有对应源码的字节码，以及能够从锁文件重建的旧依赖环境通常属于可再生文件。

清理时先确认运行已经结束、目录没有使用句柄，并限定到明确的路径。隔离资源仍在保留状态时，先导出恢复补丁或完成必要核对，再执行清理。当前文章只负责解释文件用途，具体生命周期步骤见恢复与清理指南。

## 源码与进一步阅读

[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md) · [docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md) · [opencollab/sdk/result.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/result.py) · [opencollab/bootstrap/workflow_runtime.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/workflow_runtime.py)
