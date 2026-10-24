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

## 文档目标

评估需要独立客体系统的任务，并确定后续 VM 提供器应支持的实际操作。

这项扩展处于规划阶段，后续依据后端实现与实测结果补充操作指南。

## 说明目标任务与边界

根据独立客体系统、文件交换和网络访问需求定义 VM 接入的用户目标。

## 沿用自定义环境入口

规划通过现有 environment_providers 接入 VM 工厂的路径，并标明该后端的 planned 状态。

## 准备代码与依赖

规划 VM 中工作区创建、项目文件传入和可执行依赖准备的操作方式。

## 运行工具与回传改动

规划文件操作、命令执行、完整候选导出和采纳需要支持的最小能力。

## 处理取消和资源回收

规划 VM 生命周期、借用资源和失败恢复行为，并以真实任务执行及资源测量验证接入效果。

## 对应材料

[opencollab/adapters/session_environments.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/session_environments.py#L31)

[opencollab/environments.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/environments.py#L3)

[opencollab/application/ports.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/ports.py#L13)

## 关联阅读

[隔离怎么选](../topics/isolation/isolation-overview.md) · [接入自己的执行环境](../topics/isolation/custom-environment-providers.md) · [让任务在指定目录或现有容器里执行](../topics/isolation/execution-environments.md)
