---
{
  "id": "reconstruct-workflow",
  "title": "使用 Reconstruct 从输入输出重建程序",
  "category": "workflow",
  "summary": "绑定可查询的隐藏程序与候选执行器，通过主动探索和反馈迭代得到候选程序。",
  "aliases": [
    "根据输入输出写程序",
    "程序反推",
    "黑盒复现",
    "主动探索",
    "oracle",
    "register_task",
    "candidate.py",
    "IO2CodeBench",
    "FromIO"
  ],
  "keywords": [
    "Reconstruct",
    "reconstruct",
    "黑盒程序重建",
    "ReconstructionTask"
  ],
  "questions": [
    "绑定可查询的隐藏程序与候选执行器，通过主动探索和反馈迭代得到候选程序？"
  ],
  "related": [
    "run-registered-workflow",
    "author-custom-workflow",
    "workflow-agent-calls"
  ],
  "verified_ref": "e7946a42"
}
---

# 使用 Reconstruct 从输入输出重建程序

Reconstruct 处理黑盒程序重建。调用方提供隐藏程序的查询函数，工作流逐批取得输入输出样例，再让 Agent 编写具有相同行为的候选程序。隐藏程序查询由工作流执行，Agent 根据公开样例和反馈修改 `candidate.py`。这类任务需要调用方同时提供候选执行器和独立的最终评分方式。

## 准备绑定对象

下面的函数把应用已有的 oracle、候选执行器和检查工具接入 `ReconstructionTask`。`environment` 是供 Agent 执行工具的环境对象，`evaluate` 应在同一个受控候选环境执行生成程序。

```python
from pathlib import Path
from opencollab import OpenCollab
from opencollab.builtin_workflows import ReconstructionTask, register_task
from opencollab.tools import builtin_tools

async def reconstruct_task(workspace, environment, oracle, evaluate, check_candidate):
    task = ReconstructionTask(
        input_type='list of integers',
        input_constraints='输入为整数列表，每个整数在 -100 到 100 之间。',
        function_name='solve',
        workspace=Path(workspace),
        oracle=oracle,
        evaluate=evaluate,
        tools=[
            *builtin_tools('bash', 'file_read', 'file_write', 'apply_patch'),
            check_candidate,
        ],
        kind='list', lo=-100, hi=100,
    )
    result = await OpenCollab(workspace, environment=environment).workflow(
        'reconstruct',
        {'task': register_task(task), 'max_iterations': 10, 'domain_check': True},
        budget=100000, max_steps=50, limit_mode='explicit',
    )
    return result.raise_for_status().output
```

`oracle(input)` 返回隐藏程序输出，对无效输入抛出异常。异步 `evaluate(program_path, inputs)` 为每个输入返回一条字典，成功条目含 `ok=True` 与 `output`，失败条目含 `ok=False` 与 `error`。调用方把候选代码放进 Agent 所在的受控环境执行，oracle 仍留在调用方。`check_candidate` 是提供给编码角色的实际检查工具。

`register_task` 保存本进程内的活对象，返回可序列化的单次句柄，工作流解析时消费它。重复运行应重新注册。CLI 采用 `module:callable` 工厂方式，从 `task_args` 的关键字参数构造绑定对象。

```bash
opencollab workflow run reconstruct \
  --workspace /path/to/workspace \
  --args '{"task":"my_bench.binding:make_task","task_args":{"name":"example"},"max_iterations":10}' \
  --budget 100000
```

此命令要求环境中存在 `my_bench.binding.make_task`，并返回 `ReconstructionTask`。工厂负责连接具体任务的查询器、执行环境及检查工具。

## 一轮怎样推进

工作流先获取初始样例，再开启编码会话。候选写完后，在所有已知样例上执行。全部通过构成一次 promotion，随后请求更多区分假设的输入。失败时把实际失败反馈送回编码阶段，继续使用已有样例。

