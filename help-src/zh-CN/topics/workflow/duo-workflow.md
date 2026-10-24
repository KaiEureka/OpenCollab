---
{
  "id": "duo-workflow",
  "title": "使用 Duo 生成并选择双候选方案",
  "category": "workflow",
  "summary": "运行内置 Duo，理解两个候选的执行、证据比较与最终采用结果。",
  "aliases": [
    "Duo V8",
    "双 coder",
    "A B 方案",
    "adjudicator",
    "winner",
    "adopted",
    "submission_mode",
    "candidate_evidence_dir"
  ],
  "keywords": [
    "Duo",
    "duo",
    "双候选",
    "候选选择"
  ],
  "questions": [
    "运行内置 Duo，理解两个候选的执行、证据比较与最终采用结果？"
  ],
  "related": [
    "run-registered-workflow",
    "team-run-workflow",
    "compose-workflow-candidates",
    "weave-workflow",
    "native-test-evidence",
    "candidate-delivery"
  ],
  "verified_ref": "e7946a42"
}
---

# 使用 Duo 生成并选择双候选方案

Duo 为同一任务依次运行两个独立候选，再依据改动和验证证据选择、采用一个结果。候选 A 追求简单完整的实现，候选 B 关注端到端行为、依赖和边界情况。它适合任务目标明确、希望比较两份完整交付的情形，源代码、配置与数据文件都可以是任务产物。

## 启动一项任务

```bash
opencollab workflow run duo \
  --workspace /path/to/workspace \
  --task '修复配置读取问题，保留旧格式并运行受影响测试' \
  --args '{"allow_unisolated_shell":true,"submission_mode":"working_tree"}' \
  --budget 100000
```

`goal` 提供任务，`description` 也可作为输入任务的替代字段。`--task-file` 可读取长任务。示例明确允许可信本地工作区执行 shell，使用容器环境时按实际隔离要求设置。`working_tree` 模式适合调用方取得最终工作区改动后自行提交，默认 `task` 模式按照任务要求处理交付。

```python
import asyncio
from opencollab import OpenCollab

async def main():
    result = await OpenCollab('/path/to/workspace').workflow(
        'duo',
        {
            'goal': '修复配置读取问题并验证兼容性',
            'submission_mode': 'working_tree',
            'allow_unisolated_shell': True,
            'candidate_evidence_dir': '/path/to/duo-evidence',
        },
        budget=100000,
        artifacts='/path/to/new-duo-run',
        limit_mode='explicit',
    )
    result.raise_for_status()
    print(result.output)

asyncio.run(main())
```

也可从 `opencollab.builtin_workflows` 导入 `duo` 函数直接传给 SDK。`agent_profile` 可在 SDK 或 CLI 选择角色使用的 Agent 实现，省略时保留工作流角色配置。

## 候选怎样执行

Duo 先运行 A，再运行 B，随后机械比较、按需要仲裁并采用。B 会取得 A 观察到的公开验证命令，在相关且可用时重跑。整个过程共享一次工作流的有限 token 额度。提高 `concurrency` 或 `task_concurrency` 后，A 到 B 的顺序保持由源代码定义。

Git 候选从源工作区当时的已跟踪文件与未忽略的未跟踪文件开始，捕获随后产生的候选增量。两份候选各自执行，最终改动通过采用过程交付到源工作区。任务对提交、产物和服务状态的要求会进入角色指令，环境后端决定实际交付方式。

## 比较哪些证据

机械选择先处理空改动、可比较的公开测试结果和相同差异等情况。需要仲裁时，仲裁角色分别追踪两份候选对显式需求的实现路径，结合 changed paths、测试与报告判断覆盖。模型报告是待核实的说明，运行过的检查和源代码行为提供进一步依据。

测试记录在执行时标为当前适用，之后观察到内容变化的文件编辑，会把旧记录适用性改为未知并保留编辑路径。执行其他 Bash 命令也可能使旧记录的适用性变为未知。重新执行测试会产生新的当前记录，所以最后一次相关修改之后的检查尤其有用。

比较材料较小时，仲裁获得完整 diff、公开测试记录和候选报告。较大材料通过只读证据工具分页读取，原始证据文件保留。`candidate_evidence_dir` 选择 host 上的证据父目录，每次仲裁建立独立子目录，保存期限由调用方管理。证据等价时默认偏向 B，具体失败与已有规则可能先决定选择，结果中的选择原因可追溯采用依据。

## 解释返回字段

| 字段 | 含义 |
| --- | --- |
| `winner` | 选择过程推荐的候选 |
| `adopted` | 实际采用的候选，可能因首次采用失败而变化 |
| `status` | `done` 表示采用完成，`incomplete` 表示尚无候选交付，`error` 可表示任务缺失 |
| 选择原因与采用尝试 | 记录为何选择、采用是否遇到问题 |
| 测试和候选证据 | 支持继续检查最终交付 |

SDK 的 `RunResult.status` 描述整个流程执行，Duo 的 `output.status` 描述候选交付进度，两者还要与应用检查一起阅读。任务最终正确性由真实入口、原生测试或评测器确认。

源在候选执行期间变化、采用前源版本变化和补丁冲突均可能影响交付。捕获失败会保留候选工作区及恢复位置，遇到这些情况先保留已有材料，再对照源版本和候选 diff 处理。`injected_test_paths` 可由评测集成指定在采用时保留的保护测试路径，全环境产物则由调用方注入的候选后端交付。

## 源码依据

[docs/duo.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/duo.md#L5)。

## 相关章节

[Workflow 完整指南与运行入口](run-registered-workflow.md)、[让 Team 成员调用已注册 Workflow](team-run-workflow.md)、[组合子 Workflow 并采用候选结果](compose-workflow-candidates.md)、[使用 Weave 分组执行并检查交付](weave-workflow.md)、[用实际测试验证 Agent 的交付](../observability/native-test-evidence.md)、[把候选结果采纳到交付目录](../isolation/candidate-delivery.md)。
