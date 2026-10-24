---
{
  "id": "interactive-cli-controls",
  "title": "默认终端交互与命令参数位置",
  "category": "setup",
  "summary": "在终端给指定 agent 发后续任务，查看团队状态，并正确组织根命令与子命令参数。",
  "aliases": [
    "切换 agent",
    "输入排队",
    "追问队友",
    "命令写哪里",
    "参数位置错误",
    "--yolo",
    "--allow-local-child-shell",
    "--no-worktrees"
  ],
  "keywords": [
    "CLI",
    "opencollab",
    "Tab",
    "Shift+Tab",
    "/help",
    "/exit",
    "--team-config",
    "workflow run"
  ],
  "questions": [
    "在终端给指定 agent 发后续任务，查看团队状态，并正确组织根命令与子命令参数？"
  ],
  "related": [
    "first-run-workspace",
    "session-save-resume",
    "cancellation-cleanup"
  ],
  "verified_ref": "e7946a42"
}
---

# 默认终端交互与命令参数位置

默认 CLI 把输入投递给当前选中的 Agent，并在运行期间继续接收后续任务。Tab 和 Shift+Tab 切换查看与投递对象，`/help` 显示本地操作说明。根命令启动团队，工作流和团队配置检查则通过各自的子命令使用。

## 在成员之间查看和追问

运行 `opencollab --workspace /path/to/application` 后，界面首先选择 agent 0。每个 Agent 保留独立的显示历史，切换成员会重画该成员在当前终端会话中收集的消息，输入缓冲区仍保留。配置中标记为 `available` 的角色也会出现在切换顺序里，角色真正产生会话后，界面开始显示它的消息。

提交普通文本时，消息绑定当前选中的 Agent。排队期间切换界面，消息仍发给原来的成员。给 Tester 追问测试结果时，先切换到 Tester 再输入文本。

运行中输入的任务会排队执行。若 Agent 正在询问用户，下一次输入会先作为问题的回答处理，包括 `/save` 等文本。回答当前问题后，即可继续使用本地控制命令或提交普通任务。

## 保存、退出与中断

输入 `/help` 可在本地查看帮助。`/save` 保存的是 lead 主会话，终端打印生成文件位置。`/exit`、`/quit`、`exit` 和 `quit` 离开交互会话。一次性运行加 `--hold` 后，`q` 也能退出查看界面。

Ctrl+C 在有当前轮次或待处理问题时发起中断，同时清空排队的轮次。若当前已经空闲，Ctrl+C 退出。停止后先看终端报告的原因和保留文件，再决定后续任务。保存恢复和资源清理细节见相应指南。

## 选项跟随自己所属的命令

下面四种写法分别选择交互团队、检查团队文件、列出工作流和运行指定工作流。把 `--workspace` 或模型选项放在 `workflow` 前，会被解释为交互团队的根选项，并出现参数位置提示。

```bash
opencollab --workspace /path/to/application --team-config team.yaml
opencollab team show --team-config team.yaml
opencollab workflow list --workspace /path/to/application
opencollab workflow run duo --workspace /path/to/application \
  --task '检查指定问题' --args '{"allow_unisolated_shell":true}'
```

`team show` 读取角色、工具和有向关系配置，不启动 Agent 会话，适合先确认实际选择的团队。工作流的 `--model`、`--budget`、`--concurrency` 等选项也应跟在 `workflow run NAME` 后。发现选项未知时，先看该层的 `--help`。示例中的 Duo 允许直接在宿主工作区执行 shell 命令。

## 理解三项执行选项

`--yolo` 自动批准被判定为风险操作的命令。`--allow-local-child-shell` 允许运行时动态创建的子 Agent 在宿主机执行命令，这些命令缺少操作系统沙箱保护。按任务的授权方式和执行环境分别设置这两个选项。

`--no-worktrees` 关闭子成员的 Git 工作树隔离。Git 工作树分开成员的文件改动，执行环境决定命令能访问的进程、网络和系统资源。具体共享范围见隔离指南。

## 来源

[opencollab/adapters/cli/main.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/cli/main.py#L58)　[opencollab/adapters/cli/main.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/cli/main.py#L182)　[opencollab/adapters/cli/main.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/cli/main.py#L227)　[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md#L77)

## 相关指南

[在自己的项目中完成第一次运行](first-run-workspace.md)　[保存会话与恢复历史](../operations/session-save-resume.md)　[中断任务与检查资源清理](../operations/cancellation-cleanup.md)
