---
{
  "id": "native-test-evidence",
  "title": "用实际测试验证 Agent 的交付",
  "category": "observability",
  "summary": "让工作流获得可执行的测试证据，读懂通过、未验证和过期记录。",
  "aliases": [
    "evidence_tools",
    "BashEvidence",
    "verified_targets",
    "has_pass_evidence",
    "opencollab.patches",
    "patch_paths",
    "只收集测试",
    "测试后又修改"
  ],
  "keywords": [
    "测试证据",
    "验证交付"
  ],
  "questions": [
    "让工作流获得可执行的测试证据，读懂通过、未验证和过期记录？"
  ],
  "related": [],
  "verified_ref": "e7946a42"
}
---

# 用实际测试验证 Agent 的交付

OC 可以执行项目原生测试命令，并用 `evidence_tools` 收集支持的测试记录。选择候选时，可以直接查看实际执行的测试目标与结果。

## 使用支持的工具组合

```python
from opencollab.tools import evidence_tools

tools = evidence_tools("bash", "file_read", "file_write", "apply_patch", "git_diff", "grep")
```

把这些工具交给工作流的候选 Agent。默认 shell 需要进程隔离环境，宿主执行授权按照终端权限指南配置。在工作流异步函数内可以读取候选观察记录。

```python
candidate = await ctx.candidate_agent(
    "修复公开问题并执行相关测试", tools=tools, label="implementation"
)
print(candidate.test_records)
print(candidate.verified_targets)
```

## 什么结果可以作为通过证据

观察器支持规定形式的 pytest、`go test -json` 和 Django 测试入口。pytest 使用 `-rA` 有助于保留逐项通过记录。退出码、目标、执行目录与输出截断情况会参与解释。

帮助输出、零测试收集、只执行 collection，以及截断后缺少足够结果的输出，都记为尚未验证。判断测试是否通过时，需要同时确认目标确实执行并有对应结果。

## 接入应用已经执行的测试

应用通过自己的执行服务取得命令结果时，可以用公开 `has_pass_evidence` 解释受支持测试的捕获输出。下面片段中的 `result` 是刚完成的命令结果，应保留原始退出码、标准输出、标准错误和截断标志。

```python
from opencollab.tools import has_pass_evidence

passed = has_pass_evidence(
    result.returncode,
    result.stdout + "\n" + result.stderr,
    runner="python -m pytest",
    target="tests/test_example.py::test_case",
    output_truncated=result.stdout_truncated or result.stderr_truncated,
)
print(passed)
```

该函数读取已有输出，命令执行由调用方完成。传入实际 runner 和目标，才能按对应测试记录解释结果。需要自动保存命令、执行目录和后续修改影响时，使用前面的 `evidence_tools` 或用 `BashEvidence` 包装自己的 Bash 工具。`VerificationTool` 协议允许扩展工具暴露当前 `verified_targets`，自定义测试结果仍由工具自己的解析器提供。

## 后续修改怎样影响记录

同一目标重跑会更新当前证据，失败或中断会影响先前通过状态。观察到的文件修改会清除当前通过目标，并把旧记录标为适用性未知。未识别为受支持测试的 Bash 命令也可能具有副作用，因此需要重新建立证据。

历史记录保留原始退出状态和 verified 信息。判断结果是否适用于当前候选时，查看 applicability 及后续修改，并在交付前确认采用的版本与测试的版本一致。

## 工作区与相同文件名

测试路径按实际执行工作区解释。`cd tests && pytest -rA test_example.py` 与根目录下相应路径的关系需要按环境解析，两个目录里同名文件也可能是不同目标。读取记录时保留原命令和执行目录，便于复核。

正式 benchmark 由 OCE 和对应验证器评分。开发时可以用这些测试记录选择候选、发现问题。

## 检查补丁涉及的文件

需要把测试目标与候选改动对应时，可以通过公开 `opencollab.patches` 解析 Git 补丁。

```python
from pathlib import Path

from opencollab.patches import patch_entries, patch_paths

patch = Path("candidate.patch").read_text(encoding="utf-8")
print(patch_entries(patch))
print(patch_paths(patch))
```

`patch_entries` 返回每个文件的旧、新路径，新增或删除一侧的 `/dev/null` 表示为空字符串。`patch_paths` 按首次出现顺序保留两端路径，便于处理重命名。解析器支持 Git 的引号与转义文件名、UTF-8 和文件名空格。需要逐块处理时，可使用同模块的 `split_patch_blocks` 与 `patch_block_target_path`。这些结果适合生成变更视图或选择测试，补丁采纳与候选交付继续使用 [把候选结果采纳到交付目录](../isolation/candidate-delivery.md)中的操作。

对接已有补丁读取器时，`git_header_tokens` 拆分 `diff --git` 文件头，`git_diff_endpoint` 处理某一侧端点与 `a/`、`b/` 前缀，`diff_target_path` 从文件头选择目标路径。`decode_git_c_path` 解码 Git 引号与转义路径，`normalize_patch_path` 统一路径分隔符。完整签名与实现说明从下方的公开 `opencollab.patches` 模块进入。

## 源码与进一步阅读

[docs/test-evidence.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/test-evidence.md) · [opencollab/adapters/tools/evidence/bash_evidence.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/tools/evidence/bash_evidence.py) · [opencollab/tools.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/tools.py) · [opencollab/patches.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/patches.py)
