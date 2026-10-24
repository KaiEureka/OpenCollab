---
{
  "id": "workflow-public-api-map",
  "title": "按开发任务查找 Workflow 公开 API",
  "category": "workflow",
  "summary": "从需要完成的开发动作定位公开入口、上下文操作与可插拔组件。",
  "aliases": [
    "我要用哪个接口",
    "完整 Workflow 开发接口在哪",
    "ctx 有哪些方法",
    "哪些模块可以直接 import",
    "如何扩展自己的工具"
  ],
  "keywords": [
    "Workflow API",
    "公开接口",
    "opencollab.workflows",
    "能力导航"
  ],
  "questions": [
    "从需要完成的开发动作定位公开入口、上下文操作与可插拔组件？"
  ],
  "related": [
    "author-custom-workflow",
    "workflow-agent-calls",
    "workflow-parallel-pipeline",
    "compose-workflow-candidates",
    "workflow-testing-debugging"
  ],
  "verified_ref": "e7946a42"
}
---

# 按开发任务查找 Workflow 公开 API

公开入口从 `OpenCollab.workflow` 开始，异步函数收到的 `ctx` 提供会话、集合、候选、验证和观察方法。下面按实际用途列出当前接口，便于把一个任务映射到可调用的方法。常规开发可从简洁 `ctx.agent` 起步，需要逐次控制停止与清理时采用 `agent_run`。

## 启动一次运行

```python
from opencollab import OpenCollab

async def simple_flow(ctx, args):
    return await ctx.agent(args['goal'], tools=[], label='answer')

async def run_example(workspace):
    result = await OpenCollab(workspace).workflow(
        simple_flow, {'goal': '解释一次普通函数调用的执行顺序'},
        budget=10000, concurrency=2, task_concurrency=2,
        timeout=120, max_steps=20, cleanup_timeout=2.0,
        trace=True, limit_mode='explicit', isolation='shared',
    )
    return result.raise_for_status().output
```

`flow` 接受注册名称、异步函数或 `WorkflowSpec`，`inputs` 为字符串键 Mapping。装饰器从 `opencollab` 或 `opencollab.workflows` 导入，参数为 `name`、`description`、`phases`。函数返回值成为 `RunResult.output`。

| SDK 参数 | 默认或要求 | 作用 |
| --- | --- | --- |
| `budget` | 默认读取配置 | 工作流共享 token 额度 |
| `concurrency` | 4 | 活跃 Agent 会话容量 |
| `task_concurrency` | 默认等于 concurrency | 集合单元容量，覆盖候选子流程 |
| `timeout` | 默认无运行时限 | 整次运行期限 |
| `max_steps` | 100，可为 None | 各角色默认步数上限 |
| `system_prompt` | 可选非空文字 | 工作流角色默认系统提示配置 |
| `agent_profile` | 可选 | 选择共享 Agent 实现配置 |
| `cleanup_timeout` | 2.0，有限正数 | 拥有任务的清理等待 |
| `artifacts` | 可选路径 | 新目录或空目录中的持久产物 |
| `trace` | true | 保存编排观察记录 |
| `candidate_workspace` | 可选后端对象 | 注入候选分配、捕获和采用方式 |
| `run_id` | 默认生成 | 对应清单、会话与轨迹 |
| `llm` | 可选协议兼容对象 | 注入模型传输或测试替身 |
| `limit_mode` | `environment` | 可选 `explicit`，控制是否读取无限额度环境设置 |
| `isolation` | 可选策略名 | 默认 Agent 环境策略 |

`OpenCollab` 构造器的 `workspace` 指定工作目录，`environment` 可传入调用方拥有的执行环境，`environment_providers` 注册策略工厂。模型、provider 和普通配置也在构造器选择。SDK 与 CLI 参数覆盖范围各有入口定义，CLI 可通过 `workflow run NAME --help` 查看。

## 会话操作

