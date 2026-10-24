---
{
  "id": "weave-workflow",
  "title": "使用 Weave 分组执行并检查交付",
  "category": "workflow",
  "summary": "把任务组织为有依赖的工作组，经过实际检查和有限修复后读取交付结果。",
  "aliases": [
    "有序会话",
    "新会话接力",
    "分组协作",
    "groups",
    "check_commands",
    "delivery_ok",
    "WeaveAdapter",
    "run_weave",
    "继续运行"
  ],
  "keywords": [
    "Weave",
    "weave",
    "工作组",
    "交付检查"
  ],
  "questions": [
    "把任务组织为有依赖的工作组，经过实际检查和有限修复后读取交付结果？"
  ],
  "related": [
    "run-registered-workflow",
    "team-run-workflow",
    "workflow-agent-calls",
    "duo-workflow",
    "native-test-evidence",
    "cancellation-cleanup"
  ],
  "verified_ref": "e7946a42"
}
---

# 使用 Weave 分组执行并检查交付

Weave 把任务分成有顺序关系的组，每组使用新 Agent 会话，在同一个工作区继续工作。后续组能读取前组的文件改动，会话历史则重新开始。每组获得一定的 token、步数与时间份额，实际执行的检查决定交接和最终交付结果，失败检查可以触发有限轮修复。

## 从可执行检查开始

以下完整 SDK 脚本准备一个真实检查文件，再让 Weave 创建 `greeting.py`。把工作区和产物目录改成自己的路径，工作区会由脚本创建。

```python
import asyncio
import shlex
import sys
from pathlib import Path
from opencollab import OpenCollab

async def main():
    workspace = Path('/tmp/weave-help-example')
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / 'check_greeting.py').write_text(
        "from greeting import greet\n"
        "assert greet('Ada') == 'Hello, Ada!'\n"
        "assert greet('Lin') == 'Hello, Lin!'\n"
        "print('GREETING_CHECK_PASSED')\n",
        encoding='utf-8',
    )
    result = await OpenCollab(workspace).workflow(
        'weave',
        {
            'groups': [
                {
                    'id': 'function',
                    'prompt': "创建 greeting.py，greet(name) 返回 'Hello, ' 加名字加 '!'。",
                    'resources': ['greeting.py'],
                },
                {
                    'id': 'command',
                    'prompt': '加入命令入口，python greeting.py Ada 输出 Hello, Ada!。',
                    'dependencies': ['function'],
                    'resources': ['greeting.py'],
                },
            ],
            'check_commands': [
                {
                    'command': f'{shlex.quote(sys.executable)} check_greeting.py',
                    'expected_output': 'GREETING_CHECK_PASSED',
                },
                {
                    'command': f'{shlex.quote(sys.executable)} greeting.py Ada',
                    'expected_output': 'Hello, Ada!',
                },
            ],
            'config': {'max_repair_rounds': 2},
        },
        budget=30000, limit_mode='explicit', agent_profile='single2',
    )
    result.raise_for_status()
    assert result.output['delivery_ok'], result.output
    print(result.output)

asyncio.run(main())
```

CLI 可以把同样的对象写入 `flow.json`，再传入 `--args`。shell 中可用 `--args "$(cat flow.json)"` 读取文件内容，命令本身从 `--workspace` 指定的目录执行。检查命令应使用具备任务运行依赖的解释器。

## 组、依赖与资源

每组必填唯一的 `id` 和非空 `prompt`。`weight` 默认 1，影响份额分配。`dependencies` 引用前置组 id 或 target id，`resources` 描述共享文件或其他受影响资源。`targets` 默认使用组 id，用于检查与修复定位。

`plan_weave_groups` 可按依赖安排组，把依赖环中的组并入同一个会话，再将依赖表示为 target id。需要同时考虑几处相互关联改动时，可以通过这些字段表达关系。工作流按规划后的组顺序运行，新会话从工作区读取前组成果。

## 检查和有限修复

原生检查可识别 pytest、Go 与 Django 的解析式测试结果。命令探测可像示例一样指定 `expected_output`，并把断言放在成功标记之前。Weave 同时要求检查 `ok` 与 `executed`，`delivery_ok` 反映最终执行的检查是否通过。

