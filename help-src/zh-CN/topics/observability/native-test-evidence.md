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

OC 可以通过原生命令执行项目测试，并用 `evidence_tools` 收集支持的测试记录。这样，候选选择能读取实际执行目标与结果，而非依赖 Agent 的口头总结。

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

帮助输出、零测试收集、只执行 collection，以及被截断而缺少足够结果的输出，应读取为尚未验证。某个命令进程正常退出，也可能没有执行你真正关心的测试。

## 后续修改怎样影响记录

同一目标重跑会更新当前证据，失败或中断会影响先前通过状态。观察到的文件修改会清除当前通过目标，并把旧记录标为适用性未知。未识别为受支持测试的 Bash 命令也可能具有副作用，因此需要重新建立证据。

历史记录保留原始退出状态和 verified 信息，当前候选是否仍受这些结果支持，则看 applicability 及后续修改。最后交付前应检查被采用的版本与被验证的版本一致。

## 工作区与相同文件名

测试路径按实际执行工作区解释。`cd tests && pytest -rA test_example.py` 与根目录下相应路径的关系需要按环境解析，两个目录里同名文件也可能是不同目标。读取记录时保留原命令和执行目录，便于复核。

正式 benchmark 的评分由 OCE 和对应验证器负责，开发过程中这些观察记录可以辅助决定候选与发现问题。

## 源码与进一步阅读

[docs/test-evidence.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/test-evidence.md) · [opencollab/adapters/tools/evidence/bash_evidence.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/tools/evidence/bash_evidence.py) · [opencollab/tools.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/tools.py)
