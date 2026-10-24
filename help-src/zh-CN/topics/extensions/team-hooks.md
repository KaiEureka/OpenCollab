---
{
  "id": "team-hooks",
  "title": "在工具执行和生命周期中加入 Hook",
  "category": "extensions",
  "summary": "在规定的运行事件上执行自定义命令，并理解命令在哪个环境运行。",
  "aliases": [
    "PreToolUse",
    "PostToolUse",
    "SubagentStop",
    "执行前检查",
    "执行后处理"
  ],
  "keywords": [
    "Hook",
    "钩子"
  ],
  "questions": [
    "在规定的运行事件上执行自定义命令，并理解命令在哪个环境运行？"
  ],
  "related": [],
  "verified_ref": "e7946a42"
}
---

# 在工具执行和生命周期中加入 Hook

Hook 在运行事件发生时执行配置的命令，用于记录事件或调用辅助脚本。配置到 `PreToolUse` 的命令在工具调用前执行，返回非零时会留下警告，工具调用继续执行。

## 配置一个观察脚本

在团队 YAML 顶层加入下面的片段。示例假定任务环境里有可执行的 `python3` 和项目脚本。

```yaml
hooks:
  PreToolUse:
    - matcher: file_write
      command: python3 scripts/observe_tool.py
      timeout: 20
```

`matcher` 选择工具名称，命令通过标准输入收到事件 JSON。可以先让脚本输出一行摘要，确认事件与执行目录，再接入自己的记录系统。

```python
import json
import os
import sys

payload = json.load(sys.stdin)
print(os.environ.get("OPENCOLLAB_HOOK_EVENT"))
print(payload.get("tool"), payload.get("aid"))
```

## 命令在哪里运行

| 事件 | 发生时机 | 常用数据 |
| --- | --- | --- |
| `PreToolUse` | 工具开始执行 | `aid`、`tool`、`args` |
| `PostToolUse` | 工具执行结束 | Agent 与工具信息及执行结果 |
| `SessionStart` | Team 成员创建 | 新成员信息 |
| `Stop` | 入口成员完成、失败或取消 | `disposition` 记录 `completed`、`failed` 或 `cancelled` |
| `SubagentStop` | 子成员完成、失败或取消 | 成员信息与终止状态 |
| `Notification` | Team 发出错误事件 | 对应错误信息 |

Hook 动作的 `type` 默认为 `command`，当前配置接受这种执行方式。`matcher` 使用工具名 glob，例如 `file_*` 同时匹配 `file_read` 和 `file_write`，省略时匹配该事件的所有调用。生命周期事件应省略 `matcher`，其 payload 没有工具名。匹配的命令按配置顺序等待完成后，再推进事件处理。

`PreToolUse` 和 `PostToolUse` 在调用 Agent 的执行环境中运行。Agent 位于工作树或容器时，Hook 也沿用该环境。脚本与解释器因此需要在相应环境可见。

生命周期 Hook 在宿主工作区运行。`SubagentStop` 发生在子任务改动提取之后，适合观察已经生成的交付信息。环境已释放或不可用时，工具 Hook 会留下跳过警告。

## 输入与超时

除标准输入外，命令还可读取 `OPENCOLLAB_HOOK_EVENT`、`OPENCOLLAB_TOOL` 和 `OPENCOLLAB_AID`。各事件提供的字段不同，脚本按对应事件读取所需字段。为命令设置合理的完成时间，长驻后台任务应由应用自己的服务生命周期管理。

`timeout` 默认为 30 秒，采用有限正数。命令末尾的后台符号 `&` 会使该动作跳过并留下警告。可先用一个真实事件验证脚本读取了标准输入、进入了预期目录并写出了需要的记录，再增加其他辅助操作。

普通命令失败和超时会留下警告。取消与进程清理错误会影响任务结束和资源释放，具体原因记录在运行日志中。

## 与权限策略分开配置

需要阻止危险操作时，配置工具授权、权限策略与隔离环境。Hook 负责观察和辅助动作，命令失败时留下警告，工具是否获准执行由权限检查决定。

## 源码与进一步阅读

[configs/team.example.yaml](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/team.example.yaml) · [configs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md) · [opencollab/adapters/hooks.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/hooks.py) · [opencollab/domain/hooks.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/domain/hooks.py)