`check_commands` 的每项可以是测试命令字符串，也可以是含 `command` 与 `expected_output` 的对象。后一种方式按输出子串匹配，还要求退出码为零、执行完成且输出捕获完整。检查为空、只显示帮助、只收集测试或普通命令缺少解析式测试证据时，结果保留为未验证。默认命令适配器在每次组检查和最终检查时执行所列的全部命令，自定义适配器可以使用传入的 `targets` 缩小验证范围。

`max_repair_rounds` 限制修复轮数，默认 3，`stagnant_round_limit` 默认 2。检查返回的 `repair_targets` 标明后续修复对象，`progress_markers` 表达可观察进展。共享额度、时间和实际授权也会限制能继续进行的工作。

SDK 的 `budget` 是实际硬额度来源。`config.main_budget` 可设初始软额度，经过已完成编辑和通过检查的证据，可在授权范围内扩展。`config.output_reserve_tokens` 为响应保留决策空间，单次模型输出上限通过模型配置的 `max_output_tokens` 设置。

## 配置资源分配

CLI 的 JSON `config` 字段与 Python `WeaveConfig(...)` 采用相同字段。可选上限默认 `None`，运行时结合 SDK 剩余额度和期限计算实际份额。先设置总预算与完成期限，再按任务耗时为检查和修复留下空间。

```json
{
  "config": {
    "main_budget": 18000,
    "repair_reserve": 4000,
    "max_steps": 30,
    "wall_seconds": 600,
    "group_check_timeout": 60,
    "max_repair_rounds": 2,
    "repair_steps": 12,
    "repair_timeout": 120,
    "final_check_timeout": 60
  }
}
```

| 配置字段 | 默认值与实际用途 |
| --- | --- |
| `budget` | 可选，限制 Weave 累计 token 消耗，仍受 SDK 授权限制 |
| `main_budget`、`main_hard_budget` | 可选，主阶段初始软额度和硬额度，按剩余组权重分配 |
| `repair_reserve` | 0，为修复保留 token，有限总额下最多保留总额的四分之一 |
| `max_steps`、`repair_steps` | 可选，主阶段各组累计步数和每轮修复步数上限 |
| `minimum_group_tokens`、`minimum_group_steps` | 均为 1，开始下一组所需的最小份额 |
| `wall_seconds`、`agent_seconds` | 可选，累计运行时间上限和主阶段会话分配窗口 |
| `minimum_group_seconds`、`minimum_session_seconds` | 均为 0，组启动所需时间和会话最小分配时间 |
| `main_agent_time_reserve`、`main_wall_time_reserve` | 均为 0，计算主阶段窗口时分别预留时间 |
| `group_check_fraction` | 0.25，从组窗口中预留给检查的比例 |
| `group_check_min_seconds`、`group_check_max_seconds` | 默认 0 与 `None`，限制预留的组检查时间范围 |
| `group_check_timeout`、`group_check_grace_seconds` | 默认 `None` 与 0，限制检查时长并允许使用组窗口后的额外时间 |
| `cleanup_seconds`、`tool_cleanup_seconds` | 默认 2 与 `None`，会话清理等待和单工具取消清理设置 |
| `output_reserve_tokens`、`history_trigger_tokens` | 默认 1 与 `None`，软额度决策的输出预留和会话历史整形阈值 |
| `extension_tokens` | 1，观察到新进展时软额度至少增加的 token 数 |
| `extension_margin_fraction`、`extension_margin_tokens` | 默认 0.1 与 `None`，决定接近软额度时采用的余量，显式 token 值限制比例计算结果 |
| `budget_final_prompt`、`system_prompt` | 可选，额度即将结束的提示和角色系统提示 |
| `max_repair_rounds`、`stagnant_round_limit` | 默认 3 与 2，修复轮数及连续无可观察进展的轮数上限 |
| `repair_budget`、`repair_budget_fraction` | 默认 `None` 与 0.65，单轮修复额度上限和中间轮使用的剩余额度比例，最后一轮可取得剩余额度 |
| `repair_time_reserve`、`repair_agent_time_reserve`、`minimum_repair_seconds` | 均为 0，修复可用时间、修复会话时间预留和启动条件 |
| `repair_timeout`、`final_check_timeout`、`final_check_time_reserve` | 默认 `None`、`None` 与 0，单轮修复时长及最终检查的时长和预留 |
| `heartbeat_seconds` | 60，活跃会话期间保存状态的间隔 |

次数和 token 字段采用整数，比例字段在大于 0 到 1 的范围内，时间采用秒。允许为零的预留和最小时间字段可关闭对应预留，`max_repair_rounds=0` 会保留最终检查并跳过修复。`config.max_steps` 分配主阶段累计步数，SDK `max_steps` 则是各角色继承的默认步数，两者应按各自范围设置。