| 方法 | 公开参数 | 返回值 |
| --- | --- | --- |
| `agent(prompt, ...)` | `schema`、`label`、`tools`、`isolation`、`budget`、`timeout`、`tool_choice`、`thinking`、`over_budget_ok` | 文本、校验后字典或 None |
| `agent_run(prompt, ...)` | `label`、`tools`、`isolation`、`budget`、`timeout`、`max_steps`、`system_prompt`、`run_control`、`cleanup_timeout` | `WorkflowAgentResult` |
| `draft_findings(prompt, ...)` | `label`、`budget` | 证据草稿字典或 None |

`agent` 的 `tools` 采用工具对象序列，运行实现也承接已配置的工具组选择。`agent_run` 支持工具组名称或对象序列。`isolation` 的运行实现接受布尔值、策略字符串或省略值，省略继承当前策略，`True` 选 worktree，`False` 选 shared。公开 Protocol 当前仍保留较窄的注解，开发时可结合实现与 SDK 用例核对。

`schema` 应提供对象形状的 JSON Schema，捕获失败会作一次纠正尝试。`draft_findings` 用于探索前收集已有证据草稿，后续仍需实际探索和验证。自由文本路径的 `tool_choice` 和 `thinking` 按调用配置模型行为。

`WorkflowAgentResult` 包含 `output`、`status`、`reason`、`tokens`、`steps`、`session_id`、`hard_budget_tokens`、`soft_budget_tokens`、`cleanup_complete`、`workspace_ready` 和 `observation_errors`。清理完成后才能把用量视为稳定结果，依赖同一环境的后续步骤还应读取 `workspace_ready`。

## 集合、组合与候选

| 方法 | 输入 | 输出与行为 |
| --- | --- | --- |
| `parallel(thunks)` | 无参异步任务函数序列 | 按输入顺序返回列表 |
| `pipeline(items, *stages, stop_on_none=True)` | 输入序列及逐项阶段 | 阶段收到前值、原输入、索引 |
| 普通子函数 | `await subflow(ctx, args)` | 共享 ctx 与资源，返回自定义值 |
| `candidate_agent(prompt, ...)` | 必填 `label`，可配 `tools`、`budget`、`timeout`、`tool_choice`、`thinking` | 单会话 `CandidateRun` |
| `candidate_workflow(workflow_fn, args, ...)` | 异步函数、字典、必填 label、可选 budget | 整个子流程的 `CandidateRun` |
| `adopt_candidate(candidate, preserve_paths=())` | 候选对象与保留路径 | 完成采用后返回 None |

当前 `pipeline` 在 `workflow_collections.py` 实现，SDK 嵌套集合测试覆盖实际调用，公开 Protocol 尚漏列该方法。集合普通异常转为单元 None，预算耗尽、环境撤销和取消按对应层级继续传播。

`CandidateRun` 保存 `label`、`output`、`diff`、`test_records`、`verified_targets`、`lifecycle_errors`、`source_revision`、`delivery_diff`、`delivery_paths`、`delivery_source_diff`，并提供 `effective_diff` 属性。候选执行、选择和采用是可分别观察的步骤。当前候选子流程自身未注入新的候选后端，继续复用普通子函数可保持资源关系清晰。

## 工作区、验证与观察

| 方法或属性 | 输入与返回 | 用途 |
| --- | --- | --- |
| `diff(exclude_paths=())` | 改动文本或 None | 读取可观察差异 |
| `source_changed(exclude_paths=())` | True、False 或 None | 探测路径排除后的源变化 |
| `execute_verification(tool, params)` | 验证工具、参数 Mapping，返回字符串 | 在绑定环境实际执行验证工具 |
| `phase(title)` | 异步，返回 None | 标记当前阶段 |
| `log(message)` | 异步，返回 None | 写流程日志 |
| `tokens_spent()` | 整数 | 已观察 token 消耗 |
| `tokens_remaining()` | 余额，无上限时为无穷 | 决定后续工作规模 |
| `seconds_left()` | 秒数，无期限时为无穷 | 读取剩余绝对期限 |
| `time_low()` | 布尔值 | 是否进入运行预留时间范围 |
| `workspace_root`、`host_workspace` | 路径字符串或 None | 定位绑定环境与宿主工作区 |
| `run_id` | 字符串或 None | 关联一次运行的记录 |

