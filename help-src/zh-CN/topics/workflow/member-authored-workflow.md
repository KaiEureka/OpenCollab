---
{
  "id": "member-authored-workflow",
  "title": "让成员编写并管理自己的子 Workflow",
  "category": "workflow",
  "summary": "让 Team 成员写出 Python 文件并启动子运行，再检查进展、等待结果或停止运行。",
  "aliases": [
    "动态工作单元",
    "动态 DW",
    "自己写自己跑",
    "launch",
    "status",
    "wait",
    "result",
    "stop",
    "member-authored Workflow"
  ],
  "keywords": [
    "manage_workflow",
    "成员编写工作流",
    "子运行",
    "运行时工作流"
  ],
  "questions": [
    "Agent 能自己写一个工作流再运行吗？",
    "启动后怎样查询、等待和停止子 Workflow？",
    "父 Agent 结束后子 Workflow 会怎样？"
  ],
  "related": [
    "team-run-workflow",
    "author-custom-workflow",
    "workflow-agent-calls",
    "wait-for-team-results"
  ],
  "verified_ref": "e7946a42"
}
---

# 让成员编写并管理自己的子 Workflow

`manage_workflow` 适合让成员在任务进行中写出 Python 编排程序，并拥有这次子运行的查询、等待和停止能力。`launch` 启动后立即返回 child id，成员可以继续组织任务，再按 id 获取结果。源文件在成员执行环境中的独立 Python 进程内导入，入口接收普通的 `ctx` 和 `args`。

## 准备角色与执行环境

```yaml
entry: author
roles:
  author:
    prompt: 按任务需要编写 Python Workflow，启动并读取结果。
    tools: [file_write, file_read, bash, manage_workflow]
```

目标环境需要提供安装了 OpenCollab 的 Python。默认解释器为 `python3`，可用工具参数 `python` 指定环境里的解释器路径。该工具执行源代码时使用成员的 shell 权限，宿主执行需要 Team 调用方显式允许 `allow_unisolated_shell`。容器成员则在自己的容器中执行源文件。

成员先用编辑工具写入工作区内的 `generated.py`。以下是完整的子工作流文件，普通循环先计算输入，再创建一个 Agent 会话。

```python
from opencollab.tools import builtin_tools

async def main(ctx, args):
    count = int(args.get('count', 6))
    if count < 0:
        raise ValueError('count must be nonnegative')
    total = sum(range(count))
    answer = await ctx.agent(
        f'用一句中文解释数字 {total}',
        label='explain',
        tools=builtin_tools('file_read'),
    )
    return {'total': total, 'answer': answer}
```

此文件直接按 `entry` 加载，入口默认是 `main`。装饰器主要用于名称发现，源文件启动可直接指定异步函数。子会话请求的工具名称应在父成员可用工具中，输出上限、shell 隔离要求与文件创建限制也随父成员传入。

## 启动与观察

成员用以下参数启动文件。

```json
{
  "action": "launch",
  "source": "generated.py",
  "entry": "main",
  "args": {"count": 6},
  "python": "python3",
  "budget": 10000,
  "concurrency": 2
}
```

`source` 指向成员工作区内的 Python 文件。`args` 必须能跨子进程序列化，适合传入字符串、数字、列表和字典。模型凭据与有效生成设置经子进程标准输入继承，host 继续处理模型后端及环境请求。

后续操作都使用返回的 id，下面展示一次等待请求。

```json
{"action": "wait", "id": "<child id>", "timeout": 30}
```

| `action` | 用法 | 返回行为 |
| --- | --- | --- |
| `launch` | 提供 `source`，可带入口与输入 | 立即返回 id 与当前状态 |
| `status` | 提供 id | 观察状态、已用 tokens 与 artifacts |
| `wait` | 提供 id，可带正数 timeout | 等待完成或本次等待超时 |
| `result` | 提供 id | 读取保留的结果 |
| `stop` | 提供 id | 停止子运行并等待清理 |

`wait` 的超时只结束本次等待，子运行继续执行。返回记录中的 `result` 可能仍为空，成员应结合 `status` 决定下一步。工具按调用成员保存 child id，创建者负责管理自己启动的运行。

## 额度与生命期

有限额度会在活跃子运行之间预留，也影响父成员后续模型请求。省略子预算时，子运行取得尚未预留剩余额度的一半，让父成员留有协调空间。显式 `budget` 会限制在可用余量内。无限额度父运行可启动无限额度子运行。用量汇总到父成员时按实际消耗计入一次。

子运行持续到入口返回、执行失败或父成员停止。父成员正常完成时，也会先停止仍活跃的子运行，再保存最终父快照。取消首先请求子运行停止会话并保存状态，迟迟未结束时由拥有它的 Environment 取消命令并清理进程。普通 `bash` 命令超时与这套管理生命周期分别生效。

## 查错误和持久记录

环境内的 `.opencollab/child-runs/<id>` 保存活跃控制文件。父运行设置产物目录后，会收集子运行的 Workflow 清单、会话、结果和命令输出，完成后 `artifacts` 指向 host 的持久目录。父运行关闭会清理环境里的控制文件，再捕获成员改动。

源文件导入错误、执行错误和输出序列化错误会进入结果。子工作流决定在会话失败后返回数据时，`agent_failures` 仍保留失败。调试应先读取结果和进程输出，再对照入口与父成员工具授权。每次 Agent 调用也可选择 `shared`、`worktree` 或已配置的 Docker 策略，子环境的分配与释放由 host 管理。

## 源码依据

[examples/member-authored-workflow/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/examples/member-authored-workflow/README.md#L3)、[opencollab/bootstrap/authored_workflow.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/authored_workflow.py#L52)。

## 相关章节

[让 Team 成员调用已注册 Workflow](team-run-workflow.md)、[编写第一个自定义 Workflow](author-custom-workflow.md)、[在 Workflow 中调用 Agent 并处理结果](workflow-agent-calls.md)、[等待队友结果并读懂团队状态](../team/wait-for-team-results.md)。
