# 问题导航

[Wiki](index.md) · [返回项目站主页](https://rise-x-lab.github.io/OpenCollab/) · [GitHub 源码](https://github.com/RISE-X-Lab/OpenCollab)

找到你关心的问题，点击右侧指南查看解释和示例。

| 问题 | 对应指南 |
| --- | --- |
| 哪些环境变量和 SDK 字段控制模型与运行参数 | [配置参数与环境变量速查](topics/models/configuration-reference.md) |
| 如何查看各项配置的内置默认值 | [配置参数与环境变量速查](topics/models/configuration-reference.md) |
| OC 本身提供什么，为何还需要模型服务 | [先认识 Agent、Session、Team 和 Workflow](topics/orientation/concepts-and-modes.md) |
| 默认终端启动的是一个 Agent 还是一个团队 | [默认终端交互与命令参数位置](topics/setup/interactive-cli-controls.md) |
| Base、Single2、Team 和 Workflow 怎么选 | [按任务选择默认 CLI、Base、Team 或 Workflow](topics/orientation/choose-working-mode.md) |
| 第一次使用 OC 应该运行哪个例子 | [在自己的项目中完成第一次运行](topics/setup/first-run-workspace.md) |
| 模型名、端点和环境变量谁优先 | [配置从哪里读取以及哪一项生效](topics/models/configuration-precedence.md) |
| 一个团队可以使用不同模型吗 | [逻辑模型路由与共享请求容量](topics/models/model-routing-request-capacity.md) |
| 同一台机器的多个进程怎样共用模型额度 | [逻辑模型路由与共享请求容量](topics/models/model-routing-request-capacity.md) |
| 并发 Agent 数和模型请求数是同一个设置吗 | [设置 token 预算、步数与运行并发](topics/operations/token-step-concurrency-limits.md) |
| 保存的 reasoning 是否都属于当前可见正文 | [配置深度思考与采样参数](topics/models/thinking-reasoning-sampling.md) |
| 上下文什么时候压缩，历史是否仍保留 | [上下文窗口与自动压缩](topics/operations/context-compaction.md) |
| Workflow 支持哪些语法，可以用 if、while 和导入模块吗 | [理解 Workflow 的编程模型与自由度](topics/workflow/workflow-programming-model.md) |
| 任何协作想法都能写成 Workflow 吗 | [判断 Workflow 能实现的范围与运行边界](topics/workflow/workflow-capabilities-boundaries.md) |
| Workflow 的控制粒度有哪些，有统一的低中高档位吗 | [选择 Workflow 的入口与控制粒度](topics/workflow/workflow-control-granularity.md) |
| 写 Python 流程时可用的 ctx API 有哪些 | [按开发任务查找 Workflow 公开 API](topics/workflow/workflow-public-api-map.md) |
| 怎样把上一轮结果送给下一轮 Agent | [设计 Workflow 的输入输出与步骤间数据流](topics/workflow/workflow-dataflow-structured-io.md) |
| 普通 async 函数可以组合为子 Workflow 吗 | [理解 Agent、Team 与 Workflow 的调用层级](topics/workflow/workflow-composition-hierarchy.md) |
| Workflow 能否直接通过 ctx 启动一个 Team | [理解 Agent、Team 与 Workflow 的调用层级](topics/workflow/workflow-composition-hierarchy.md) |
| Team 怎样运行一个已注册 Workflow | [让 Team 成员调用已注册 Workflow](topics/workflow/team-run-workflow.md) |
| 成员能否自己生成 Python 工作流并在运行中启动 | [让成员编写并管理自己的子 Workflow](topics/workflow/member-authored-workflow.md) |
| 什么时候用 spawn_agent，什么时候用 run_workflow | [派发 Agent 任务并安排审阅](topics/team/dispatch-team-agents.md) |
| 怎样并行跑候选，再按检查结果只采用一个 | [组合子 Workflow 并采用候选结果](topics/workflow/compose-workflow-candidates.md) |
| Duo 的两个候选是否同时执行 | [使用 Duo 生成并选择双候选方案](topics/workflow/duo-workflow.md) |
| Weave 的检查和修复由谁定义 | [使用 Weave 分组执行并检查交付](topics/workflow/weave-workflow.md) |
| Reconstruct 怎样通过观察探索隐藏程序 | [使用 Reconstruct 从输入输出重建程序](topics/workflow/reconstruct-workflow.md) |
| 一个 Agent 失败会怎样影响其余步骤 | [处理 Workflow 的失败、重试与恢复](topics/workflow/workflow-failure-recovery-lifecycle.md) |
| 进程退出后能否自动从任意 Python 语句继续 | [处理 Workflow 的失败、重试与恢复](topics/workflow/workflow-failure-recovery-lifecycle.md) |
| 怎样在没有模型额度时验证自己写的流程 | [验证与调试自定义 Workflow](topics/workflow/workflow-testing-debugging.md) |
| 工作树能隔离进程、网络和宿主权限吗 | [隔离怎么选](topics/isolation/isolation-overview.md) |
| 几个 Agent 会自动共享对话吗 | [隔离怎么选](topics/isolation/isolation-overview.md) |
| docker-group 和 docker-session 的差别是什么 | [在容器中运行任务](topics/isolation/docker-isolation.md) |
| Agent 在 Docker 中运行时，编排代码在哪里执行 | [让任务在指定目录或现有容器里执行](topics/isolation/execution-environments.md) |
| 能否让任务使用现有容器或自定义执行服务 | [接入自己的执行环境](topics/isolation/custom-environment-providers.md) |
| 子 Agent 改好了，为什么主目录没有变化 | [采用队友改动并验证交付](topics/team/adopt-team-changes.md) |
| 用户取消以后，命令和容器是否已经清理完 | [中断任务与检查资源清理](topics/operations/cancellation-cleanup.md) |
| 模型报错以后，已经生成的改动去哪里找 | [找回隔离任务的改动并清理资源](topics/isolation/workspace-recovery.md) |
| Skill 与工具、Workflow 的关系是什么 | [使用与编写 Skill](topics/extensions/skills-on-demand.md) |
| 能否写一个业务工具，怎样传给模型 | [把自己的函数接成工具](topics/extensions/custom-tools.md) |
| PreToolUse Hook 可以阻止危险命令吗 | [在工具执行和生命周期中加入 Hook](topics/extensions/team-hooks.md) |
| 为什么运行完成还要检查测试证据 | [读取运行结果与判断任务是否完成](topics/observability/run-results.md) |
| 测试过后继续改文件，旧通过记录是否还适用 | [用实际测试验证 Agent 的交付](topics/observability/native-test-evidence.md) |
| trace、会话文件和用量账本有什么区别 | [找到运行记录、会话和交付文件](topics/observability/artifacts-and-history.md) |
| 缓存与 reasoning token 是否还要加到 total 上 | [统计 token、缓存命中和费用](topics/observability/token-cache-cost.md) |
| 如何检查团队实际的分工协作情况 | [检查团队是否真的按分工协作](topics/observability/collaboration-adherence.md) |
| OC 与 OCE 哪个负责正式 benchmark 评分 | [用 OCE 评测 OC 的 Agent、Team 和 Workflow](topics/observability/evaluation-boundary.md) |
| 怎样把 OC 加到 Web 服务或后台任务系统里 | [从示例接入自己的服务](topics/architecture/service-integration-lifecycle.md) |
| 版本升级后旧团队文件和旧示例怎样检查 | [升级已有安装、团队和 SDK 集成](topics/architecture/upgrade-existing-integration.md) |
| 遇到问题时应该带哪些材料来反馈 | [反馈问题、请求帮助与提交改进建议](topics/architecture/report-problem-and-request-help.md) |
