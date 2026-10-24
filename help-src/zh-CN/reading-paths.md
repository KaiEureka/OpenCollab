# 阅读路径

[Wiki](index.md) · [返回项目站主页](https://rise-x-lab.github.io/OpenCollab/) · [GitHub 源码](https://github.com/RISE-X-Lab/OpenCollab)

选择你要完成的任务，按顺序阅读相关指南。熟悉的部分可以跳过，直接进入需要的章节。

## 刚接触 OC

[先认识 Agent、Session、Team 和 Workflow](topics/orientation/concepts-and-modes.md) → [按任务选择默认 CLI、Base、Team 或 Workflow](topics/orientation/choose-working-mode.md) → [安装前提与版本选择](topics/setup/install-source-checkout.md) → [在自己的项目中完成第一次运行](topics/setup/first-run-workspace.md) → [默认终端交互与命令参数位置](topics/setup/interactive-cli-controls.md) → [读取运行结果与判断任务是否完成](topics/observability/run-results.md)

## 设计新的 Workflow

[理解 Workflow 的编程模型与自由度](topics/workflow/workflow-programming-model.md) → [判断 Workflow 能实现的范围与运行边界](topics/workflow/workflow-capabilities-boundaries.md) → [编写第一个自定义 Workflow](topics/workflow/author-custom-workflow.md) → [按开发任务查找 Workflow 公开 API](topics/workflow/workflow-public-api-map.md) → [设计 Workflow 的输入输出与步骤间数据流](topics/workflow/workflow-dataflow-structured-io.md) → [在 Workflow 中调用 Agent 并处理结果](topics/workflow/workflow-agent-calls.md) → [在 Workflow 中并行处理多项任务](topics/workflow/workflow-parallel-pipeline.md) → [理解 Agent、Team 与 Workflow 的调用层级](topics/workflow/workflow-composition-hierarchy.md) → [组合子 Workflow 并采用候选结果](topics/workflow/compose-workflow-candidates.md) → [处理 Workflow 的失败、重试与恢复](topics/workflow/workflow-failure-recovery-lifecycle.md) → [验证与调试自定义 Workflow](topics/workflow/workflow-testing-debugging.md)

## 建立团队并派发任务

[Team 完整指南与团队配置](topics/team/configure-team.md) → [派发 Agent 任务并安排审阅](topics/team/dispatch-team-agents.md) → [向队友发消息并继续协作](topics/team/message-team-agents.md) → [等待队友结果并读懂团队状态](topics/team/wait-for-team-results.md) → [采用队友改动并验证交付](topics/team/adopt-team-changes.md) → [让 Team 成员调用已注册 Workflow](topics/workflow/team-run-workflow.md) → [让成员编写并管理自己的子 Workflow](topics/workflow/member-authored-workflow.md)

## 理解与配置隔离

[隔离怎么选](topics/isolation/isolation-overview.md) → [默认隔离与单次覆盖怎么配](topics/isolation/isolation-configuration.md) → [多个任务怎样在独立工作区改代码](topics/isolation/worktree-workspaces.md) → [在容器中运行任务](topics/isolation/docker-isolation.md) → [让任务在指定目录或现有容器里执行](topics/isolation/execution-environments.md) → [把候选结果采纳到交付目录](topics/isolation/candidate-delivery.md) → [找回隔离任务的改动并清理资源](topics/isolation/workspace-recovery.md)

## 配置模型与运行参数

[配置从哪里读取以及哪一项生效](topics/models/configuration-precedence.md) → [配置参数与环境变量速查](topics/models/configuration-reference.md) → [接入模型端点与认证凭据](topics/models/provider-endpoint-credentials.md) → [选择调用协议与流式响应](topics/models/wire-protocol-streaming.md) → [配置深度思考与采样参数](topics/models/thinking-reasoning-sampling.md) → [检查有效配置与定位常见启动故障](topics/operations/configuration-diagnostics.md)

## 接入自己的应用服务

[从 Python 调用 OpenCollab](topics/setup/python-sdk-entrypoints.md) → [接入模型端点与认证凭据](topics/models/provider-endpoint-credentials.md) → [逻辑模型路由与共享请求容量](topics/models/model-routing-request-capacity.md) → [从示例接入自己的服务](topics/architecture/service-integration-lifecycle.md) → [中断任务与检查资源清理](topics/operations/cancellation-cleanup.md) → [查看 Agent 做了什么](topics/observability/trace-and-events.md) → [找到运行记录、会话和交付文件](topics/observability/artifacts-and-history.md)

## 理解原理与架构

[一次任务如何从输入走到工具执行](topics/architecture/runtime-execution-model.md) → [Session 如何保存进度并继续执行](topics/architecture/session-state-lifecycle.md) → [理解 Workflow 的编程模型与自由度](topics/workflow/workflow-programming-model.md) → [判断 Workflow 能实现的范围与运行边界](topics/workflow/workflow-capabilities-boundaries.md) → [隔离怎么选](topics/isolation/isolation-overview.md) → [OpenCollab 负责什么以及如何扩展](topics/architecture/extension-and-evaluation-boundaries.md) → [检查团队是否真的按分工协作](topics/observability/collaboration-adherence.md) → [用实际测试验证 Agent 的交付](topics/observability/native-test-evidence.md)
