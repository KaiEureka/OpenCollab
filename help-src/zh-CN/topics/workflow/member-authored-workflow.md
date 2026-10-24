---
{
  "id": "member-authored-workflow",
  "title": "让成员自己写并运行子 Workflow",
  "category": "workflow",
  "summary": "Team 成员现场写一个 Python 子 Workflow，用 manage_workflow 启动它，再按 id 查看、等待、取结果或停止，可以并行调用多个 Agent。",
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
    "让成员编写并管理自己的子 Workflow",
    "子工作流",
    "成员现场编排"
  ],
  "keywords": [
    "manage_workflow",
    "成员编写工作流",
    "子运行",
    "运行时工作流",
    "child id",
    "child-runs",
    "describe",
    "ctx.parallel",
    "request_capacity",
    "vm-session",
    "worktree"
  ],
  "questions": [
    "Agent 能自己写一个工作流再运行吗？",
    "启动后怎样查询、等待和停止子 Workflow？",
    "父 Agent 结束后子 Workflow 会怎样？",
    "子 Workflow 的预算默认是多少？",
    "成员怎样现场编写一个 DW 并并行调用多个 Agent？",
    "使用 manage_workflow 前，人需要配置哪些资源和工具？",
    "VM 成员怎样在自己的 guest 内创建子工作树？"
  ],
  "related": [
    "team-run-workflow",
    "author-custom-workflow",
    "workflow-agent-calls",
    "wait-for-team-results",
    "workflow-parallel-pipeline",
    "vm-environment-extension"
  ],
  "verified_ref": "0c47f74a"
}
---

# 让成员自己写并运行子 Workflow

`manage_workflow` 是给 Team 成员用的一个工具。Team 成员就是 Team 里的一个 Agent 角色。有了这个工具，成员可以自己写一段 Python Workflow 代码，再把它当作子运行（child run）启动，类似 agent 在运行中自己拼出一个小型编排脚本。

用法分两步：

1. 成员用文件工具写出一个 Python 文件。
2. 成员调用 `launch` 启动它。`launch` 马上返回一个 child id，不会等子运行结束。之后成员用 `status`、`wait`、`result`、`stop` 按这个 id 管理子运行。

什么时候用它，什么时候用 `run_workflow`：

| 工具 | 跑什么 | 怎么等 |
| --- | --- | --- |
| `run_workflow` | 事先注册好的 Workflow 名称 | 同步等到结束 |
| `manage_workflow` | 成员现场写的源码 | 异步，按 id 管理 |

OC 会在成员自己的执行环境里起一个独立的 Python 进程，导入这个源文件。入口函数和普通 Workflow 一样，接收 `ctx` 和 `args`。

本页里的 DW（动态工作单元）就是指这样一段由程序安排执行步骤的代码。成员可以按任务现场写一个 DW，让里面的多个 Agent 分工处理输入。下面先讲只调一个 Agent 的最小例子，再扩展到并行跑三个任务。

版本要求：你需要安装源码修订 `0c47f74a` 对应的 OpenCollab。按[从源码安装并核对当前 checkout](../setup/install-source-checkout.md)准备解释器。本页源码依据里的链接带有完整修订号，可以用来定位代码。包内的版本号和已安装的源码修订要分别核对。

## 最小用法

开始前，你（人类调用方）要先准备好这些东西：

- 模型服务和额度。模型配置放在控制端进程的环境变量里，或者放在工作区之外的配置文件里。
- 执行环境。如果要用逻辑模型路由、共享请求容量、Docker 镜像或 VM，也由你提前配好。
- 凭据文件的位置。OC 会按快照规则把项目文件复制到子环境，所以凭据文件不要放在会被复制的目录里。
- 角色的工具授权，见下一步。

### 1. 给角色配工具

```yaml
entry: author
roles:
  author:
    prompt: 编写前先调用 manage_workflow describe，再按任务需要写出 Workflow，启动并读取结果。
    tools: [file_write, file_read, bash, manage_workflow]
```

环境要求：