输入提议需满足数量、去重、输入约束和 oracle 接受要求。失败提议按 `propose_attempts` 次数重试。每轮候选归档在 `.oc_candidates/`，结束时按最终样例集上的阶段得分选择本次归档中的候选复制为 `candidate.py`。先前运行留在目录中的文件与本次候选集合分开处理。

## 配置探索与停止

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `initial_batch` | 2 | 第一批样例数量 |
| `batch_step` | 2 | 后续批次增长量 |
| `max_iterations` | 50 | 编码迭代上限 |
| `early_stop` | 5 | 连续晋级达到该值后结束 |
| `propose_attempts` | 3 | 一批输入提议的尝试上限 |
| `examples_shown` | 12 | 输入提议角色看到的最近样例数 |
| `random_share` | 0.0 | 从独立随机源取得的样例比例 |
| `coverage_gate` | false | 让覆盖类别参与晋级判断 |
| `memorisation_gate` | false | 识别对已知输入的直接嵌入 |
| `complexity_gate` | false | 让高复杂度惩罚影响晋级 |
| `domain_check` | false | 检查提议数字范围 |

这些是已有探索策略选项，可按任务选择。启用随机比例时还要提供 `random_inputs(k, exclude)`，返回独立生成且新鲜的输入。`kind` 可为 `list`、`two_lists`、`int`、`pair`、`string`、`points` 或 `other`，配合 `lo`、`hi` 与 `is_float` 描述输入域。

前六个计数参数采用正整数，`random_share` 在 0 到 1 之间，策略开关在 JSON 中使用布尔值。`examples_shown` 缩小提议角色的样例展示窗口，编码角色和候选验证仍使用全部已知样例。

`coverage_gate` 按输入种类检查空值、长度、正负数和重复值等结构类别。相同缺失类别连续阻挡三批后允许继续。`memorisation_gate` 在候选嵌入至少三个已知输入时，先用同一批证据要求重写，最多等待两轮再取得新样例。`complexity_gate` 对复杂度惩罚至少 0.8 的候选继续补样例，前三次暂缓计入晋级。`domain_check` 根据 `lo` 与 `hi` 检查提议中的数值，`kind='other'` 跳过结构覆盖与数值域检查。这些选项影响探索和提前停止，最终评分继续采用应用自己的独立样例。

## 定制绑定与样例记录

`ReconstructionTask.candidate_file` 可把默认 `candidate.py` 改成任务需要的相对路径。`outputs_match(actual, expected)` 定义输出相等规则，适合业务确实允许浮点误差或等价结构的任务。`on_example(input, output, source)` 收到每条实际接纳的样例及其来源，可以接入应用日志。

```python
observed = []

def record_example(input_value, output_value, source):
    observed.append({'input': input_value, 'output': output_value, 'source': source})

def numeric_match(actual, expected):
    return abs(actual - expected) <= 1e-8
```

数值标量任务可在构造绑定时传入 `outputs_match=numeric_match, on_example=record_example`。列表或嵌套结构应采用匹配自身数据形状的比较函数。随机源收到的 `exclude` 使用输入的字符串表示，生成器可以先过滤已有值，工作流还会校验新输入与等价重复输入。

预算判断在当前候选执行和归档之后进行，便于保留最后一轮工作。`stop_reason` 可为 `early_stop`、`max_iterations`、`proposer_failed` 或 `budget_exhausted`。输出还包含迭代次数、晋级次数、已知样例、逐轮事件，以及 `final_program` 和 `final_score`。阶段得分根据已知样例和协议惩罚计算，独立隐藏测试的评分由调用方在运行结束后执行。

## 源码依据

[docs/reconstruct.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/reconstruct.md#L3)、[opencollab/builtin_workflows/reconstruct.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/builtin_workflows/reconstruct.py#L118)、[opencollab/builtin_workflows/__init__.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/builtin_workflows/__init__.py#L21)。

## 相关章节

[Workflow 完整指南与运行入口](run-registered-workflow.md)、[编写第一个自定义 Workflow](author-custom-workflow.md)、[在 Workflow 中调用 Agent 并处理结果](workflow-agent-calls.md)。
