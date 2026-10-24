---
{
  "id": "report-problem-and-request-help",
  "title": "反馈问题、请求帮助与提交改进建议",
  "category": "architecture",
  "summary": "选择正确反馈入口，用最小复现和必要记录让维护者理解遇到的问题。",
  "aliases": [
    "怎么提 issue",
    "找谁帮忙",
    "报 bug",
    "功能建议",
    "最小复现",
    "提供哪些日志",
    "脱敏",
    "安全漏洞",
    "Security Advisories"
  ],
  "keywords": [
    "问题反馈",
    "Bug report",
    "Question or support",
    "Feature request",
    "GitHub Issue",
    "version",
    "repro",
    "logs"
  ],
  "questions": [
    "选择正确反馈入口，用最小复现和必要记录让维护者理解遇到的问题？"
  ],
  "related": [
    "configuration-diagnostics",
    "run-results",
    "artifacts-and-history",
    "trace-and-events",
    "upgrade-existing-integration"
  ],
  "verified_ref": "e7946a42"
}
---

# 反馈问题、请求帮助与提交改进建议

遇到报错可以提交 Bug report，配置或用法疑问可以选择 Question or support，需要新能力时使用 Feature request。反馈中先写想完成的任务、预期结果和实际观察，再提供足以重复问题的最小命令或 SDK 调用。疑似安全漏洞通过私密安全报告入口提交。

## 选择反馈入口

[GitHub Issues 模板选择页](https://github.com/RISE-X-Lab/OpenCollab/issues/new/choose)提供这三类入口。Bug report 适合可观察的错误行为，使用问题适合解释目标与卡住的位置，功能建议应描述现有工作中具体遇到的困难，以及建议能力怎样改善它。

安全问题使用 [Security Advisories 私密报告](https://github.com/RISE-X-Lab/OpenCollab/security/advisories/new)。框架涉及模型生成代码执行、凭据和容器资源，报告应说明触发条件、影响范围与复现材料。仓库安全策略中给出的目标是在 72 小时内确认收到。

## 让维护者复现同一个问题

Bug 模板需要实际行为、复现步骤、编排模式和调用接口。补充模型与 provider、包版本或 Git 提交，能帮助区分配置问题与执行路径差异。命令应包含关键选项，SDK 示例保留触发错误的最小函数及参数，团队文件则保留相关角色、工具与拓扑。

下面是一份补丁交付问题的英文反馈示例。提交时填写实际版本、任务和观察结果。

```text
Goal
Apply a completed coder's changes to the delivery workspace.

Expected behavior
The adopted implementation appears in the delivery directory.

Observed behavior
The coder completed, but the source file retained its previous contents.

Reproduction
Run the attached minimal team configuration with the included SDK script.
Inspect the coder completion result and the final source file.

Environment
Team through Python SDK, with the exact package version and commit attached.

Evidence
Include the sanitized tool result, final diff, and relevant traceback.
```

描述观察时，写明哪个文件保留旧内容、工具返回什么、命令退出码是多少。最小复现可以保留一个小项目和一次工具操作，使维护者直接执行。

## 选择并处理相关记录

运行结果中的 `status`、`reason`、`agent_failures` 和 `metrics["run_id"]` 可以关联问题。产物目录、清单和轨迹能展示调用顺序，保存会话能帮助理解当时模型看到的任务。配置问题可以附上生效的配置字段和相关 CLI 参数。

提交前应移除 API key、授权头、带凭据的 URL、私有仓库内容与敏感日志。完整轨迹可能包含任务文件和模型回答，分享时先检查实际内容。保留错误类型、调用顺序、必要参数和相关日志片段，让维护者能够还原失败过程。

收到修复版本后，用原复现步骤再次执行，检查原先出错的文件或操作，并将观察补充到同一问题。说明使用的修复提交、实际执行命令和最终结果，可以帮助维护者判断问题是否得到解决。

模板字段见[Bug report](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/.github/ISSUE_TEMPLATE/bug_report.yml)、[使用问题](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/.github/ISSUE_TEMPLATE/question.yml)和[功能建议](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/.github/ISSUE_TEMPLATE/feature_request.yml)。安全处理见[安全策略](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/SECURITY.md)，相关记录的读取方式见 [读取运行结果与判断任务是否完成](../observability/run-results.md) 和 [查看 Agent 做了什么](../observability/trace-and-events.md)。
