---
{
  "id": "isolation-overview",
  "title": "隔离怎么选",
  "category": "isolation",
  "summary": "根据任务共享文件、运行代码和联网的需求选择合适的隔离策略。",
  "aliases": [
    "沙箱",
    "互相覆盖",
    "独立会话",
    "shared",
    "worktree",
    "docker-group",
    "docker-session",
    "custom"
  ],
  "keywords": [
    "隔离",
    "环境隔离",
    "isolation"
  ],
  "questions": [
    "几个 Agent 会不会互相覆盖文件？",
    "worktree 能隔离进程和网络吗？",
    "怎样让每个 Agent 在独立容器里运行？",
    "Agent 之间能看到彼此的对话吗？"
  ],
  "related": [
    "isolation-configuration",
    "docker-isolation",
    "custom-environment-providers",
    "shell-permissions-execution",
    "execution-environments",
    "candidate-delivery"
  ],
  "verified_ref": "e7946a42"
}
---

# 隔离怎么选

按任务需要访问和修改的资源选择隔离策略。只读分析可以复用目录，并行改代码通常适合独立工作树。代码需要在容器内运行时，可以让一组会话共享容器，或让每个会话拥有自己的容器。每个 Agent 都保存独立对话，文件和系统资源的共享方式由执行环境决定。

## 分开判断五种范围

对话隔离决定模型能读到哪些消息。每个会话保存自己的历史，通过任务说明、显式上下文、队友消息和完成结果接收协作信息。共享文件也可以传递信息，因此需要分别选择对话和文件的共享方式。

文件隔离决定成员是否在同一目录中修改文件。进程隔离决定进程树、后台服务和资源的共享范围，网络隔离决定任务能连接哪些服务。权限控制模型可调用的工具，以及可访问的路径和命令。例如，移除角色的 `bash` 可以限制命令执行，设置容器网络可以限制服务连接。

| 策略 | 文件关系 | 进程与网络范围 | 适合的工作 |
| --- | --- | --- | --- |
| `shared` | 复用父环境和目录 | 沿用父环境 | 顺序分析、明确共用同一份文件 |
| `worktree` | 父环境内的独立工作树，普通目录使用副本 | 沿用父环境 | 并行实现、比较不同改法 |
| `docker-group` | 同一容器内各会话独立工作树 | 同组共享容器的进程与网络环境 | 共享依赖和临时服务的一组任务 |
| `docker-session` | 每个会话拥有独立容器和目录 | 每个会话使用自己的容器范围 | 会相互影响的代码执行任务 |
| 已注册策略名 | 由提供器决定 | 由提供器决定 | 接入专用环境服务 |

工作树共享 Git 对象存储。同组容器里的成员也能通过绝对路径、公共临时目录或进程产生影响。任务要验证这些区别时，可以让两个成员分别执行 `pwd`、查看 `/tmp` 中的任务标记，再观察一个成员启动的服务是否能被另一个成员访问。观察动作应在实际任务环境里完成。

## 用实际任务作选择

顺序阅读仓库时，可以共用目录并为角色配置只读工具，减少重复准备工作区的时间。两个成员分别尝试不同修复时，独立工作树能让它们从清晰的起点各自修改，入口再比较和采纳。在任务中写清各成员负责的文件范围，有助于减少合并冲突。

需要运行未知脚本或测试项目安装过程时，应先考虑进程与网络范围。组容器适合共同使用一次准备好的服务和依赖，会话容器适合让安装、后台进程和临时文件各自分开。联网构建可以选择允许的 Docker 网络，并通过镜像预装尽量减少运行期下载。涉及敏感宿主资源时，还应检查只读素材、可写项目目录和可访问凭据。

隔离策略决定工具操作的落点。项目依赖由镜像或环境准备过程提供，候选文件通过采纳过程进入最终目录。完成环境准备后，应执行项目本来的构建或测试命令，查看退出码和真实输出。遇到权限拒绝时，先找到是角色工具、命令授权、路径检查还是环境限制，再调整对应设置。

## 在运行和调用两层选择

下面的 Workflow 默认使用独立工作树，第二次调用显式覆盖为共享环境。两个调用通过传入输出衔接内容，对话历史各自独立。

```python
import asyncio
from opencollab import OpenCollab
from opencollab.tools import builtin_tools

async def inspect(ctx, inputs):
    first = await ctx.agent(
        inputs["task"], tools=builtin_tools("file_read", "grep"), isolation="worktree"
    )
    summary = await ctx.agent(
        f"根据这份分析概括项目结构\n{first}", tools=[], isolation="shared"
    )
    return {"inspection": first, "summary": summary}

async def main():
    result = await OpenCollab(".").workflow(
        inspect, {"task": "阅读项目并说明入口"}, isolation="worktree"
    )
    print(result.raise_for_status().output)

asyncio.run(main())
```

这里把 `first` 显式放进下一次请求。`tools=[]` 让摘要调用处理传入的分析文本，需要它继续阅读文件时，应传入文件工具，并在任务中明确材料路径。工作树里的改动以候选或补丁交付，采纳后才会出现在目标交付目录，具体见 [把候选结果采纳到交付目录](candidate-delivery.md)。

## 容器和权限怎样配合

Docker 策略的 Python 调度仍在调用方进程里运行，文件工具和命令进入所选容器。内置 Docker 默认网络为 `none`，安装在线依赖需要预先准备镜像，或明确设置允许的 Docker 网络。宿主执行 `bash` 的授权、文件工具的路径检查以及容器限制分别生效。处理未知代码时，应选可丢弃的隔离环境，并检查挂载素材与凭据范围。

内置环境提供器支持 `shared`、`worktree`、`docker-group` 和 `docker-session`。`custom` 是注册自定义提供器后使用的策略名。VM 专用后端属于[后续扩展规划](../../reserved/vm-environment-extension.md)，当前可以由调用方实现环境端口并接入。具体参数见 [默认隔离与单次覆盖怎么配](isolation-configuration.md) 和 [在容器中运行任务](docker-isolation.md)。

策略语义和实际分配方式可对照[隔离指南](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/session-isolation.zh-CN.md)及[环境池实现](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/session_environments.py)。
