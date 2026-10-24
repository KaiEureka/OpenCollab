---
{
  "id": "vm-environment-extension",
  "title": "接入 VM 执行环境（规划中）",
  "category": "reserved",
  "summary": "OC 还没有内置 VM 后端；需要独立系统的任务，可以通过自定义环境提供器接自己的虚拟机服务。",
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
    "VM 隔离",
    "虚拟机后端",
    "自定义环境提供器"
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

# 接入 VM 执行环境（规划中）

OC 目前没有内置的 VM 后端，它还在规划中。如果任务需要独立的内核、系统依赖或网络配置，可以用自定义环境提供器接入你自己的虚拟机服务。虚拟机的创建、文件交换和释放由你的提供器负责。

## 什么时候需要 VM

虚拟机给工具一个独立的客体系统（guest OS）。适合需要单独内核、系统依赖或网络配置的任务。

选后端前先回答三个问题：

1. 项目文件怎样进入虚拟机？
2. 命令在哪里执行？
3. 完成后的改动怎样返回？

## 怎样对接

1. 通过 `environment_providers` 注册一个工厂。
2. 工厂返回一个实现 `EnvironmentPool` 的对象。环境池负责分配和释放任务环境。
3. 环境对象提供文件读写和命令执行。

接口和注册示例见 [接入自己的执行环境](../topics/isolation/custom-environment-providers.md)。

工具运行前，在客体系统里准备好工作区和项目依赖。文件读取、写入和命令执行要访问同一份工作区。网络访问和宿主文件挂载由虚拟机后端配置。

## 交付与资源管理

- 任务结束后，后端把候选文件或补丁导出到调用方能读取的位置，再按采用结果更新交付目录。
- 调用方已有的虚拟机，继续由调用方管理。
- 为任务新建的虚拟机，随任务释放。
- 取消任务时，要同时处理客体内的命令和虚拟机资源。

## 上线前验证

依次验证这四项，都通过后再用于正式任务：

1. 文件修改
2. 命令执行
3. 结果回传
4. 中途取消

## 对应材料

[opencollab/adapters/session_environments.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/session_environments.py#L31)

[opencollab/environments.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/environments.py#L3)

[opencollab/application/ports.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/ports.py#L13)

## 关联阅读

[隔离怎么选](../topics/isolation/isolation-overview.md) · [接入自己的执行环境](../topics/isolation/custom-environment-providers.md) · [让任务在指定目录或现有容器里执行](../topics/isolation/execution-environments.md)
