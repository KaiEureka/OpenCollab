---
{
  "id": "run-results",
  "title": "读取运行结果与判断任务是否完成",
  "category": "observability",
  "summary": "正确处理 RunResult 和 RunError，把框架完成状态与实际任务验证分开理解。",
  "aliases": [
    "completed",
    "stopped",
    "failed",
    "raise_for_status",
    "RunError",
    "ok"
  ],
  "keywords": [
    "运行结果",
    "任务完成",
    "RunResult"
  ],
  "questions": [
    "正确处理 RunResult 和 RunError，把框架完成状态与实际任务验证分开理解？"
  ],
  "related": [
    "artifacts-and-history",
    "native-test-evidence",
    "cancellation-cleanup"
  ],
  "verified_ref": "e7946a42"
}
---

# 读取运行结果与判断任务是否完成

SDK 的 `agent`、`team` 和 `workflow` 都返回 `RunResult`。先读取 `status` 与 `reason`，再消费输出和产物，能够清楚区分任务正常结束、被限制停止以及执行失败。

## 主要字段

| 字段 | 含义 |
| --- | --- |
| `output` | 当前入口返回的文字或结构化结果 |
| `status` | `completed`、`stopped` 或 `failed` |
| `reason` | 停止或失败原因 |
| `tokens` | 已记录的 token 用量，可缺省 |
| `artifacts` | 已保存产物的目录 |
| `metrics` | 运行指标及补充恢复、清理信息 |
| `agent_failures` | 工作流中记录的子 Agent 失败 |

`ok` 在 `status == "completed"` 时为真。完成状态表示运行入口正常完成，项目功能是否正确仍需根据测试或任务验证判断。工作流可以在处理了某个 Agent 失败后返回，因此也应查看 `agent_failures`。

## 在应用中处理结果

下面片段放在已构造 `oc` 的异步函数中。

```python
result = await oc.agent("分析当前项目的测试入口", budget=50_000)
if result.ok:
    print(result.output)
else:
    print(result.status, result.reason)
    print(result.artifacts)
```

希望失败立即进入异常处理时，可调用 `result.raise_for_status()`。它对完成结果返回自身，对其它状态抛出 `RunError`。异常的 `result` 属性保留关联结果。

## 调用本身也可能抛错

无效参数可能在执行前报错。生命周期或保存故障也可能直接抛出 `RunError`。用户取消采用异步取消机制，应在应用的取消处理里等待相应清理。异常路径中的已有输出、用量和恢复信息要连同错误一起保存。

## 交付检查

对编码任务，至少确认修改落在预期目录，记录中的候选与最终交付一致，并执行相关测试。对调查任务，检查结论是否有引用材料。`completed` 与 `ok` 可以驱动应用流程，具体任务的验收条件应由应用定义。

## 源码与进一步阅读

[opencollab/sdk/result.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/result.py) · [opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md) · [docs/evaluation-runtime.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md)

[找到运行记录、会话和交付文件](artifacts-and-history.md) · [用实际测试验证 Agent 的交付](native-test-evidence.md) · [中断任务与检查资源清理](../operations/cancellation-cleanup.md)
