---
{
  "id": "choose-working-mode",
  "title": "按任务选择默认 CLI、Base、Team 或 Workflow",
  "category": "orientation",
  "summary": "根据交互需求、可重复流程和协作方式选择最合适的启动入口。",
  "aliases": [
    "单智能体还是多智能体",
    "Team vs Workflow",
    "默认模式",
    "Duo还是Weave"
  ],
  "keywords": [
    "模式选择",
    "用哪个模式"
  ],
  "questions": [
    "根据交互需求、可重复流程和协作方式选择最合适的启动入口？"
  ],
  "related": [
    "interactive-cli-controls",
    "base-single2",
    "configure-team",
    "run-registered-workflow",
    "duo-workflow",
    "weave-workflow"
  ],
  "verified_ref": "e7946a42"
}
---

# 按任务选择默认 CLI、Base、Team 或 Workflow

选择模式时，先确定任务怎样推进。需要边聊边改代码，可以使用默认 CLI。从程序运行一个 Agent，可以使用 Base。让成员根据情况分工，可以配置 Team。按指定算法安排协作，可以编写 Workflow。

## 按目标选择

| 你的目标 | 推荐起点 | 需要继续决定的事项 |
| --- | --- | --- |
| 在终端持续处理项目 | 默认 CLI | 工作目录、模型、命令权限 |
| 从程序获得一次编码结果 | `oc.agent(...)` | 工具、额度、产物目录 |
| 让分析、实现、测试角色自主交接 | `oc.team(...)` 或团队 CLI | 角色、通信方向、改动采用 |
| 强制先实现再验证 | 自定义 Workflow | 输入输出、验证与失败处理 |
| 比较两个编码候选 | Duo | 工具环境、候选证据和交付方式 |
| 将应用开发拆成有检查的工作组 | Weave | 组依赖、检查器和修复预算 |
| 根据输入输出探索隐藏程序 | Reconstruct | 查询接口、候选执行器和停止条件 |

任务简单时，一个 Agent 通常就能完成，也更容易查看执行过程。增加角色后，需要安排成员交接、传递上下文，并将改动交付到目标目录。

## Team 与 Workflow 可以组合

Team 适合把“下一步找谁”交给成员决定。拓扑声明谁可以派发或通信，角色工具决定能执行的操作。一个注册了 `run_workflow` 的成员，可以在任务需要时调用已安装的工作流。

Workflow 适合把协作算法写进程序。例如先让两个候选各自实现，再执行检查，最后根据结果采用其中一个。普通 Python 条件和循环决定流程结构，OC 提供 Agent、候选、并发和用量管理等操作。

成员可以用 `manage_workflow` 编写并启动 Python 工作流文件。它与 `run_workflow` 的启动、停止和清理方式各有规定，使用方法见对应指南。

## 隔离与模式分别选择

Team 和 Workflow 负责组织任务，执行环境与隔离策略决定文件和命令在哪里运行。Workflow 的 Python 调度代码运行在调用方进程中。选择组织方式后，再设置 `shared`、`worktree` 或 Docker 策略以及命令权限。

对陌生仓库，可以先用只读工具调查。需要执行项目代码时，应明确执行环境及其网络和挂载设置。开始时保留运行记录，便于看到实际调用、停止原因和改动去向。

## 从哪条路径开始读

使用终端可以从第一次运行与默认交互开始。接入应用可以从 SDK 与运行结果开始。设计新协作算法可以先读 Workflow 编程模型，再查公开 API、数据流和调试方法。Skill 提供任务指导，具体流程通过程序和运行接口实现。

## 源码与进一步阅读

[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md) · [docs/single2.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/single2.md) · [docs/duo.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/duo.md) · [docs/weave.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/weave.md)

[默认终端交互与命令参数位置](../setup/interactive-cli-controls.md) · [使用 Base 单 Agent 与 Single2 配置](../setup/base-single2.md) · [Team 完整指南与团队配置](../team/configure-team.md) · [Workflow 完整指南与运行入口](../workflow/run-registered-workflow.md) · [使用 Duo 生成并选择双候选方案](../workflow/duo-workflow.md) · [使用 Weave 分组执行并检查交付](../workflow/weave-workflow.md)
