---
{
  "id": "member-authored-workflow",
  "title": "让成员自己写并运行子 Workflow",
  "category": "workflow",
  "summary": "Team 成员用 manage_workflow 启动自己写的 Python 文件作为子运行，之后按 id 查询、等待、取结果或停止。",
  "aliases": [
    "动态工作单元",
    "动态 DW",
    "自己写自己跑",
    "launch",
    "status",
    "wait",
    "result",
    "stop",
    "member-authored Workflow",
    "让成员编写并管理自己的子 Workflow"
  ],
  "keywords": [
    "manage_workflow",
    "成员编写工作流",
    "子运行",
    "运行时工作流",
    "child id",
    "child-runs"
  ],
  "questions": [
    "Agent 能自己写一个工作流再运行吗？",
    "启动后怎样查询、等待和停止子 Workflow？",
    "父 Agent 结束后子 Workflow 会怎样？",
    "子 Workflow 的预算默认是多少？"
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

# 让成员自己写并运行子 Workflow

`manage_workflow` 是 OC 给 Team 成员的另一个工具：成员先用文件工具写出一个 Python Workflow 文件，再用 `launch` 启动它。`launch` 立即返回 child id，不阻塞；成员可以继续干别的，之后用 `status`、`wait`、`result`、`stop` 按 id 管理这个子运行。

它和 `run_workflow` 的区别：`run_workflow` 只能跑已注册的名称，并且同步等待结束；`manage_workflow` 跑成员现写的代码，异步管理。源文件在成员执行环境里的一个独立 Python 进程中导入，入口函数接收普通的 `ctx` 和 `args`。

## 最小用法

### 1. 给角色配工具

```yaml
entry: author
roles:
  author:
    prompt: 按任务需要编写 Python Workflow，启动并读取结果。
    tools: [file_write, file_read, bash, manage_workflow]
```

环境要求：

- 目标环境里要有装了 OpenCollab 的 Python。默认解释器是 `python3`，可以用工具参数 `python` 指定别的路径。
- 工具用成员的 shell 权限执行源代码。在宿主机上执行时，Team 调用方要显式允许 `allow_unisolated_shell`。
- 容器成员在自己的容器里执行源文件。

### 2. 成员写出 Workflow 文件

成员用编辑工具在工作区写入 `generated.py`。下面是一个完整的子工作流文件：先算输入的总和，再开一个 Agent 会话。

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
        tools=builtin_tools('file_read'),  # 必须是父成员可用的工具
    )
    return {'total': total, 'answer': answer}
```

- 文件按 `entry` 加载，默认入口是 `main`。不需要装饰器，装饰器只用于按名称发现。
- 子会话请求的工具名必须在父成员的可用工具里。
- 输出上限、shell 隔离要求和文件创建限制，都从父成员继承。

### 3. 启动

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

- `source`：成员工作区里的 Python 文件。
- `args`：要能跨子进程序列化，用字符串、数字、列表和字典即可。
- 模型凭据和有效的生成设置通过子进程标准输入传过去。模型后端和环境请求仍由宿主处理。

### 4. 等待结果

之后所有操作都用返回的 id，例如：

```json
{"action": "wait", "id": "<child id>", "timeout": 30}
```

## 五种操作

| `action` | 需要的参数 | 返回什么 |
| --- | --- | --- |
| `launch` | `source`，可加入口和输入 | 马上返回 id 和当前状态 |
| `status` | id | 状态、已用 tokens 和 artifacts |
| `wait` | id，可加正数 timeout | 等到完成，或本次等待超时 |
| `result` | id | 读取保留的结果 |
| `stop` | id | 停止子运行并等清理完成 |

注意：

- `wait` 超时只是结束这一次等待，子运行还在跑。这时返回记录里的 `result` 可能是空的，要结合 `status` 决定下一步。
- child id 按调用成员保存。只有创建它的成员能管理它，把 id 发给其他成员，对方只能看。

## 预算

| 情况 | 子运行拿到的预算 |
| --- | --- |
| 省略 `budget` | 父成员尚未预留余额的一半，给父成员留出协调空间 |
| 显式 `budget` | 不超过可用余量 |
| 父运行额度无限 | 可以启动无限额度的子运行 |

- 有限额度会在活跃的子运行之间预留，也会影响父成员后续的模型请求。
- 用量汇总到父成员时，按实际消耗只计一次。

## 子运行什么时候结束

子运行持续到以下任一情况：入口返回、执行失败、父成员停止。

- 父成员正常完成时，会先停止还在跑的子运行，再保存最终的父快照。
- 取消时，先请求子运行停止会话并保存状态。如果迟迟不结束，由拥有它的 Environment 取消命令并清理进程。
- 普通 `bash` 命令的超时和这套生命周期是分开生效的。

## 排查错误

记录保存在哪：

- 环境里的 `.opencollab/child-runs/<id>` 保存活跃的控制文件。
- 父运行设置了产物目录时，会收集子运行的 Workflow 清单、会话、结果和命令输出。完成后 `artifacts` 指向宿主上的持久目录。
- 父运行关闭时，先清理环境里的控制文件，再捕获成员的改动。

错误会出现在哪：

- 源文件导入错误、执行错误、输出序列化错误，都会写进结果。
- 子工作流在会话失败后仍返回了数据时，`agent_failures` 照样保留失败信息。

排查顺序：先读结果和进程输出，再对照入口名和父成员的工具授权。

另外，子工作流里每次 Agent 调用都可以选 `shared`、`worktree` 或已配置的 Docker 隔离策略，子环境的分配和释放由宿主管理。

## 源码依据

[examples/member-authored-workflow/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/examples/member-authored-workflow/README.md#L3)、[opencollab/bootstrap/authored_workflow.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/authored_workflow.py#L52)。

## 相关章节

[让 Team 成员调用已注册的 Workflow](team-run-workflow.md)、[编写第一个自定义 Workflow](author-custom-workflow.md)、[在 Workflow 中调用 Agent：ctx.agent 与 ctx.agent_run](workflow-agent-calls.md)、[等待队友结果并读懂团队状态](../team/wait-for-team-results.md)。
