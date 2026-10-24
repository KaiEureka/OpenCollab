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

业务逻辑由调用方维护。例如一个工单处理服务可以通过自定义工具暴露查询能力，由 Workflow 规定何时提取工单、何时调用 Agent 和何时保存业务结果。模型能选择工具的范围由调用方显式给出。

## 选择合适的扩展入口

| 需求 | 适合的入口 | 由扩展者提供的内容 |
| --- | --- | --- |
| 查询或操作业务系统 | `opencollab.tools.Tool` | 参数 schema、实际执行和结果 |
| 固定顺序、分支与重试 | `@workflow` 与 `WorkflowContext` | 异步 Python 控制逻辑 |
| 成员角色和通信方向 | Team YAML | 提示词、工具、角色与拓扑 |
| 专用执行服务 | Environment 与 EnvironmentPool | 文件、命令、申请、交付与清理 |
| 按需说明材料 | 技能目录和工具 | 可加载指令与配套资料 |
| 模型传输或包装 | SDK 的模型客户端接入 | 符合模型调用协议的实现 |

只需要一项工单查询时，实现一个工具并传给 `agent(tools=[tool])` 即可。需要严格规定“查询、实施、测试、交付”的次序时，用 Workflow 把这些步骤写成 Python。已有工具与 SDK 能表达任务时，直接在应用中组合即可。

## 为什么核心通过接口使用外部能力

代码依赖方向为 `bootstrap → adapters → application → domain`。domain 保存纯状态和值对象，application 编写运行用例并依赖端口，adapters 实现模型、工具、环境和存储，bootstrap 组合这些实现。

扩展新的外部执行服务时，核心用例只需知道它能申请环境、读写文件和执行命令。具体连接、资源 ID 和清理行为留在适配实现。集成代码可以从公开模块 `opencollab.tools`、`opencollab.environments` 和 `opencollab.workflows` 导入所需能力，便于跟随包版本维护。

## 离线读取与分析从哪里接入

普通应用从包根导入 `OpenCollab`、`RunResult`、`RunError`、`workflow`、`RunControl`、`RunEvent`、`BudgetSnapshot` 和 `BudgetDecision`。按所需能力选择公开模块，升级时再核对对应版本的接口。

| 需要读取或复用的能力 | 公开模块或资料 | 实用入口 |
| --- | --- | --- |
| 团队声明与上下文策略 | `opencollab.teams` | [读取角色、工具、额度和 profile](../observability/collaboration-adherence.md) |
| 原生测试结果与 Git 补丁路径 | `opencollab.tools`、`opencollab.patches` | [接入测试输出并检查补丁文件](../observability/native-test-evidence.md) |
| 模型能力与有效 profile | `opencollab.models`、`opencollab.profiles` | [模型检查指南](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/model-inspection.md) |
| 已安装协作流程 | `opencollab.builtin_workflows` | [运行已注册工作流](../workflow/run-registered-workflow.md) |
| 单次运行的过程与开销 | trace、`RunEvent`、提供方用量账本 | [执行轨迹](../observability/trace-and-events.md)和[用量统计](../observability/token-cache-cost.md) |

这些读取入口可以在本地检查配置与已保存材料。运行任务时继续使用 Agent、Team 或 Workflow，研究批次的尝试选择和统计报告从 OCE 的实验工具进入。

## OC 与 OCE 怎样配合

OpenCollab-Eval 维护 benchmark 数据、实验配置、运行与远端执行，调用 OpenCollab 的公开运行接口取得会话与交付记录。任务提示词、数据构造和正式评分需要按评测项目对应版本解释。OC 的 `completed` 表示运行完成，正式评测结果来自实际验证过程。

研究结果的入口包括 [benchmark 结果说明](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/results.md)与 [Adherence 说明](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/adherence.md)。README 图表由 `docs/benchmark-results.json` 经 `scripts/generate_benchmark_assets.py` 生成，数据对应已记录实验。要分析自己的新运行，沿 [OCE 实验工具](https://github.com/RISE-X-Lab/OpenCollab-Eval/blob/main/src/opencollab_eval/experiment/README.md)和[原始研究材料](https://github.com/RISE-X-Lab/OpenCollab-Eval/blob/main/experiment/README.md)选择适用的报告方式，保留任务、模型、角色、工具、预算、上下文与实际验证结果。

当前上下文来源包括角色、团队、技能、派发任务和项目概览。通用记忆或 RAG 来源需要自行接入，并确定内容来源与保留方式。环境提供器可以扩展专用后端，VM 后端的规划见[相关说明](../../reserved/vm-environment-extension.md)。

扩展原则见[仓库架构规则](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/AGENTS.md)和[包指南](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md)，环境接入见 [接入自己的执行环境](../isolation/custom-environment-providers.md)，业务服务集成见 [从示例接入自己的服务](service-integration-lifecycle.md)。
