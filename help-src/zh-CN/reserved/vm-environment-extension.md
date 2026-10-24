---
{
  "id": "vm-environment-extension",
  "title": "规划 VM 执行环境接入",
  "category": "reserved",
  "summary": "评估需要独立客体系统的任务，并确定后续 VM 提供器应支持的实际操作。",
  "aliases": [
    "虚拟机沙箱",
    "独立系统",
    "Environment",
    "EnvironmentPool",
    "custom",
    "environment_providers"
  ],
  "keywords": [
    "虚拟机",
    "VM",
    "VM 隔离"
  ],
  "questions": [],
  "related": [
    "isolation-overview",
    "custom-environment-providers",
    "execution-environments"
  ],
  "verified_ref": "e7946a42",
  "status": "planned"
}
---

# 规划 VM 执行环境接入

VM 专用后端处于规划阶段。应用可以通过自定义环境提供器连接自己的虚拟机服务，负责虚拟机的创建、文件交换和资源释放。

## 适合哪些任务

虚拟机为工具提供独立的客体系统，适合需要单独内核、系统依赖或网络配置的任务。选择后端时，需要确定项目文件怎样进入虚拟机、命令在哪里执行，以及完成后的改动怎样返回。

## 对接环境接口

通过 `environment_providers` 注册工厂，由工厂返回实现 `EnvironmentPool` 的对象。环境池负责分配和释放任务环境，环境对象提供文件读写与命令执行。接口和注册示例见 [接入自己的执行环境](../topics/isolation/custom-environment-providers.md)。

工具运行前，在客体系统中准备工作区和项目依赖。文件读取、写入和命令执行应访问同一份工作区，网络访问与宿主文件挂载由虚拟机后端配置。

## 交付与资源管理

任务结束后，后端把候选文件或补丁导出到调用方可读取的位置，再按采用结果更新交付目录。调用方已有的虚拟机继续由调用方管理，为任务新建的虚拟机则随该任务释放。

取消任务时，需要同时处理客体内命令和虚拟机资源。接入完成后，用文件修改、命令执行、结果回传和中途取消验证整个过程，再用于正式任务。

## 对应材料

[opencollab/adapters/session_environments.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/session_environments.py#L31)

[opencollab/environments.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/environments.py#L3)

[opencollab/application/ports.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/ports.py#L13)

## 关联阅读

[隔离怎么选](../topics/isolation/isolation-overview.md) · [接入自己的执行环境](../topics/isolation/custom-environment-providers.md) · [让任务在指定目录或现有容器里执行](../topics/isolation/execution-environments.md)