## 应用检查与继续执行

有自己的验证服务时，在包装 Workflow 内调用 `run_weave`，传入 `WeaveGroup`、`WeaveConfig` 和自定义 `WeaveAdapter`。覆盖 `verify(ctx, targets, seconds)` 后返回 `WeaveCheck`，其中保留是否执行、是否通过、报告、修复对象及进展标记。这样的适配器可以把应用的真实行为检查接进分组流程。

`save_state(data)` 会收到独立的 JSON 状态快照，调用方可以持久化它。继续时把快照作为 JSON 输入 `state`，或构造 `WeaveState(saved_snapshot)` 传给 `run_weave`，并在 SDK 复用保存的 `run_id`。已生成组从检查处继续，已经检查的组保留生成结果，消耗过的修复轮数继续计数。保存组字段应与本次提供的组保持一致，这套继续能力依靠显式状态和相同的实际工作区成果。

下面的包装流程沿用原生命令检查，并把状态写到调用方指定的宿主文件。`launch` 接受与前面示例相同的 `groups` 和 `check_commands`，再次传入同一检查点时会恢复原 `run_id`。

```python
import json
from pathlib import Path
from opencollab import OpenCollab
from opencollab.builtin_workflows import (
    WeaveAdapter, WeaveConfig, WeaveGroup, WeaveState, run_weave,
)

class SavedChecks(WeaveAdapter):
    def __init__(self, checkpoint, commands):
        super().__init__(check_commands=commands)
        self.checkpoint = Path(checkpoint)

    def save_state(self, data):
        self.checkpoint.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

async def saved_weave(ctx, args):
    return await run_weave(
        ctx,
        [WeaveGroup(**group) for group in args['groups']],
        config=WeaveConfig(max_repair_rounds=2),
        adapter=SavedChecks(args['checkpoint'], args['check_commands']),
        state=WeaveState(args['state'], run_id=ctx.run_id),
    )

async def launch(workspace, checkpoint, groups, check_commands):
    checkpoint = Path(checkpoint).resolve()
    saved = json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else {}
    result = await OpenCollab(workspace).workflow(
        saved_weave,
        {'groups': groups, 'check_commands': check_commands,
         'checkpoint': str(checkpoint), 'state': saved},
        run_id=saved.get('run_id'), budget=30000, limit_mode='explicit',
    )
    return result.raise_for_status().output
```

检查点父目录应已存在，继续时使用原工作区中的真实文件成果。同步 `save_state`、`on_update(kind, state, data)` 与 `on_event(phase, event)` 适合保存短记录。自定义工具需要让适配器的 `source_snapshot()` 和 `progress()` 观察到真实文件变化和通过的检查，才能参与原生的重检与软额度扩展。`group_prompt`、`repair_prompt` 和 `improved` 可分别调整任务说明、修复说明及进展比较，`affects_prior_groups` 可声明某些变化需要扩大对先前组的检查。

## 读取交付结果

`output.status` 为 `completed`、`failed` 或 `unverified`，由最终执行的检查决定。`groups` 保存每组生成与检查结果，`verification` 保存最终报告，`repair_rounds` 保存各轮结果与进展。`main_stop_reason`、`repair_stop_reason` 和 `unstarted_ids` 说明资源在哪里停止、还有哪些目标尚未启动。`total_tokens` 与 `elapsed_seconds` 包含显式状态继续的累计记录，`state` 可用于下一次恢复。

## 源码依据

[docs/weave.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/weave.md#L3)。

[WeaveConfig 与 WeaveAdapter](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/builtin_workflows/weave.py#L76) 给出全部配置字段、验证回调和状态保存方式。[命令检查实现](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/builtin_workflows/_weave_execution.py#L87) 定义测试证据、输出匹配和未验证结果。

## 相关章节

[Workflow 完整指南与运行入口](run-registered-workflow.md)、[让 Team 成员调用已注册 Workflow](team-run-workflow.md)、[在 Workflow 中调用 Agent 并处理结果](workflow-agent-calls.md)、[使用 Duo 生成并选择双候选方案](duo-workflow.md)、[用实际测试验证 Agent 的交付](../observability/native-test-evidence.md)、[中断任务与检查资源清理](../operations/cancellation-cleanup.md)。
