---
{
  "id": "custom-environment-providers",
  "title": "接入自己的执行环境",
  "category": "isolation",
  "summary": "使用已有环境提供器扩展隔离策略并让资源分配、交付和清理保持一致。",
  "aliases": [
    "custom",
    "EnvironmentPool",
    "Environment",
    "acquire",
    "release_env",
    "release",
    "adopt"
  ],
  "keywords": [
    "自定义环境",
    "环境提供器",
    "environment_providers"
  ],
  "questions": [
    "使用已有环境提供器扩展隔离策略并让资源分配、交付和清理保持一致？"
  ],
  "related": [
    "execution-environments",
    "workspace-recovery"
  ],
  "verified_ref": "e7946a42"
}
---

# 接入自己的执行环境

自定义执行环境通过 `environment_providers` 注册。键名成为可选的 `isolation` 策略，值是每次运行创建环境池的工厂。这样可以把现有容器配置或专用执行服务接到 Team 和 Workflow，同时沿用它们的环境申请、交付与清理过程。

## 从一个可工作的提供器开始

下面将已有 Docker 提供器注册为业务自己的策略名。镜像、网络与隔离能力仍来自这份 Docker 配置。

```python
from opencollab import OpenCollab
from opencollab.environments import DockerOptions, docker_isolation_provider

container_factory = docker_isolation_provider(
    DockerOptions(image="python:3.11", network="none"), scope="session"
)

def project_environment_factory(
    *, workspace, base_environment, owned_save_dir,
    enable_adoption, owned_output_paths,
):
    return container_factory(
        workspace=workspace,
        base_environment=base_environment,
        owned_save_dir=owned_save_dir,
        enable_adoption=enable_adoption,
        owned_output_paths=owned_output_paths,
    )

client = OpenCollab(".", environment_providers={
    "custom": project_environment_factory,
})
```

之后在运行中写 `await client.workflow(flow, inputs, isolation="custom")`。`flow` 是调用方自己的异步工作流，`inputs` 是它的输入字典。也可以在 Team 文件或角色中写 `isolation: custom`。策略名需要合法，并且与注册键保持一致。

## 工厂如何使用运行信息

`workspace` 是调用方项目位置，`base_environment` 是真实来源环境。`owned_save_dir` 指向当前运行产物目录，`owned_output_paths` 帮助环境区分运行自己写出的记录与任务源内容。`enable_adoption` 表示运行需要采纳能力，这会影响候选准备和交付方式。

环境池需要实现公开的 `opencollab.environments.EnvironmentPool` 接口。`acquire(role, source=...)` 接收角色和实际父环境或审阅源，返回已经准备好执行工具的 Environment。审阅源可能来自另一策略，也可能位于容器中，工厂应按环境操作读取其内容。直接从宿主同名目录复制，会让审阅任务读到错误版本。

## 为新后端实现同一组操作

接入远端执行服务时，先在一个环境中写入并读取文件，执行短命令并查看退出码。再申请两份环境，分别写入同名文件，观察内容是否独立。这样可以直接检查文件操作、命令执行和隔离是否可用。

明确环境对象与底层资源的关系。例如，环境对象可以代表一份会话租约，底层容器由池统一管理。多个环境视图引用同一资源时，归还其中一个视图后，资源仍需保留到其他使用者结束。审阅和清理都应遵循这一所有权关系。

## 把分配、交付与释放一起实现

`adopt(patch)` 将增量应用到交付工作区。`release_env(environment)` 归还一次分配，`release()` 结束池拥有的资源。传入的源环境保持借用关系，释放应针对池分配的资源。Environment 需要提供准备、文件读写、命令执行、撤销、停止和清理操作，详细签名见[公开端口](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/ports.py)。

取消可能发生在资源申请途中。提供器需要追踪尚未结束的申请，等它得到明确结果再完成撤销。补丁导出失败时，应保留可恢复的文件与资源信息，并让同一个池能够再次清理。接入时实际执行文件写入、命令取消和导出失败恢复，检查这些操作是否符合资源的所有权和清理要求。

为使用者说明提供器的文件共享范围、进程与网络隔离方式，以及权限限制。VM 专用后端仍处于[扩展规划](../../reserved/vm-environment-extension.md)。实现自己的 VM 环境时，上述生命周期同样需要完成。环境池组合行为见[策略池实现](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/session_environments.py)，已有环境的所有权见 [让任务在指定目录或现有容器里执行](execution-environments.md)。
