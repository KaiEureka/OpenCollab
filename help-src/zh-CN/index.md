# OC 中文帮助中心

OpenCollab 中文帮助中心包含 69 篇指南，按使用目标组织安装、模型配置、团队协作、Workflow、执行环境与结果检查。文档依据 OC main 提交 [`e7946a42`](https://github.com/RISE-X-Lab/OpenCollab/tree/e7946a42be378e0bf6dc02e24f9ded9752ce2a41)，说明该版本的公开接口及运行行为。

[返回项目站主页](https://rise-x-lab.github.io/OpenCollab/) · [GitHub 源码](https://github.com/RISE-X-Lab/OpenCollab) · [阅读路径](reading-paths.md) · [新用户与技术问题](questions.md)

## 从你的目标开始

| 想做的事情 | 阅读入口 |
| --- | --- |
| 理解 OC 并开始第一次任务 | [先认识 Agent、Session、Team 和 Workflow](topics/orientation/concepts-and-modes.md) · [在自己的项目中完成第一次运行](topics/setup/first-run-workspace.md) |
| 编写一个新的 Workflow | [理解 Workflow 的编程模型与自由度](topics/workflow/workflow-programming-model.md) · [按开发任务查找 Workflow 公开 API](topics/workflow/workflow-public-api-map.md) · [判断 Workflow 能实现的范围与运行边界](topics/workflow/workflow-capabilities-boundaries.md) |
| 配置 Team 并派发子任务 | [Team 完整指南与团队配置](topics/team/configure-team.md) · [派发 Agent 任务并安排审阅](topics/team/dispatch-team-agents.md) |
| 选择隔离策略 | [隔离怎么选](topics/isolation/isolation-overview.md) · [在容器中运行任务](topics/isolation/docker-isolation.md) |
| 让成员运行子 Workflow | [让 Team 成员调用已注册 Workflow](topics/workflow/team-run-workflow.md) · [让成员编写并管理自己的子 Workflow](topics/workflow/member-authored-workflow.md) |

## 认识 OC

| 指南 | 解决的问题 |
| --- | --- |
| [先认识 Agent、Session、Team 和 Workflow](topics/orientation/concepts-and-modes.md) | 建立 OC 的使用概念，理解不同对象之间的关系并找到下一篇指南。 |
| [按任务选择默认 CLI、Base、Team 或 Workflow](topics/orientation/choose-working-mode.md) | 根据交互需求、可重复流程和协作方式选择最合适的启动入口。 |

## 安装与开始使用

| 指南 | 解决的问题 |
| --- | --- |
| [安装前提与版本选择](topics/setup/install-source-checkout.md) | 选定适合自己的发布版本或当前源码，准备能够启动 OpenCollab 的 Python 环境。 |
| [在自己的项目中完成第一次运行](topics/setup/first-run-workspace.md) | 选好项目目录，接通模型，并完成一次可观察的终端任务。 |
| [默认终端交互与命令参数位置](topics/setup/interactive-cli-controls.md) | 在终端给指定 agent 发后续任务，查看团队状态，并正确组织根命令与子命令参数。 |
| [从 Python 调用 OpenCollab](topics/setup/python-sdk-entrypoints.md) | 在现有 Python 程序中构造客户端，调用单 agent、团队或工作流，并接入程序的结果处理。 |
| [使用 Base 单 Agent 与 Single2 配置](topics/setup/base-single2.md) | 直接运行一个 Agent，理解 Base 默认配置并按任务选择工具和执行环境。 |

## 模型接入与配置

| 指南 | 解决的问题 |
| --- | --- |
| [配置从哪里读取以及哪一项生效](topics/models/configuration-precedence.md) | 解释某项配置为什么生效，并稳定地让 CLI 或 SDK 读取选定配置。 |
| [配置参数与环境变量速查](topics/models/configuration-reference.md) | 按环境变量、SDK 字段和运行入口查找默认值与详细用法。 |
| [接入模型端点与认证凭据](topics/models/provider-endpoint-credentials.md) | 将 OpenAI 兼容或原生 Anthropic 端点接入 OpenCollab，并选对模型和认证方式。 |
| [逻辑模型路由与共享请求容量](topics/models/model-routing-request-capacity.md) | 让不同角色或进程选择明确后端，并协调同一台机器上的模型请求容量。 |
| [选择调用协议与流式响应](topics/models/wire-protocol-streaming.md) | 为端点选择正确的 Chat Completions、Responses 或 Anthropic Messages 路径，并配置流式接收。 |
| [配置深度思考与采样参数](topics/models/thinking-reasoning-sampling.md) | 按当前 provider、协议和模型能力选择 thinking、reasoning effort 与采样设置。 |

## 执行环境与隔离

| 指南 | 解决的问题 |
| --- | --- |
| [隔离怎么选](topics/isolation/isolation-overview.md) | 根据任务共享文件、运行代码和联网的需求选择合适的隔离策略。 |
| [默认隔离与单次覆盖怎么配](topics/isolation/isolation-configuration.md) | 让 Team 角色和 Workflow 调用使用预期的执行环境。 |
| [多个任务怎样在独立工作区改代码](topics/isolation/worktree-workspaces.md) | 并行修改同一个项目时保留清晰的文件起点并理解结果如何返回。 |
| [在容器中运行任务](topics/isolation/docker-isolation.md) | 准备可执行的容器环境并按任务需求设置共享范围、网络和资源。 |
| [让任务在指定目录或现有容器里执行](topics/isolation/execution-environments.md) | 将模型的文件与命令操作连接到目标工作区，并掌握环境资源的所有权。 |
| [接入自己的执行环境](topics/isolation/custom-environment-providers.md) | 使用已有环境提供器扩展隔离策略并让资源分配、交付和清理保持一致。 |
| [把候选结果采纳到交付目录](topics/isolation/candidate-delivery.md) | 找到 Agent 的实际改动，选择要保留的候选，并在交付目录验证结果。 |
| [找回隔离任务的改动并清理资源](topics/isolation/workspace-recovery.md) | 在取消、输出截断或清理失败后找到完整改动，并继续处理属于该运行的资源。 |

## Team 与子 Agent

| 指南 | 解决的问题 |
| --- | --- |
| [Team 完整指南与团队配置](topics/team/configure-team.md) | 创建可运行的 team.yaml，为每个角色设置职责、工具、模型和允许交接的对象。 |
| [派发 Agent 任务并安排审阅](topics/team/dispatch-team-agents.md) | 向合适角色派发清晰任务，理解并行子任务和带审阅的执行过程。 |
| [向队友发消息并继续协作](topics/team/message-team-agents.md) | 找到已有成员并发送补充信息、修改要求或协作回复。 |
| [等待队友结果并读懂团队状态](topics/team/wait-for-team-results.md) | 区分派发结果、消息回复和团队结束条件，采用适合当前交接方式的等待操作。 |
| [采用队友改动并验证交付](topics/team/adopt-team-changes.md) | 将已完成 Coder 的改动交付到团队工作区，再让新的 Tester 验证采用后的结果。 |

## Workflow 与子 Workflow

| 指南 | 解决的问题 |
| --- | --- |
| [Workflow 完整指南与运行入口](topics/workflow/run-registered-workflow.md) | 从 CLI 或 Python SDK 选择内置与工作区工作流，传入任务并获取运行结果。 |
| [理解 Workflow 的编程模型与自由度](topics/workflow/workflow-programming-model.md) | 明确普通 Python 代码与 ctx 操作各自承担的职责，设计由运行结果驱动的协作流程。 |
| [判断 Workflow 能实现的范围与运行边界](topics/workflow/workflow-capabilities-boundaries.md) | 评估流程需求需要普通 Python、ctx 内置能力、环境扩展还是调用方服务支持。 |
| [编写第一个自定义 Workflow](topics/workflow/author-custom-workflow.md) | 通过公开的 Python 接口编写一个可发现、可运行并返回结果的协作流程。 |
| [按开发任务查找 Workflow 公开 API](topics/workflow/workflow-public-api-map.md) | 从需要完成的开发动作定位公开入口、上下文操作与可插拔组件。 |
| [选择 Workflow 的入口与控制粒度](topics/workflow/workflow-control-granularity.md) | 按需要选择 CLI、SDK、ctx 单次调用与回调参数，理解每层可控制的具体行为。 |
| [设计 Workflow 的输入输出与步骤间数据流](topics/workflow/workflow-dataflow-structured-io.md) | 清楚传递任务输入、Agent 输出、结构化数据和文件产物，形成可检查的最终结果。 |
| [在 Workflow 中调用 Agent 并处理结果](topics/workflow/workflow-agent-calls.md) | 为工作流中的每个角色建立会话，选择文本或结构化输出并处理预算、停止与失败。 |
| [在 Workflow 中并行处理多项任务](topics/workflow/workflow-parallel-pipeline.md) | 使用 parallel 或 pipeline 组织多项工作，同时限制活跃任务与 Agent 会话数量。 |
| [理解 Agent、Team 与 Workflow 的调用层级](topics/workflow/workflow-composition-hierarchy.md) | 根据所需的成员自治或程序控制选择调用层级，并为组合明确预算、环境和结果所有者。 |
| [组合子 Workflow 并采用候选结果](topics/workflow/compose-workflow-candidates.md) | 把已有异步流程组合成更大的流程，按需要在独立候选环境中运行并采用其改动。 |
| [处理 Workflow 的失败、重试与恢复](topics/workflow/workflow-failure-recovery-lifecycle.md) | 明确各层失败如何返回或传播，在重试与继续执行前检查会话、工作区和产物状态。 |
| [验证与调试自定义 Workflow](topics/workflow/workflow-testing-debugging.md) | 用可观察的最小运行核对控制流、数据传递、工具执行和最终任务结果。 |
| [让 Team 成员调用已注册 Workflow](topics/workflow/team-run-workflow.md) | 给指定角色授予 run_workflow，让成员在自己的执行环境中运行注册工作流并获得结果。 |
| [让成员编写并管理自己的子 Workflow](topics/workflow/member-authored-workflow.md) | 让 Team 成员写出 Python 文件并启动子运行，再检查进展、等待结果或停止运行。 |
| [使用 Duo 生成并选择双候选方案](topics/workflow/duo-workflow.md) | 运行内置 Duo，理解两个候选的执行、证据比较与最终采用结果。 |
| [使用 Weave 分组执行并检查交付](topics/workflow/weave-workflow.md) | 把任务组织为有依赖的工作组，经过实际检查和有限修复后读取交付结果。 |
| [使用 Reconstruct 从输入输出重建程序](topics/workflow/reconstruct-workflow.md) | 绑定可查询的隐藏程序与候选执行器，通过主动探索和反馈迭代得到候选程序。 |

## 预算、上下文与运行恢复

| 指南 | 解决的问题 |
| --- | --- |
| [上下文窗口与自动压缩](topics/operations/context-compaction.md) | 理解长任务何时压缩上下文，如何调整有效窗口，并定位上下文溢出后的停止。 |
| [设置 token 预算、步数与运行并发](topics/operations/token-step-concurrency-limits.md) | 为单 agent、团队和工作流设置合适的执行额度，并解释预算拒绝、等待与停止。 |
| [超时、重试与请求卡住诊断](topics/operations/timeouts-retries.md) | 按卡住的位置调整模型请求或整次任务的时间限制，并理解重试如何停止。 |
| [保存会话与恢复历史](topics/operations/session-save-resume.md) | 找到已保存的会话，恢复终端主会话历史，并了解恢复后预算和执行状态如何延续。 |
| [中断任务与检查资源清理](topics/operations/cancellation-cleanup.md) | 从终端或 Python 中停止正在运行的任务，并确认部分工作、用量和环境的最终状态。 |
| [检查有效配置与定位常见启动故障](topics/operations/configuration-diagnostics.md) | 用可核对的信息定位配置、模型能力、CLI 参数和端点连接中的常见问题。 |

## 工具、Skill 与扩展

| 指南 | 解决的问题 |
| --- | --- |
| [选择 Agent 能使用的工具](topics/extensions/tool-selection.md) | 按任务给 Agent 配置文件、命令、协作或技能工具并理解权限边界。 |
| [把自己的函数接成工具](topics/extensions/custom-tools.md) | 通过公开 Tool 接口加入业务能力并让 Agent 在正确环境中调用。 |
| [使用与编写 Skill](topics/extensions/skills-on-demand.md) | 给角色增加按需加载的操作说明，并知道技能内容何时进入会话。 |
| [在工具执行和生命周期中加入 Hook](topics/extensions/team-hooks.md) | 在规定的运行事件上执行自定义命令，并理解命令在哪个环境运行。 |
| [安全地查找读取和修改文件](topics/extensions/file-editing-safe-workspace.md) | 在 Agent 实际工作区内定位文件、准确编辑内容并理解路径或覆盖错误。 |
| [管理终端权限并确认命令结果](topics/extensions/shell-permissions-execution.md) | 让必要命令真正运行，理解确认提示、超时和截断，并据实际输出判断结果。 |

## 结果、轨迹与评测

| 指南 | 解决的问题 |
| --- | --- |
| [读取运行结果与判断任务是否完成](topics/observability/run-results.md) | 正确处理 RunResult 和 RunError，把框架完成状态与实际任务验证分开理解。 |
| [找到运行记录、会话和交付文件](topics/observability/artifacts-and-history.md) | 运行结束后定位对应目录，区分产物、原始会话、轨迹和可再生缓存。 |
| [查看 Agent 做了什么](topics/observability/trace-and-events.md) | 通过轨迹和事件定位模型调用、工具执行、交接与停止原因。 |
| [统计 token、缓存命中和费用](topics/observability/token-cache-cost.md) | 从实际用量记录计算开销，理解推理、缓存和重试对总量的影响。 |
| [用实际测试验证 Agent 的交付](topics/observability/native-test-evidence.md) | 让工作流获得可执行的测试证据，读懂通过、未验证和过期记录。 |
| [检查团队是否真的按分工协作](topics/observability/collaboration-adherence.md) | 读懂声明的团队与实际运行轨迹之间的差异，并找到分析入口。 |
| [用 OCE 评测 OC 的 Agent、Team 和 Workflow](topics/observability/evaluation-boundary.md) | 理解 OC 与 OCE 的分工，找到正式运行和收集评分结果的入口。 |

## 架构、集成与维护

| 指南 | 解决的问题 |
| --- | --- |
| [一次任务如何从输入走到工具执行](topics/architecture/runtime-execution-model.md) | 理解模型、Session、调度器和执行环境怎样合作，并找到实际行为由谁控制。 |
| [Session 如何保存进度并继续执行](topics/architecture/session-state-lifecycle.md) | 读懂会话状态、停止原因和恢复方式，判断继续运行会从哪里开始。 |
| [OpenCollab 负责什么以及如何扩展](topics/architecture/extension-and-evaluation-boundaries.md) | 把业务需求放到合适的扩展位置，并理解 OpenCollab 与评测项目各自维护什么。 |
| [从示例接入自己的服务](topics/architecture/service-integration-lifecycle.md) | 把一次 SDK 演示接入业务服务，并明确运行身份、结果处理与资源清理责任。 |
| [升级已有安装、团队和 SDK 集成](topics/architecture/upgrade-existing-integration.md) | 从已有版本迁移到选定版本，核对默认行为和公开接口，并验证原有任务继续按预期运行。 |
| [反馈问题、请求帮助与提交改进建议](topics/architecture/report-problem-and-request-help.md) | 选择正确反馈入口，用最小复现和必要记录让维护者理解遇到的问题。 |