- 目标环境里要有装了 OpenCollab 的 Python。默认解释器是 `python3`，可以用工具参数 `python` 指定别的路径。
- 角色必须有 `bash` 工具。`manage_workflow` 用成员的 shell 权限执行源代码。
- 在宿主机上直接执行时，Team 调用方要显式允许 `allow_unisolated_shell`。
- 容器或 VM 成员在自己的环境里执行源文件。那里的解释器也要装同一个 OpenCollab 源码修订。工具原有的进程隔离要求和命令安全检查照常生效。

### 2. 成员写出 Workflow 文件

成员先调用 `describe`，看看当前能用什么。

```json
{"action": "describe"}
```

返回的字段：

| 字段 | 含义 |
| --- | --- |
| `default_isolation` | 默认隔离策略 |
| `strategies` | 当前配置了哪些隔离策略 |
| `launch_allowed_in_current_environment` | 当前环境能不能 `launch` |
| `allowed_child_tools` | 子会话可以用哪些工具 |
| `builtin_child_tools` | 其中哪些可以用 `builtin_tools` 构造 |

返回值里还有入口说明和一段最小源码。成员照着这些信息写代码。

每个策略都标着 `checked_on_allocation`。意思是：`describe` 只列出配置，不占用资源。连接是否可用、资源够不够，要等实际分配时才检查。

然后成员用编辑工具在工作区写入 `generated.py`。下面是一个完整的子 Workflow 文件。它先算出输入的总和，再开一个 Agent 会话解释这个数。

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

- OC 按 `entry` 加载入口函数，默认是 `main`。不需要装饰器。装饰器只在按名称发现 Workflow 时才用。
- 子会话请求的工具名必须在父成员的可用工具里。
- 子会话继承父成员的输出上限、shell 隔离要求和文件创建限制。

### 3. 启动

```json
{
  "action": "launch",
  "source": "generated.py",
  "entry": "main",
  "args": {"count": 6},
  "python": "python3",
  "budget": 10000,
  "concurrency": 2,
  "isolation": "shared"
}
```

