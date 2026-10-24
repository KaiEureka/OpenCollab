---
{
  "id": "first-run-workspace",
  "title": "在自己的项目中完成第一次运行",
  "category": "setup",
  "summary": "选好项目目录，接通模型，并完成一次可观察的终端任务。",
  "aliases": [
    "第一次怎么用",
    "在哪个目录启动",
    "项目路径",
    "跑一句话",
    "一次性任务",
    "--hold"
  ],
  "keywords": [
    "快速开始",
    "workspace",
    "--workspace",
    "configs/.env",
    "uv run opencollab",
    "--prompt",
    "--prompt-file"
  ],
  "questions": [
    "选好项目目录，接通模型，并完成一次可观察的终端任务？"
  ],
  "related": [
    "install-source-checkout",
    "provider-endpoint-credentials",
    "interactive-cli-controls",
    "session-save-resume"
  ],
  "verified_ref": "e7946a42"
}
---

# 在自己的项目中完成第一次运行

从 OpenCollab 源码根目录启动 CLI，并用 `--workspace` 指向自己的项目，就能在终端完成第一次任务。根命令默认启动 Self-Collaboration 团队，入口是 Analyst，内部成员使用 Coder 和 Tester。工作区决定 Agent 操作的项目文件，启动命令所在目录则决定使用哪个源码环境。

## 准备项目与模型设置

先按安装指南完成源码环境。把下面的 `/path/to/application` 换成一个已经存在的项目目录。SDK 也要求传入的工作区是目录，建立清晰的项目路径能减少配置和产物位置混淆。

在 OpenCollab 源码根目录复制配置示例，然后用编辑器填写所用服务的 provider、base URL、物理模型名称和凭据。

```bash
cp configs/.env.example configs/.env
```

例如支持 Chat Completions 的兼容服务使用以下结构。占位值需替换成服务实际提供的内容，凭据文件保持在本地。

```dotenv
OPENCOLLAB_PROVIDER=openai
OPENCOLLAB_BASE_URL=https://your-endpoint.example/v1
OPENCOLLAB_MODEL=your-model-id
OPENCOLLAB_API_KEY=your-api-key
```

配置搜索先读取工作区下的 `configs/.env` 和 `.env`，再读取启动目录下的同名位置。若两处已有不同设置，按配置优先级指南确认生效值。需要直接选定某一文件时，可在命令前设置 `OPENCOLLAB_CONFIG_FILE`。

## 启动并观察一次任务

先运行交互入口，在终端里输入一个范围清晰的任务，例如查看项目入口并说明测试命令。

```bash
uv run opencollab --workspace /path/to/application
```

会话从 agent 0 开始。内置 Analyst 可把实现交给独立工作区中的 Coder，采用其完成改动后再安排 Tester。具体交接由 Agent 的实际工具调用决定，可以用 Tab 和 Shift+Tab 查看成员消息。项目理解任务也可能直接由入口角色完成。

希望输入固定任务并在该轮结束后退出时，使用 `--prompt`。在下面的命令中，`--trace` 开启轨迹记录。

```bash
uv run opencollab --workspace /path/to/application \
  --prompt '阅读项目入口和测试配置，说明如何运行最小验证。' \
  --trace
```

较长任务可以写入 UTF-8 文本文件，然后改用 `--prompt-file /absolute/path/task.txt`。这两个输入选项互斥，输入文件最大为 4 MiB。添加 `--hold` 会在一次性任务结束后保留查看界面，此时可以切换成员，输入 `q` 或 `exit` 离开。`--hold` 需要配合其中一个任务输入选项。

## 找到运行记录

终端会显示本次会话位置。团队自动保存目录位于项目的 `.opencollab/sessions/` 下，每个成员有自己的 JSON 会话，另有 `team.json` 描述该次团队运行。先查看 Agent 的回答，再核对文件改动和实际验证输出。希望继续主会话时，使用保存恢复指南中的 `--session`。

## 尝试工作流入口

工作流使用另一组子命令，选项放在对应子命令后。先列出该工作区能够发现的名称，再查看具体工作流的帮助。

```bash
uv run opencollab workflow list --workspace /path/to/application
uv run opencollab workflow run duo --help
```

在确实打算让 Duo 在宿主项目执行命令时，可以按下面的形式启动。

```bash
uv run opencollab workflow run duo --workspace /path/to/application \
  --task '修复指定问题，并运行项目验证。' \
  --args '{"allow_unisolated_shell":true}'
```

`--task` 写入工作流的 `goal` 参数。使用 `--args` 提供 `goal` 时，删除 `--task`，避免重复输入。需要隔离进程执行的项目，可先按执行环境指南选择合适环境再运行。

## 来源

[README.zh-CN.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/README.zh-CN.md#L47)　[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md#L44)　[opencollab/adapters/cli/main.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/cli/main.py#L58)　[opencollab/sdk/client.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/client.py#L123)

## 相关指南

[安装前提与版本选择](install-source-checkout.md)　[接入模型端点与认证凭据](../models/provider-endpoint-credentials.md)　[默认终端交互与命令参数位置](interactive-cli-controls.md)　[保存会话与恢复历史](../operations/session-save-resume.md)
