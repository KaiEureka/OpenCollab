# VM 成员自编 Workflow

[English](README.md)

按照 [VM 使用指南](../../docs/virtual-machines.zh-CN.md) 准备两台现有 Linux VM。
在控制端和两台来宾中安装相同版本的 OpenCollab，再参照
[Session 隔离指南](../../docs/session-isolation.zh-CN.md) 保存包含 `resources` 和
`lease_database` 的 VM 配置 JSON 文件。

```bash
uv run python examples/vm-authored-workflow/demo.py --config /path/to/vm-profile.json
```

本地 HTTP 模型让 Team 成员写出 `generated.py`，通过 `isolation="worktree"`
启动它，再取得原生 Workflow 结果。第一个子 Agent 在成员 VM 内的独立 Git
工作树执行。第二个显式选择 `vm-session`，领取另一台来宾。每个 Agent 读取
输入文件，返回主机名、内核启动标识和目录，再创建自己的 `worker.txt`。

输出中的工作目录保留父子 Session 记录、Workflow 清单、用量、结果和导出的
VM 工作区。模型请求通过既有成员自编 Workflow 桥接在控制端执行。来宾需要
控制端能通过 SSH 访问，并已安装执行环境。模型凭据和请求额度目录保存在控制端。

每台 VM 资源需要管理员提供重置命令。来宾重新进入池之前，该命令须恢复工作负载
允许产生的全部来宾改动。示例只在分配的工作区中写文件，并使用既有命令清理。
若工作负载会修改依赖或系统配置，重置操作也须恢复这些变化。
