---
{
  "id": "execution-environments",
  "title": "让任务在指定目录或现有容器里执行",
  "category": "isolation",
  "summary": "将模型的文件与命令操作连接到目标工作区，并掌握环境资源的所有权。",
  "aliases": [
    "接已有容器",
    "本地目录",
    "local_environment",
    "worktree_environment",
    "docker_environment",
    "attach_container",
    "build_repo_map_via_env",
    "Environment"
  ],
  "keywords": [
    "执行环境",
    "指定工作目录",
    "environment"
  ],
  "questions": [
    "将模型的文件与命令操作连接到目标工作区，并掌握环境资源的所有权？"
  ],
  "related": [
    "isolation-overview",
    "docker-isolation",
    "custom-environment-providers"
  ],
  "verified_ref": "e7946a42"
}
---

# 让任务在指定目录或现有容器里执行

`OpenCollab(workspace, environment=...)` 可以把 Agent 的文件与命令工具接到指定环境。`workspace` 标识调用方项目，注入的 environment 决定工具实际看到的目录和执行位置。接入已有容器时，文件应从环境读取，调用方本机同名路径可能对应另一份内容。

## 本机目录与独立工作树

`local_environment(path)` 创建本机目录环境，`worktree_environment(path)` 创建尚未准备的工作树环境。公开工厂都把对象交给调用方持有，调用方负责 `setup()` 和最终 `cleanup()`。

```python
import asyncio
from opencollab import OpenCollab
from opencollab.environments import local_environment, build_repo_map_via_env

async def main():
    environment = local_environment(".")
    try:
        await environment.setup()
        print(await build_repo_map_via_env(environment))
        result = await OpenCollab(".", environment=environment).agent(
            "阅读 README 并解释项目目标", tools="coding"
        )
        print(result.raise_for_status().output)
    finally:
        await environment.cleanup()

asyncio.run(main())
```

`build_repo_map_via_env` 从目标环境读取路径概览，并限制概览大小。返回空字符串可能是读取失败，可以用 `exec_cmd("pwd")` 和 `read_file("README.md")` 查看执行位置与文件内容。

## 新建容器和连接已有容器

`docker_environment(image, backing_environment, options=...)` 创建由调用方管理的新容器环境。需要把宿主目录映射为容器工作区时，先准备来源，再传入 `await container.setup(mount_dir=prepared_path)`。项目通过该挂载读写，附加只读素材使用 DockerOptions 中的 DockerMount。容器清理会连同其拥有的 backing_environment 资源一起处理，调用方需据此安排清理顺序。

连接服务已有的运行容器，可以使用下列公开入口。

```python
from opencollab.environments import attach_container

environment = attach_container(
    container_id="existing-project-container",
    workspace="/workspace/project",
)
await environment.setup()
probe = await environment.exec_cmd("pwd")
print(probe.returncode, probe.stdout)
```

这里的代码在已有异步函数中执行。容器参数接受完整容器 ID 或合法名称，目录是容器内经过规范化的绝对路径，并且应指向项目目录。`command_prefix` 可以接入已有命令包装，`timeout_returncode` 可以匹配集成端的超时返回约定。

## 选择路径时保留相对关系

本机的工作树目录与容器内的项目目录可以不同，工具的相对路径始终从 environment.workspace 解释。服务可以把宿主项目作为客户端的 workspace，再把容器中的工作目录交给 environment，这使配置发现和实际工具执行各有明确位置。

连接后先读取项目中确实存在的小文件，再执行一次与任务相符的命令。文件读取能确认目标内容，命令结果能确认运行依赖与当前目录。如果服务需要在容器里运行子 Agent，后续 worktree 策略会沿用容器父环境。想让每个子会话获得另一个容器，则应使用 Docker 会话隔离提供器。

## 资源由谁结束

SDK 借用传入的 environment，运行完成后由调用方清理。连接已有容器的环境会管理本次发出的命令和临时资源，外部容器继续由创建它的服务管理。新建容器则由拥有方在导出交付后清理。

调用方提供环境时，SDK 结果中用于表示环境活动已结束的指标为 `None`，由调用方清理环境并确认操作已停止。会话执行与持久化是否完成，可查看 `session_quiesced`。具体说明见 [从示例接入自己的服务](../architecture/service-integration-lifecycle.md)，策略选择见 [隔离怎么选](isolation-overview.md)。

工厂签名见[公开环境 API](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/environments.py)和[环境组合实现](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/programmatic.py)。
