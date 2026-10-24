---
{
  "id": "workflow-capabilities-boundaries",
  "title": "判断 Workflow 能实现的范围与运行边界",
  "category": "workflow",
  "summary": "评估流程需求需要普通 Python、ctx 内置能力、环境扩展还是调用方服务支持。",
  "aliases": [
    "任意 Python 都能用吗",
    "能不能写 DAG",
    "支持分布式多机吗",
    "有没有自动断点恢复",
    "Docker 隔离到哪里",
    "能在运行时生成代码吗"
  ],
  "keywords": [
    "Workflow 能力范围",
    "执行边界",
    "Python 编排",
    "环境扩展"
  ],
  "questions": [
    "评估流程需求需要普通 Python、ctx 内置能力、环境扩展还是调用方服务支持？"
  ],
  "related": [
    "workflow-programming-model",
    "workflow-composition-hierarchy",
    "workflow-failure-recovery-lifecycle",
    "member-authored-workflow"
  ],
  "verified_ref": "e7946a42"
}
---

# 判断 Workflow 能实现的范围与运行边界

协作想法可以先拆成三个问题。控制顺序怎样表达，模型和工具怎样执行，运行外的业务状态由谁管理。普通 Python 能表达分支、循环、函数组合和动态依赖，OpenCollab 的 `ctx` 管理会话、容量、额度与候选环境，应用服务负责自己的数据库、外部系统和跨主机协调。确定每部分由谁负责后，就可以选择对应的接口。

## 任意想法怎样变成程序

只要能用当前解释器、依赖和权限实现，Workflow 函数就能导入模块、调用业务函数、处理数据，再按输入或中间结果创建后续步骤。一个探索流程可以先观察资料，再判断缺少哪些信息，动态生成任务列表，最后汇总结果。分支用 `if`，有限迭代用 `for`，反馈循环用 `while`，这些语法由 Python 提供。

设计流程时，先写出输入、操作和停止条件。工作流代码读取模型结果，决定下一步操作，并通过文件、工具回执和实际检查确认结果。较长阻塞操作应交给异步库、线程或应用拥有的独立进程，以便保留事件循环的并发与取消响应。

## 当前能力矩阵

| 能力 | 支持方式 | 使用位置与约束 |
| --- | --- | --- |
| 条件分支 | Python 原生 | 依据输入或结构化结果写 if、elif、match |
| 循环与反馈 | Python 原生 | for、while，明确轮数、资源与终止条件 |
| 普通模块和业务逻辑 | Python 原生 | 导入当前环境已有库，遵循解释器权限 |
| 动态生成任务 | Python 原生 | 根据输入和反馈构造异步任务函数 |
| 独立任务并行 | OC 内置 | ctx.parallel，共享集合与会话容量 |
| 逐项多阶段 | OC 内置 | ctx.pipeline，阶段顺序按输入分别推进 |
| 依赖图和 DAG | Python 或 Weave 分组 | 自建依赖执行逻辑，或使用 Weave 分组规划 |
| 结构化 Agent 输出 | OC 内置 | ctx.agent 的 schema 捕获与校验 |
| 子函数和子流程 | Python 与候选接口 | 共享 ctx，或 candidate_workflow 建立候选环境 |
| Team 调用注册流程 | OC 内置 | run_workflow 等待完整结果，角色显式获权 |
| 成员运行生成的源文件 | OC 内置 | manage_workflow，创建成员拥有子进程 |
| Workflow 组合独立 Team | 宿主 SDK | 显式调用 OpenCollab.team，连接资源与取消 |
| 内置和自定义工具 | OC 扩展 | builtin_tools、公开 Tool 和 VerificationTool |
| 工作区与容器策略 | OC 环境策略 | shared、worktree、docker-group、docker-session 或自定义 provider |
| 业务失败重试 | Python 与传输层各自处理 | 步骤按副作用重试，模型客户端处理分类后的传输重试 |
| 调试与交付验证 | OC 观察和应用检查 | phase、log、label、trace、实际工具及验证器 |
| 通用跨进程自动重放 | 调用方设计 | 保存完成步骤和业务状态，重建依赖并继续 |
| 分布式多主机调度 | 调用方服务 | 任务队列、远端资源、跨主机状态与结果协调 |