探测的 None 表示信息暂不可得，业务代码应保留这一状态。文件路径在工具环境与宿主 Python 中可能不同，实际文件操作先依据执行位置选择入口。

## 工具、环境和结果类型

`opencollab.tools` 导出 `Tool`、`VerificationTool`、`builtin_tools`、`evidence_tools` 及证据辅助函数。内置工具名称为 `bash`、`file_read`、`file_write`、`apply_patch`、`git_diff`、`grep` 和 `submit`，可按角色组合。`VerificationTool` 额外提供 `verified_targets`，验证应通过真实工具执行获得。

查询默认配置时，`profile_tool_names(profile)` 返回工具名元组，`profile_tool_limits(profile)` 返回独立的上限字典。`BuiltinToolName` 是公开工具名类型。`BashEvidence` 包装 Bash 并保留真实测试记录，`has_pass_evidence(returncode, output, runner=..., target=...)` 解析相应 runner 的通过证据。可配置输出字段与完整构造例子见[选择 Agent 能使用的工具](../extensions/tool-selection.md)。自定义业务操作见[把自己的函数接成工具](../extensions/custom-tools.md)。

`opencollab.environments` 导出环境协议与 Docker 配置。自定义 `EnvironmentPool` 提供 `acquire`、`release_env`、`release` 和 `adopt`，其工厂按运行及策略创建资源。`opencollab.builtin_workflows` 导出 Duo、Weave、Reconstruct，以及 Weave 适配器、状态和 Reconstruct 绑定类型。

`get_builtin_workflows()` 返回包含 `duo`、`weave`、`reconstruct` 的新注册表，可用 `list_specs()` 读取名称、说明和阶段。Duo 的 `run_dual_coder` 与 `CONTRACT_PROMPT` 供应用定制角色及仲裁，示例见[Duo 双候选方案](duo-workflow.md)。Weave 的 `WeaveGroup`、`WeaveCheck`、`WeaveConfig`、`WeaveState`、`WeaveAdapter`、`plan_weave_groups` 和 `run_weave` 用于自定义分组、验证及状态继续。Reconstruct 的 `ReconstructionTask` 与 `register_task` 连接隐藏程序和候选执行器。

最终 `RunResult` 的字段为 `output`、`status`、`reason`、`tokens`、`artifacts`、`error`、`metrics`、`agent_failures`。`ok` 对应 completed，`raise_for_status()` 可转成带结果的 `RunError`。`RunControl`、`BudgetSnapshot`、`BudgetDecision` 和 `RunEvent` 从 `opencollab` 导入，用于单次会话预算策略和观察。

`opencollab.sdk` 与顶层 `opencollab` 提供同一组八个公开入口，即 `OpenCollab`、`workflow`、`RunResult`、`RunError`、`RunControl`、`BudgetSnapshot`、`BudgetDecision` 和 `RunEvent`。`opencollab.workflows` 的公开导出是 `workflow`、`WorkflowContext`、`WorkflowAgentResult` 与 `CandidateRun`。普通函数可以直接交给 SDK，按名称发现的工作流则通过 `@workflow` 注册。

评测集成采用上述 SDK、环境和验证工具入口。[配置评测运行时](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md) 是这些入口的使用文档。数据集加载、隐藏测试和正式评分由外部评测包负责，完整说明见[区分任务交付检查与正式评测](../observability/evaluation-boundary.md)。

## 源码依据

[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md#L96)、[opencollab/workflows.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/workflows.py#L14)、[opencollab/application/workflow_collections.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/workflow_collections.py#L253)。

[opencollab/sdk/client.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/client.py)、[opencollab/application/workflow_agent_run.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/workflow_agent_run.py)、[opencollab/tools.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/tools.py)。

## 相关章节

[编写第一个自定义 Workflow](author-custom-workflow.md)、[在 Workflow 中调用 Agent 并处理结果](workflow-agent-calls.md)、[在 Workflow 中并行处理多项任务](workflow-parallel-pipeline.md)、[组合子 Workflow 并采用候选结果](compose-workflow-candidates.md)、[验证与调试自定义 Workflow](workflow-testing-debugging.md)。
