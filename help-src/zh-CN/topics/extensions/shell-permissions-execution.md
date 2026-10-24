---
{
  "id": "shell-permissions-execution",
  "title": "管理终端权限并确认命令结果",
  "category": "extensions",
  "summary": "让必要命令真正运行，理解确认提示、超时和截断，并据实际输出判断结果。",
  "aliases": [
    "风险命令",
    "自动批准",
    "命令超时",
    "输出截断",
    "--yolo",
    "allow_unisolated_shell",
    "require_process_isolation",
    "SandboxInterceptor",
    "timeout",
    "max_output_chars"
  ],
  "keywords": [
    "终端命令",
    "命令确认",
    "bash"
  ],
  "questions": [
    "让必要命令真正运行，理解确认提示、超时和截断，并据实际输出判断结果？"
  ],
  "related": [
    "isolation-overview",
    "file-editing-safe-workspace",
    "workspace-recovery",
    "tool-selection",
    "native-test-evidence"
  ],
  "verified_ref": "e7946a42"
}
---

# 管理终端权限并确认命令结果

Bash 工具在 Agent 绑定的执行环境中运行命令。决定它能影响哪些资源，需要同时看环境、工具授权和命令确认策略。

## 先判断命令的执行位置

本地环境和本地工作树中的命令使用宿主进程权限。Docker 环境中的命令在对应容器内执行，网络、资源和只读挂载按容器选项配置。Workflow 的 Python 编排本身仍由调用方进程执行，因此业务代码里直接调用宿主文件或子进程操作时，要单独考虑其作用范围。

## 三个常见开关

| 设置 | 管理的行为 |
| --- | --- |
| CLI `--yolo` | 自动批准被判定需要确认的风险命令 |
| CLI `--allow-local-child-shell` | 允许动态子成员在缺少系统进程沙箱时执行宿主 shell |
| SDK 或工作流的 `allow_unisolated_shell` | 显式允许相应运行使用宿主 shell 路径 |

这些选项的作用各有范围。团队配置和工具集合仍然需要包含相关工具。`builtin_tools(..., headless=True)` 默认要求 shell 使用进程隔离环境，`headless=False` 则允许对应的宿主执行方式。

## 怎样确认命令真的完成

读取执行工具返回的退出状态、标准输出、错误输出和超时标志。测试命令还应核实执行了预期目标，而非只输出帮助或只收集测试。需要机器可读测试证据时，使用 `evidence_tools` 与支持的 runner。

对于较长命令，应区分命令自身时限、Agent 运行期限和模型请求超时。增加模型超时无法延长一个已经被命令工具终止的进程。

## 中断与清理

取消运行会触发由框架拥有的命令和环境清理。调用方自己传入的环境继续由调用方管理。返回结果中的清理状态与恢复路径能帮助判断何时可以安全复用目录或删除临时资源。

命令执行失败后先看原始错误。找不到依赖、工作目录不正确、环境被撤销、权限拒绝与测试断言失败，需要分别处理。把原命令、退出状态和相关环境设置一起保留，比重复启动整项任务更容易定位原因。

## 源码与进一步阅读

[opencollab/adapters/tools/bash.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/tools/bash.py#L43) · [opencollab/adapters/safety.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/safety.py#L21) · [opencollab/bootstrap/tool_registry.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/tool_registry.py#L117) · [opencollab/adapters/cli/main.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/cli/main.py#L361)

[隔离怎么选](../isolation/isolation-overview.md) · [安全地查找读取和修改文件](file-editing-safe-workspace.md) · [找回隔离任务的改动并清理资源](../isolation/workspace-recovery.md) · [选择 Agent 能使用的工具](tool-selection.md) · [用实际测试验证 Agent 的交付](../observability/native-test-evidence.md)