## 动态图与组合深度

Workflow 可以自行维护节点表、完成结果和依赖集合，选择就绪节点交给 `parallel`，再把结果送入下一批。通用图的节点语义、失败传播和停止条件由作者定义。Weave 已提供分组依赖规划，依赖环中的组会合入同一个会话，适合采用其现成分组协议的任务。

普通异步子函数可以按业务拆分并共享 `ctx`。候选子流程建立独立候选环境，继续共享父会话容量、集合容量与绝对期限。当前默认候选子上下文未注入另一层候选后端，子流程内部再次建立候选需要集成提供相应能力。子函数适合继续拆分逻辑，候选后端负责额外环境的分配与交付。

## 代码与工具分别在哪里执行

| 执行方式 | Python 编排位置 | Agent 工具位置 |
| --- | --- | --- |
| 宿主 SDK 加 shared | 调用方进程 | 借用当前环境和目录 |
| 宿主 SDK 加 worktree | 调用方进程 | 父环境中的独立 checkout |
| 宿主 SDK 加 docker-group | 调用方进程 | 本次策略运行的容器，各会话私有工作区 |
| 宿主 SDK 加 docker-session | 调用方进程 | 每会话独立容器与工作区 |
| manage_workflow 源文件 | 成员环境内的 Python 子进程 | 按继承或单次选择的策略执行 |

worktree 共享操作系统和 Git 对象存储，容器环境中的 worktree 仍位于该容器。Docker 策略需要可用 Docker daemon 和合适镜像，默认网络为 none，挂载和网络按 provider 配置。宿主 Python 直接使用 `Path` 或 `subprocess` 时，行为仍取决于宿主进程的位置，Agent 的 Docker 配置主要决定工具环境。

自定义 provider 工厂每次为运行和策略创建 `EnvironmentPool`，负责 acquire、release 与 adopt。自定义 provider 同样遵循工具权限、工作区检查与输出限制。全环境候选后端还可决定活服务和文件如何一起交付。

## 运行时源文件与业务服务

Team 成员可以用编辑工具生成 Python 文件，再通过 `manage_workflow launch` 在自己的环境启动。成员需要 bash 权限、安装了 OpenCollab 的 Python，以及其子会话要用的工具授权。启动立即返回 id，创建者随后查询、等待和停止，父成员结束时清理剩余子运行。

普通 SDK Workflow 的并发参数管理本次运行中的会话和集合任务。多主机队列、远端 worker 注册、全局状态和跨系统资源所有权需要应用组件连接。保存的 `workflow.json`、会话和轨迹可追溯执行，通用控制流继续执行所需的状态与副作用处理则由应用设计。Weave 的显式状态继续可作为现有、范围明确的实现参考。

## 验证一个方案是否成立

用一个小任务运行工作流，查看每次 Agent 调用和工具执行，再检查生成的文件、服务行为或返回值。`completed` 表示工作流入口已完成，任务结果由项目测试或应用验证器检查。根据检查结果，决定下一步需要调整流程代码、增加 ctx 调用，还是接入应用服务。

## 源码依据

[opencollab/sdk/client.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/client.py#L408)、[docs/session-isolation.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/session-isolation.md#L5)、[examples/member-authored-workflow/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/examples/member-authored-workflow/README.md#L3)。

## 相关章节

[理解 Workflow 的编程模型与自由度](workflow-programming-model.md)、[理解 Agent、Team 与 Workflow 的调用层级](workflow-composition-hierarchy.md)、[处理 Workflow 的失败、重试与恢复](workflow-failure-recovery-lifecycle.md)、[让成员编写并管理自己的子 Workflow](member-authored-workflow.md)。
