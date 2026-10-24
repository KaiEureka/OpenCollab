---
{
  "id": "extension-and-evaluation-boundaries",
  "title": "OpenCollab 负责什么以及如何扩展",
  "category": "architecture",
  "summary": "把业务需求放到合适的扩展位置，并理解 OpenCollab 与评测项目各自维护什么。",
  "aliases": [
    "OpenCollab-Eval",
    "ports",
    "adapters",
    "bootstrap",
    "LLMPort",
    "ToolPort",
    "EnvironmentPort",
    "workflow",
    "Skill"
  ],
  "keywords": [
    "架构",
    "扩展点",
    "OC 与 OCE"
  ],
  "questions": [
    "把业务需求放到合适的扩展位置，并理解 OpenCollab 与评测项目各自维护什么？"
  ],
  "related": [
    "runtime-execution-model",
    "custom-environment-providers",
    "service-integration-lifecycle"
  ],
  "verified_ref": "e7946a42"
}
---

# OpenCollab 负责什么以及如何扩展

OpenCollab 负责执行 Agent、Team 与 Workflow，组织会话、工具和交付。调用方提供任务目标、业务输入和验收方式。扩展时先判断需要增加一种操作、改变协作步骤，还是接入外部运行能力，再选择相应的公开入口。

## 业务目标与运行机制怎样分工

“修复某个仓库问题”需要任务输入、项目工作区和明确的验证命令。OpenCollab 提供模型交互、工具执行、预算、取消与产物记录。修复是否满足项目要求，应由项目测试和实际变更判断。若任务是调用业务服务，也应使用该服务的查询与状态确认机制检查结果。

这让调用方可以保留领域逻辑。例如一个工单处理服务可以通过自定义工具暴露查询能力，由 Workflow 规定何时提取工单、何时调用 Agent 和何时保存业务结果。模型能选择工具的范围由调用方显式给出。

## 选择合适的扩展入口

| 需求 | 适合的入口 | 由扩展者提供的内容 |
| --- | --- | --- |
| 查询或操作业务系统 | `opencollab.tools.Tool` | 参数 schema、实际执行和结果 |
| 固定顺序、分支与重试 | `@workflow` 与 `WorkflowContext` | 异步 Python 控制逻辑 |
| 成员角色和通信方向 | Team YAML | 提示词、工具、角色与拓扑 |
| 专用执行服务 | Environment 与 EnvironmentPool | 文件、命令、申请、交付与清理 |
| 按需说明材料 | 技能目录和工具 | 可加载指令与配套资料 |
| 模型传输或包装 | SDK 的模型客户端接入 | 符合模型调用协议的实现 |

只需要一项工单查询时，实现一个工具并传给 `agent(tools=[tool])` 即可。需要严格规定“查询、实施、测试、交付”的次序时，用 Workflow 把这些步骤写成 Python。若已有工具与 SDK 可以表达任务，优先沿用它们，避免把业务流程扩散到框架内部。

## 为什么核心通过接口使用外部能力

代码依赖方向为 `bootstrap → adapters → application → domain`。domain 保存纯状态和值对象，application 编写运行用例并依赖端口，adapters 实现模型、工具、环境和存储，bootstrap 组合这些实现。已有架构检查维护这项依赖关系。

扩展新的外部执行服务时，核心用例只需知道它能申请环境、读写文件和执行命令。具体连接、资源 ID 和清理行为留在适配实现。公开模块 `opencollab.tools`、`opencollab.environments` 和 `opencollab.workflows` 为集成保留较窄的依赖面，便于后续版本维护。

## OC 与 OCE 怎样配合

OpenCollab-Eval 维护 benchmark 数据、实验配置、运行与远端执行，调用 OpenCollab 的公开运行接口取得会话与交付记录。任务提示词、数据构造和正式评分需要按评测项目对应版本解释。OC 的 `completed` 表示运行完成，正式评测结果来自实际验证过程。

当前上下文计划已接入角色、团队、技能、派发任务和项目概览。通用记忆或 RAG 来源需要真实内容生产者和保留需求。环境提供器可以扩展专用后端，VM 后端的状态见[规划说明](../../reserved/vm-environment-extension.md)。已有实现和扩展设想应分别按源码与实现记录判断。

扩展原则见[仓库架构规则](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/AGENTS.md)和[包指南](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md)，环境接入见 [接入自己的执行环境](../isolation/custom-environment-providers.md)，业务服务集成见 [从示例接入自己的服务](service-integration-lifecycle.md)。
