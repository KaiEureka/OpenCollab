---
{
  "id": "docker-isolation",
  "title": "在容器中运行任务",
  "category": "isolation",
  "summary": "准备可执行的容器环境并按任务需求设置共享范围、网络和资源。",
  "aliases": [
    "组容器",
    "每人一个容器",
    "离线环境",
    "DockerOptions",
    "DockerMount",
    "docker_isolation_provider",
    "OPENCOLLAB_DOCKER_TEST_IMAGE"
  ],
  "keywords": [
    "Docker",
    "容器",
    "docker-group",
    "docker-session"
  ],
  "questions": [
    "准备可执行的容器环境并按任务需求设置共享范围、网络和资源？"
  ],
  "related": [
    "isolation-overview",
    "execution-environments",
    "workspace-recovery"
  ],
  "verified_ref": "e7946a42"
}
---

# 在容器中运行任务

使用 `docker-group` 可以让一组会话在同一容器的独立工作树里执行，使用 `docker-session` 可以让每个会话拥有独立容器。模型请求和 Python 调度由调用方进程负责，Agent 的文件与命令操作进入分配的容器。

## 准备镜像与联网条件

内置默认镜像为 `python:3.11`，运行前需要 Docker daemon 可用。自定义镜像要提供 Bash 和 Git，入口程序需要能够接收 `sleep infinity`。项目依赖最好直接放进任务镜像，这样执行和测试可以沿用离线网络设置。

`DockerOptions.network` 默认为 `none`。需要下载依赖或访问服务时，可以写 `bridge` 或一个已有的 Docker 网络名。`host` 会被拒绝。容器网络设置控制工具执行时的连接，模型请求通过调用方进程发送，使用客户端模型配置。部署时分别检查两处连接。

## 配置一次运行

```python
import asyncio
from opencollab import OpenCollab
from opencollab.environments import (
    DockerOptions, docker_isolation_provider,
)

async def inspect(ctx, inputs):
    return await ctx.agent(
        inputs["task"], tools="coding", label="inspector"
    )

async def main():
    options = DockerOptions(
        image="python:3.11",
        network="none",
        cpus=2,
        memory="2g",
        pids_limit=256,
    )
    client = OpenCollab(".", environment_providers={
        "docker-session": docker_isolation_provider(options, scope="session"),
    })
    result = await client.workflow(
        inspect, {"task": "执行 pwd 和 git status，并解释结果"},
        isolation="docker-session",
    )
    print(result.status, result.output)

asyncio.run(main())
```

要改成组容器，将注册名与 `isolation` 改为 `docker-group`，并把 `scope` 设为 `group`。默认提供器已经支持两种策略，显式注册主要用于替换镜像和资源选项。

`cpus` 是正有限数，`memory` 可以是正字节数或带 `b`、`k`、`m`、`g` 后缀的值，`pids_limit` 是正整数。它们约束分配的容器。组模式中，多名成员共享这一份资源额度。会话模式中，每个容器各自使用对应设置。

## 文件、临时目录与挂载

两种模式都会把源内容复制到容器私有存储，并准备可在容器中使用的 Git 元数据。源工作区可以位于本机，也可以来自已有 Docker 环境。组模式中，各会话的工作树独立，容器内的进程和公共临时目录共享。需要把这些资源分开时，使用会话模式。

只读素材可通过 `mounts=(DockerMount("/absolute/assets", "/assets"),)` 传入，`DockerMount` 从 `opencollab.environments` 导入。附加挂载固定只读，宿主 socket 会被拒绝。隔离提供器还要求挂载目标避开工作区、`/tmp` 和 `/var/tmp`。挂载前应检查素材目录中的凭据与私有文件。

## 验证实际环境和交付

可以在 Agent 执行的 Bash 中查看 `pwd`，读取 `/sys/fs/cgroup/cpu.max`、`memory.max`、`pids.max`，并根据任务镜像验证网络访问。观察两个会话能否读到同一个临时标记，可以直接区分组与会话的共享范围。实际字段会随容器平台的 cgroup 版本变化。

完整补丁会在运行拥有的容器移除前捕获。导出失败或得到截断结果时，工作区与容器会保留，恢复位置带完整容器 ID。按 [找回隔离任务的改动并清理资源](workspace-recovery.md) 收集改动，再处理资源回收。外部已有容器的接入方法见 [让任务在指定目录或现有容器里执行](execution-environments.md)。

配置与默认值见[Docker 值对象](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/domain/docker.py)，共享范围和生命周期见[Docker 隔离池](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/docker_isolation.py)。
