---
{
  "id": "session-save-resume",
  "title": "保存会话与恢复历史",
  "category": "operations",
  "summary": "找到已保存的会话，恢复终端主会话历史，并了解恢复后预算和执行状态如何延续。",
  "aliases": [
    "断点续跑",
    "继续上次对话",
    "会话 JSON",
    "旧 JSONL",
    "自动保存",
    "恢复预算",
    "workflow.json",
    "team.json"
  ],
  "keywords": [
    "/save",
    "--session",
    "-s",
    "OpenCollab.read_session_snapshot",
    ".opencollab/sessions",
    "agent_0",
    "--save",
    "--no-save"
  ],
  "questions": [
    "找到已保存的会话，恢复终端主会话历史，并了解恢复后预算和执行状态如何延续？"
  ],
  "related": [
    "interactive-cli-controls",
    "token-step-concurrency-limits",
    "cancellation-cleanup",
    "configuration-diagnostics",
    "artifacts-and-history",
    "trace-and-events"
  ],
  "verified_ref": "e7946a42"
}
---

# 保存会话与恢复历史

终端团队会自动保存成员会话，`--session PATH` 可以在下次启动时恢复主会话历史。手动 `/save` 也会导出 lead 会话并打印文件路径。恢复适合继续已记录的对话、核对之前的工具结果和部分任务，同时需要沿用正确的项目工作区与模型设置。

## 找到保存目录

团队运行目录位于 `<workspace>/.opencollab/sessions/`，名称含时间及随机后缀。成员文件采用 `agent_<aid>_<role>.json`，入口可能是 `agent_0_analyst.json`，团队 manifest 为 `team.json`。终端显示的本次目录比根据时间猜测更可靠。

工作流目录带 `wf-` 前缀，内部会话采用 `000_<role>.json` 等顺序文件名，另有 `workflow.json`。启用轨迹时还有 `orchestration.jsonl`。这些文件把一次运行中的多个会话联系起来，查看时先用 manifest 确认运行身份。

交互中输入 `/save` 会把 lead 主会话写为当前启动目录中的 `session-<随机值>.jsonl`，终端打印实际路径。切换到其他成员后执行该命令，保存对象仍为 lead。需要完整团队历史时保留自动保存目录。

## 恢复主会话后继续输入

选定自己的项目目录与入口会话文件后，在 shell 执行以下命令。`-s` 是 `--session` 的简写。

```bash
opencollab --workspace /path/to/application \
  --session /absolute/path/agent_0_analyst.json
```

也可以把手动导出的 JSONL 路径传给同一个选项。兼容入口接受旧的 JSONL 与 Agent JSON。读取不到快照及其可恢复记录时会报告文件不存在，文件结构或记录损坏则报告读取错误。

恢复入口读取的是主会话历史。团队成员与工作流各会话的保存记录适合分别检查，具体团队结构继续由当前选中的团队配置创建。恢复后先查看历史中最后的工具结果和待办，再提交一个范围明确的后续任务。

## 预算和执行阶段怎样延续

token 和步数属于会话整个生命周期，快照会恢复累计用量、结束状态及可恢复的等待事件。新一轮用户输入重置当前轮的局部执行状态，同时继续使用会话累计额度。上次已经耗尽额度时，需要结合原有总消耗决定新的限制。

正在进行的 provider 或工具协程属于原进程，恢复时从 `IDLE` 重新进入执行。重新启动前应检查原任务对应的进程和文件状态，再决定后续动作。快照中的已有工具结果、待配对结果和后续消息会按存储规则恢复。

## 用 SDK 离线读取

下面的脚本只读取保存内容，合并存储适配器认可的已完成 journal 记录。它适合先确认消息数量和会话状态，再安排恢复运行。

```python
from pathlib import Path
from opencollab import OpenCollab

path = Path("/absolute/path/agent_0_analyst.json")
snapshot = OpenCollab.read_session_snapshot(path)
print(len(snapshot["messages"]))
print(snapshot.get("session_state", {}))
print(snapshot.get("run_id"))
```

迁移或备份运行目录时，一并保留已有 sidecar 文件，读取时通过公开存储入口处理。运行身份 `run_id` 可关联会话、manifest 和轨迹，SDK 新运行也可由调用方指定该值。

## 选择工作流保存设置

工作流 CLI 默认开启 `--save` 和 `--trace`。`--no-trace` 保留会话与 manifest，停止记录编排轨迹。`--no-save` 关闭运行产物，轨迹依赖保存目录。参数放在 `workflow run NAME` 后。下面的 Duo 示例显式允许宿主工作区执行 shell。

```bash
opencollab workflow run duo --workspace /path/to/application \
  --task '完成指定任务。' --args '{"allow_unisolated_shell":true}' \
  --save --no-trace
```

需要事后复查长任务时，保留保存目录，并在每次继续前确认工作区文件与最后记录一致。

## 来源

[opencollab/adapters/cli/main.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/cli/main.py#L69)　[opencollab/adapters/cli/main.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/cli/main.py#L176)　[opencollab/bootstrap/scheduler_factory.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/scheduler_factory.py#L83)　[docs/runtime-design.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/runtime-design.md#L8)

## 相关指南

[默认终端交互与命令参数位置](../setup/interactive-cli-controls.md)　[设置 token 预算、步数与运行并发](token-step-concurrency-limits.md)　[中断任务与检查资源清理](cancellation-cleanup.md)　[检查有效配置与定位常见启动故障](configuration-diagnostics.md)　[找到运行记录、会话和交付文件](../observability/artifacts-and-history.md)　[查看 Agent 做了什么](../observability/trace-and-events.md)
