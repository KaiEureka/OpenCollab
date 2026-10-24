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

`max_repair_rounds` 限制修复轮数，默认 3，`stagnant_round_limit` 默认 2。检查返回的 `repair_targets` 标明后续修复对象，`progress_markers` 表达可观察进展。共享额度、时间和实际授权也会限制能继续进行的工作。

SDK 的 `budget` 是实际硬额度来源。`config.main_budget` 可设初始软额度，经过已完成编辑和通过检查的证据，可在授权范围内扩展。`config.output_reserve_tokens` 为响应保留决策空间，单次模型输出上限通过模型配置的 `max_output_tokens` 设置。

## 应用检查与继续执行

有自己的验证服务时，在包装 Workflow 内调用 `run_weave`，传入 `WeaveGroup`、`WeaveConfig` 和自定义 `WeaveAdapter`。覆盖 `verify(ctx, targets, seconds)` 后返回 `WeaveCheck`，其中保留是否执行、是否通过、报告、修复对象及进展标记。这样的适配器可以把应用的真实行为检查接进分组流程。

`save_state(data)` 会收到独立的 JSON 状态快照，调用方可以持久化它。继续时把快照作为 JSON 输入 `state`，或构造 `WeaveState(saved_snapshot)` 传给 `run_weave`，并在 SDK 复用保存的 `run_id`。已生成组从检查处继续，已经检查的组保留生成结果，消耗过的修复轮数继续计数。保存组字段应与本次提供的组保持一致，这套继续能力依靠显式状态和相同的实际工作区成果。

## 源码依据

[docs/weave.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/weave.md#L3)。

## 相关章节

[Workflow 完整指南与运行入口](run-registered-workflow.md)、[让 Team 成员调用已注册 Workflow](team-run-workflow.md)、[在 Workflow 中调用 Agent 并处理结果](workflow-agent-calls.md)、[使用 Duo 生成并选择双候选方案](duo-workflow.md)、[用实际测试验证 Agent 的交付](../observability/native-test-evidence.md)、[中断任务与检查资源清理](../operations/cancellation-cleanup.md)。