| 参数 | 说明 |
| --- | --- |
| `source` | 成员工作区里的 Python 文件，必填 |
| `entry` | 入口函数名，默认 `main` |
| `args` | 传给入口的输入。要能跨进程序列化，用字符串、数字、列表和字典即可 |
| `python` | 目标环境里的解释器，默认 `python3` |
| `budget` | 子运行的 token 预算，见[预算](#预算) |
| `concurrency` | 子运行里同时活跃的 Agent 会话上限，默认 4 |
| `isolation` | 子运行里 Agent 会话的默认隔离策略。省略时继承成员的策略 |
| `cleanup_timeout` | 清理宽限秒数，必须是正数。省略时继承父运行配置 |

`isolation` 和 `cleanup_timeout` 只能在 `launch` 里用。代码里单次调用 `ctx.agent` 或 `ctx.agent_run` 时，还可以再覆盖 `isolation`。

子进程能拿到什么、拿不到什么：

- OC 通过子进程的标准输入传入非敏感的生成设置、输入和工具限制。
- 模型凭据、端点路由、请求池路径和共享请求名额留在宿主的桥接层。子进程要请求模型时，经由桥接层交给宿主的模型客户端。

### 4. 等待结果

之后的操作都用 `launch` 返回的 id，例如：

```json
{"action": "wait", "id": "<child id>", "timeout": 30}
```

## 六种操作

| `action` | 需要的参数 | 返回什么 |
| --- | --- | --- |
| `describe` | 无额外参数 | 当前策略、权限、启动条件和入口示例 |
| `launch` | `source`，可加入口和输入 | 马上返回 id 和当前状态 |
| `status` | id | 状态、已用 tokens 和 artifacts |
| `wait` | id，可加正数 timeout | 等到完成，或本次等待超时 |
| `result` | id | 读取保留的结果 |
| `stop` | id | 停止子运行并等清理完成 |

注意：

- `wait` 超时只是结束这一次等待，子运行还在跑。这时返回记录里的 `result` 可能是空的，要结合 `status` 决定下一步。
- child id 只属于创建它的成员。只有创建者能查询和停止它。其他成员想操作，要通过 Team 已授权的通信关系联系创建者。

## 进阶：让一个 DW 并行跑三个 Agent

### 1. 写源码

成员调用 `file_write`，参数是 `path="generated.py"`、`mode="create"`，`content` 放下面的完整源码。源码由成员按任务自己写，`manage_workflow` 只负责加载文件和管理运行。

```python
async def main(ctx, args):
    # 工厂函数让每个任务保留自己的 index 和 task
    def make_task(index, task):
        async def run_one():
            answer = await ctx.agent(
                task,
                label=f"task-{index}",
                tools=[],  # 只分析输入文字，不需要工具
            )
            return {"task": task, "answer": answer}
        return run_one

    tasks = [make_task(i, task) for i, task in enumerate(args["tasks"])]
    results = await ctx.parallel(tasks)  # 结果按输入顺序返回
    return {"results": results, "spent_tokens": ctx.tokens_spent()}
```

要点：

- 入口默认叫 `main`，接收 `ctx` 和 JSON 对象 `args`，返回值要能 JSON 序列化。按源文件启动时，可以直接指定一个异步函数作为入口。
- `ctx.parallel` 接收一组无参的异步函数。
- 每个任务都会开一个新的 Agent 会话。
- 返回的列表保持输入顺序。

### 2. 启动

```json
{
  "action": "launch",
  "source": "generated.py",
  "entry": "main",
  "python": "python3",
  "args": {
    "tasks": [
      "用一句中文解释工作区的作用",
      "用一句中文解释会话的作用",
      "用一句中文解释并发的作用"
    ]
  },
  "budget": 12000,
  "concurrency": 2,
  "isolation": "shared"
}
```

`concurrency=2` 表示这个 DW 里同时最多有两个活跃的 Agent 会话。`ctx.parallel` 的任务容量也用这个值。所以三个任务会先跑两个，空出位置后再跑第三个。

同一段代码也能处理一百项输入。成员只要把一百项任务交给它，再按预算和模型服务的容量选一个并发数。

`isolation` 可选的值取决于当前配置，以 `describe` 返回的列表为准。常见的有：

| 策略 | 子 Agent 在哪执行 |
| --- | --- |
| `shared` | 和成员共用执行环境和工作区 |
| `worktree` | 在继承来的执行环境里，各自用独立工作树 |
| `docker-group` | 共用一个专属容器，各自用独立工作树 |
| `docker-session` | 每个子 Agent 一个专属容器和工作区 |
| `vm-group` | 租用一台已配置的 Linux VM，各自用独立工作树 |
| `vm-session` | 每个子 Agent 租用一台已配置的 Linux VM |

还可以有自定义名称的策略。

### 3. 观察和读取结果

启动后，成员记下返回的 child id，可以先去做别的事。下面每段 JSON 都是一次单独的工具调用。

```json
{"action": "status", "id": "<child id>"}
```

```json
{"action": "wait", "id": "<child id>", "timeout": 30}
```

```json
{"action": "result", "id": "<child id>"}
```

`wait` 和 `result` 返回的记录里有一个 `result` 字段，里面包含：

| 字段 | 内容 |
| --- | --- |
| `output` | 入口函数的返回值 |
| `reason` | 停止或失败的原因 |
| `agent_failures` | 内部 Agent 会话的失败记录 |

在这个例子里，成员要检查 `output` 里的三项 `results` 和每项的 `answer`。

状态 `completed` 只说明 Python 编排代码已经返回。项目是否真的交付了，要靠实际文件、测试或应用来验证。

需要停止时，调用 `stop`。它会等清理完成后再返回。

```json
{"action": "stop", "id": "<child id>"}
```

### 从宿主启动这个 Team

上面是成员这一侧的工具调用。在宿主这一侧，你只需要启动父 Team，成员会在任务里自己写并启动 DW。

配置好模型服务后，把下面的代码保存为工作区里的 `run_authored.py`，用装了指定源码修订的解释器运行。示例里的 `sys.executable` 只适用于父成员就在当前本地环境执行的情况。

```python
import asyncio
import sys
from pathlib import Path

from opencollab import OpenCollab

async def main():
    workspace = Path.cwd().resolve()
    pool = workspace / ".model-request-pool"
    pool.mkdir(exist_ok=True)
    client = OpenCollab(workspace, config={
        "request_pool": str(pool),
        "request_capacity": 2,
    })
    prompt = (
        "先调用 manage_workflow describe。用 file_write 创建 generated.py，"
        "定义 async main(ctx, args)，通过 ctx.parallel 为 args['tasks'] 的每项输入"
        "调用 ctx.agent，tools=[]，返回三项任务及其答案。"
        "launch 使用 entry=main、budget=12000、concurrency=2、isolation=shared，"
        f"python={sys.executable!r}。args 的 tasks 为"
        "['用一句中文解释工作区的作用', '用一句中文解释会话的作用', "
        "'用一句中文解释并发的作用']。保留启动返回的 id，查询状态，"
        "等待完成并读取结果，检查三项答案后提交。"
    )
    result = await client.team(
        prompt,
        config=workspace / "team.yaml",
        isolation="shared",
        allow_unisolated_shell=True,
        budget=20000,
        max_steps=16,
        timeout=180,
        artifacts=workspace / "authored-run",
        trace=False,
    )
    result.raise_for_status()
    print(result.output)

asyncio.run(main())
```

这里有两个容量设置，管的是不同的东西：

| 设置 | 在哪设 | 限制什么 |
| --- | --- | --- |
| `request_capacity=2` | 宿主的 `OpenCollab` 配置 | 共享模型客户端同时发出的原生请求数 |
| `concurrency=2` | `launch` 参数 | 单个 DW 里同时活跃的 Agent 会话数 |

`request_pool` 和 `request_capacity` 要一起设置。如果多个 Team 或 DW 用同一个模型服务，宿主要让它们使用同一个 `request_pool` 绝对目录和同样的容量配置。

一次原生请求结束后、开始执行工具前，OC 就会释放请求名额。所以父成员在等子运行时，子会话照样可以请求模型。

宿主可以通过 `client.configuration` 查看公开配置，包括模型、服务商、协议、路由映射、上下文和请求容量。成员则通过工具 schema 和 `describe` 了解自己能做什么。模型路由和容量由你提前配好，成员只按拿到的接口组织任务。

### 先跑一下仓库自带的本地演示

在装好当前源码的 checkout 里，可以先跑这个离线演示：

```bash
python examples/member-authored-workflow/demo.py
```

演示会做这些事：

1. 在回环地址上启动一个脚本化的 HTTP 模型，所有请求都留在本机。
2. 父成员真的写出 `generated.py`。
3. 启动 Python 子进程，收集原生结果。

输出里会有 `Local answer`、计算值 `15` 和总用量 `90`。程序会打印工作区路径，里面保留了源码和运行产物。跑通之后，再用上面的三任务源码和 Team 调用接上你自己的模型服务。

### 在 VM 成员里用独立工作树

先按[VM 环境扩展](../isolation/vm-environment-extension.md)准备好这些：宿主上的 VM profile、SSH、guest 里同一修订的 OpenCollab，以及管理员 reset 操作。

父 Team 使用 `isolation="vm-session"` 时，author 成员会拿到一台 VM。成员先 `describe`，再把源码写进这台 VM 的工作区，然后用下面的参数启动。这样子会话会在同一台 guest 里各自创建独立工作树。

```json
{
  "action": "launch",
  "source": "generated.py",
  "entry": "main",
  "python": "/home/worker/.venvs/oc/bin/python",
  "args": {"tasks": ["检查输入文件", "验证运行结果", "汇总观察记录"]},
  "concurrency": 2,
  "isolation": "worktree"
}
```

- `python` 指向准备 VM 时装了 OpenCollab 的 guest 解释器。
- DW 的编排子进程也跑在父成员的 VM 里。子 Agent 的工具在这台 VM 的独立工作树里执行。
- 子 Agent 要查看或修改文件的话，父角色得先有对应工具，然后在 DW 源码里用 `builtin_tools` 传给会话。比如给一个明确的文件检查任务配上 `builtin_tools("bash")`。
- 子进程的标准输入只有非敏感的生成设置、输入和工具限制。guest 经由 bridge 请求宿主的模型客户端，凭据和路由不进 guest。

如果某次调用选了 `isolation="vm-session"`，宿主会再为它申请一台空闲 VM。父成员手里的那台 VM 也算在资源占用里，所以规划容量时要给额外的 guest 留出位置。

## 预算

| 情况 | 子运行拿到的预算 |
| --- | --- |
| 省略 `budget` | 父成员尚未预留余额的一半，给父成员留出协调空间 |
| 显式 `budget` | 不超过可用余量 |
| 父运行额度无限 | 可以启动无限额度的子运行 |

- 有限预算会为活跃的子运行预留。预留的部分父成员后续的模型请求也不能用。
- 子运行的用量按实际消耗计入父成员，只计一次。

## 子运行什么时候结束

出现以下任一情况，子运行就结束：入口返回、执行失败、父成员停止。

- 父成员正常完成时，OC 先停止还在跑的子运行，再保存最终的父快照。
- 取消时，OC 先请子运行停止会话并保存状态。如果它迟迟不结束，由所属的 Environment 取消命令并清理进程。
- 普通 `bash` 命令的超时和这套生命周期互不影响。

## 排查错误

记录保存在哪：

- 环境里的 `.opencollab/child-runs/<id>` 保存运行中的控制文件。
- 如果父运行设置了产物目录，OC 会收集子运行的 Workflow 清单、会话、结果和命令输出。完成后 `artifacts` 指向宿主上的持久目录。
- 父运行关闭时，OC 先清理环境里的控制文件，再捕获成员的改动。

错误会出现在哪：

- 源文件导入错误、执行错误、输出序列化错误，都会写进结果。
- 如果某个 Agent 会话失败了，但子 Workflow 仍然返回了数据，`agent_failures` 照样会记下失败信息。

排查顺序：先读结果和进程输出，再核对入口名和父成员的工具授权。

其他细节：

- 子 Workflow 里每次 Agent 调用都可以单独选隔离策略，名称以 `describe` 返回的为准。
- 子会话的步数上限默认取父成员的步骤上限。作者也可以用 `ctx.agent_run(max_steps=...)` 指定单次步数。
- 子环境的分配和释放由宿主管理。

## 源码依据

[能力发现与子运行启动](https://github.com/RISE-X-Lab/OpenCollab/blob/0c47f74a27441888286a22012b23b658185f8b8f/opencollab/bootstrap/authored_workflow.py#L63)、[宿主模型桥接](https://github.com/RISE-X-Lab/OpenCollab/blob/0c47f74a27441888286a22012b23b658185f8b8f/opencollab/bootstrap/authored_workflow_bridge.py#L127)、[子会话权限继承](https://github.com/RISE-X-Lab/OpenCollab/blob/0c47f74a27441888286a22012b23b658185f8b8f/opencollab/bootstrap/authored_workflow_runner.py#L38)。

[examples/member-authored-workflow/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/0c47f74a27441888286a22012b23b658185f8b8f/examples/member-authored-workflow/README.md#L3)、[opencollab/bootstrap/authored_workflow.py](https://github.com/RISE-X-Lab/OpenCollab/blob/0c47f74a27441888286a22012b23b658185f8b8f/opencollab/bootstrap/authored_workflow.py#L52)。

## 相关章节

[让 Team 成员调用已注册的 Workflow](team-run-workflow.md)、[编写第一个自定义 Workflow](author-custom-workflow.md)、[在 Workflow 中调用 Agent：ctx.agent 与 ctx.agent_run](workflow-agent-calls.md)、[等待队友结果并读懂团队状态](../team/wait-for-team-results.md)。
