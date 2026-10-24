---
{
  "id": "concepts-and-modes",
  "title": "先认识 Agent、Session、Team 和 Workflow",
  "category": "orientation",
  "summary": "建立 OC 的使用概念，理解不同对象之间的关系并找到下一篇指南。",
  "aliases": [
    "智能体",
    "会话",
    "harness",
    "多智能体",
    "工作流和团队区别"
  ],
  "keywords": [
    "OC概念",
    "OC是什么",
    "Agent",
    "Session"
  ],
  "questions": [
    "建立 OC 的使用概念，理解不同对象之间的关系并找到下一篇指南？"
  ],
  "related": [
    "choose-working-mode",
    "base-single2",
    "configure-team",
    "run-registered-workflow"
  ],
  "verified_ref": "e7946a42"
}
---

# 先认识 Agent、Session、Team 和 Workflow

OC 把模型调用、工具执行、会话状态和协作组织放在一个运行框架里。你可以让一个 Agent 处理任务，也可以把工作分给一个 Team，或用 Python 写明每一步怎样交接。模型服务、执行环境和结果记录可以分别配置。

## 从一次任务理解框架

用户提出目标后，模型接收当前会话内容，返回文字或工具调用。OC 检查调用参数及可用工具，把调用交给执行环境，再将结果送回模型。这个往返会持续到任务结束、触及限制或运行被取消。模型输出一条命令与系统实际执行了这条命令，属于过程中的两个阶段。

Agent 表示带有模型、提示和工具配置的执行角色。Session 保存一次对话的消息、执行阶段、用量和等待状态。同一角色可以参与多次任务，每次任务需要清楚关联到相应会话。

## 三种使用方式

| 方式 | 下一步由谁决定 | 适用目标 |
| --- | --- | --- |
| 单 Agent | 模型在当前工具集合中决定 | 调查代码、完成一次修复、给出分析 |
| Team | 成员根据角色提示和通信关系派发、交流 | 分工随任务变化，需要成员自主协作 |
| Workflow | Python 函数决定调用顺序与结果处理 | 对照方案、重复执行流程、明确验证与采用过程 |

三种方式共用底层会话和工具机制。Team 中的一个成员可以获得调用 Workflow 的能力，Workflow 又能启动 Agent 会话。读代码时应区分“组织任务的控制器”和“实际与模型往返的会话”。

## 默认入口是什么

`opencollab --workspace .` 启动内置 Self-Collaboration 团队。SDK 的 `OpenCollab(...).agent(...)` 是单 Agent 入口，当前 Base 配置映射到 Single2。Duo、Weave 与 Reconstruct 则是已经实现的具体 Workflow。

因此，“默认 CLI”“Base Agent”和“Duo”各自代表不同入口。配置工具时也要区分普通编码工具和需要 Team 调度器的协作工具。

## 哪些信息会共享

每个会话分别保存对话。任务说明、显式消息、返回结果决定成员收到的信息。文件是否共享由执行环境和隔离策略决定。两个 Agent 可以拥有独立对话却编辑同一个目录，也可以拥有独立工作树并通过补丁交付。模型请求容量又是另一项设置，负责约束同时发往服务端的请求。

先根据任务选择运行方式，再确定工作区、模型和工具，最后配置预算及结果保存。这样可以逐项观察配置是否符合预期。

## 源码与进一步阅读

[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md) · [opencollab/sdk/client.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/client.py) · [opencollab/domain/session.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/domain/session.py) · [docs/runtime-design.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/runtime-design.md)

[按任务选择默认 CLI、Base、Team 或 Workflow](choose-working-mode.md) · [使用 Base 单 Agent 与 Single2 配置](../setup/base-single2.md) · [Team 完整指南与团队配置](../team/configure-team.md) · [Workflow 完整指南与运行入口](../workflow/run-registered-workflow.md)
