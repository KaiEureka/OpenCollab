---
{
  "id": "collaboration-adherence",
  "title": "检查团队是否真的按分工协作",
  "category": "observability",
  "summary": "读懂声明的团队与实际运行轨迹之间的差异，并找到分析入口。",
  "aliases": [
    "有没有真协作",
    "团队依从性",
    "角色没参与",
    "分工执行",
    "拓扑统计",
    "opencollab.teams",
    "declared_role_names",
    "declared_role_tools",
    "declared_role_budgets",
    "declared_context_policy"
  ],
  "keywords": [
    "协作统计",
    "Adherence"
  ],
  "questions": [
    "读懂声明的团队与实际运行轨迹之间的差异，并找到分析入口？"
  ],
  "related": [
    "configure-team",
    "trace-and-events",
    "evaluation-boundary"
  ],
  "verified_ref": "e7946a42"
}
---

# 检查团队是否真的按分工协作

团队文件描述希望怎样协作，运行轨迹描述实际发生了什么。Adherence 用于检查声明的组织是否在运行中实现，能够帮助判断多 Agent 实验中的收益来自怎样的协作行为。

## 先看声明

Team 配置包含入口角色、各角色的模型与工具、预算和通信拓扑。声明的角色可能实际没有参与任务，因此分析时要保留完整名册，包含未活动的成员。

`opencollab.teams` 提供离线声明读取接口。下面示例从仓库根目录读取自带团队文件，也可以把路径换成自己的 YAML。读取会解析配置和关联提示词文件，适合在发起运行前保存实验条件。

```python
from opencollab.teams import (
    declared_context_policy,
    declared_role_budgets,
    declared_role_names,
    declared_role_profiles,
    declared_role_tools,
)

team_file = "configs/team.collab.yaml"
print(declared_role_names(team_file))
print(declared_role_tools(team_file))
print(declared_role_budgets(team_file))
print(declared_role_profiles(team_file))
print(declared_context_policy(team_file))
```

角色名称已归一化，入口角色排在名册首位。工具名称按文件中的声明顺序返回，实际启动时还会应用能力筛选和后备工具组合。预算结果为空字典时，团队使用调用方提供的共享额度规则。显式角色额度则来自 YAML，并要求固定名册。上下文结果包含 `policy` 与 `tool_result_budget`。需要按提示词内容分组时，同模块的 `declared_role_prompt_digests` 读取包含 `prompt_file` 内容的提示词摘要。

入口角色、声明工具与通信边可以通过 `opencollab team show --team-config configs/team.collab.yaml` 查看。模型与完整角色配置从选定的 YAML 读取，具体配置见 [Team 完整指南与团队配置](../team/configure-team.md)。

## 再看实际执行

沿运行标识读取模型调用、工具操作、派发、消息与结果。角色收到任务、角色真正工作、改动被父任务采用，是不同事件。成功消息应按送达记录判断，被拒绝的尝试也有分析价值。

比如主成员先给 Coder 分工，随后自己完成全部实现，最后并未采用 Coder 的改动。这次运行可能获得正确结果，但它与“编码由 Coder 完成”的声明存在差异。

通信分析分别读取 `message_sent` 和 `message_refused`。它们的 payload 都包含 `from_aid`、`from_role`、`to_aid` 与 `to_role`，拒绝记录还包含 `reason`。`message_sent` 记录消息已被接受并入队，接收方会话中的消息与后续模型调用可以继续说明实际使用情况。按 `message_id` 关联持久化消息，能够区分同一角色之间的多次交接。`commit_refs` 保存消息携带的 Git 对象引用，便于核对候选从谁传给谁。

## 常见分析问题

| 问题 | 需要结合的材料 |
| --- | --- |
| 所有角色是否参与 | 声明名册、实际模型与工具事件 |
| 谁向谁派发或发送 | 拓扑声明、操作尝试和成功回执 |
| 角色是否超出职责 | 工具集合、提示和实际执行 |
| 交付来自哪个候选 | 采用记录、候选补丁与最终目录 |
| 是否处于相同实验条件 | 模型、工具、预算与上下文配置 |

## 指标怎样使用

Adherence 应基于明确的审计条件计算，首先说明单次运行何时算符合声明，再计算相应运行比例。论文中记录的结果与新的应用实验需要各自对应配置和轨迹。关于因果解释或团队效果比较，应继续阅读研究说明，而非把一个比例自动当作全部质量结论。

## 去哪里做批量分析

OC 保存通用运行与协作证据，OCE 侧维护实验配置、批量分析与报告。OC 的 CLI 从 `opencollab --help` 进入团队与工作流操作，离线声明读取则使用上面的 Python 接口。

批量研究从 [OCE 实验工具说明](https://github.com/RISE-X-Lab/OpenCollab-Eval/blob/main/src/opencollab_eval/experiment/README.md)进入。[cell_report_rows](https://github.com/RISE-X-Lab/OpenCollab-Eval/blob/main/src/opencollab_eval/experiment/cell_report_rows.py)解释轨迹和角色参与，[cell_report_statistics](https://github.com/RISE-X-Lab/OpenCollab-Eval/blob/main/src/opencollab_eval/experiment/cell_report_statistics.py)汇总选定尝试、通信与角色统计，并计算相应区间，[cell_report_rendering](https://github.com/RISE-X-Lab/OpenCollab-Eval/blob/main/src/opencollab_eval/experiment/cell_report_rendering.py)输出文字与 JSON。重试、替换和撤回的选择由 OCE 的 [batch_reporting](https://github.com/RISE-X-Lab/OpenCollab-Eval/blob/main/src/opencollab_eval/commands/batch_reporting.py)维护。研究批次的原始分析脚本位于 [experiment/analysis](https://github.com/RISE-X-Lab/OpenCollab-Eval/tree/main/experiment/analysis)，按对应批次版本和条件使用。

应用也可以用公开 Team 配置读取接口和事件记录建立自己的观察视图。先用少量具体运行逐项核对，再对批次汇总，能较早发现漏记、重复累计和角色映射问题。使用 OCE 汇总时，应同时读取指标分母与缺失状态，单成员参与率、所有声明成员参与率和通信边覆盖率各自回答不同的问题。

## 源码与进一步阅读

[docs/adherence.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/adherence.md) · [opencollab/teams.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/teams.py) · [opencollab/adapters/trace.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/trace.py) · [docs/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/README.md)

[Team 完整指南与团队配置](../team/configure-team.md) · [查看 Agent 做了什么](trace-and-events.md) · [用 OCE 评测 OC 的 Agent、Team 和 Workflow](evaluation-boundary.md)
