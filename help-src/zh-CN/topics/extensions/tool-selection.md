---
{
  "id": "tool-selection",
  "title": "选择 Agent 能使用的工具",
  "category": "extensions",
  "summary": "按任务给 Agent 配置文件、命令、协作或技能工具并理解权限边界。",
  "aliases": [
    "tools",
    "builtin_tools",
    "evidence_tools",
    "六工具",
    "只读Agent",
    "工具没出现"
  ],
  "keywords": [
    "工具",
    "工具列表",
    "工具权限"
  ],
  "questions": [
    "按任务给 Agent 配置文件、命令、协作或技能工具并理解权限边界？"
  ],
  "related": [],
  "verified_ref": "e7946a42"
}
---

# 选择 Agent 能使用的工具

工具集合决定 Agent 可以请求哪些操作。OC 将普通编码工具与需要调度器、技能存储或交互能力的工具分别组装。配置前先确定任务需要读取、修改、运行命令还是与成员协作。

## 工具类别

| 类别 | 常见名称 | 作用 |
| --- | --- | --- |
| 文件与检索 | `file_read`、`grep` | 读取项目文件和寻找信息 |
| 编辑与差异 | `file_write`、`apply_patch`、`git_diff` | 修改文件并查看变化 |
| 命令执行 | `bash` | 在指定环境执行命令 |
| Team 协作 | `spawn_agent`、`message_agent`、`team_status`、`adopt_agent` | 派发、通信、状态与交付 |
| 扩展与组合 | `use_skill`、`run_workflow`、`manage_workflow` | 载入指导或执行工作单元 |

公开 `builtin_tools` 构造普通编码工具。协作工具由 Team 运行环境提供，在团队角色中声明后使用。

## 给 SDK 选择工具

```python
import asyncio
from opencollab import OpenCollab
from opencollab.tools import builtin_tools

async def main():
    tools = builtin_tools("file_read", "grep", "git_diff")
    result = await OpenCollab(".").agent(
        "阅读项目并说明测试入口，保留现有文件。",
        tools=tools,
        budget=50_000,
    )
    print(result.status, result.output)

asyncio.run(main())
```

示例授予读取与差异工具，适合查看项目文件并说明测试入口。要观察实际选中的工具，可查看运行指标 `agent_tool_names`。`profile_tool_names("base")` 返回的是 profile 默认列表，显式传入工具后应以实际运行记录为准。

## 工具存在还需要运行条件

`builtin_tools` 的 `headless=True` 会要求 shell 工具运行在提供进程隔离的环境中。明确选择 `headless=False` 会允许相应宿主 shell 路径，应结合实际调用环境确认。工作树解决目录分离，宿主权限与网络仍由父环境决定。

`allow_file_creation=False` 控制原生文件创建能力。它与整个运行的命令权限是不同设置，Bash 能执行的内容需要通过独立的权限与环境策略管理。

## 调整工具输出大小

`limits` 在构造工具时设置输出上限，适用于大文件、长测试日志和大范围差异。下面的示例保留较大的读取窗口，同时限制搜索结果和差异占用的上下文。

```python
from opencollab.tools import builtin_tools, profile_tool_limits, profile_tool_names

tools = builtin_tools(
    'file_read', 'grep', 'git_diff',
    limits={
        'file_read': {'max_read_chars': 32000},
        'grep': {'max_grep_chars': 12000},
        'git_diff': {'max_diff_chars': 24000, 'max_status_chars': 4000},
    },
)
print(profile_tool_names('single2'))
print(profile_tool_limits('single2'))
```

| 工具 | 可配置字段 | 含义 |
| --- | --- | --- |
| `bash` | `max_output_chars` | 单项命令输出的字符上限 |
| `file_read` | `max_read_chars` | 文件读取内容的字符上限 |
| `grep` | `max_grep_chars` | 搜索结果的字符上限 |
| `git_diff` | `max_diff_chars`、`max_status_chars` | 差异和状态各自的字符上限 |

各值采用 1 到 10,000,000 的整数。工作流启用 profile 后，`builtin_tools` 继承该 profile 的默认工具上限，再应用显式 `limits`。`profile_tool_names` 和 `profile_tool_limits` 分别用于查询默认工具顺序及上限，后者返回独立字典，适合用于配置展示。`evidence_tools` 采用同样的参数，再增加真实测试记录。

字符上限决定本次工具结果能展示多少内容。大文件仍可通过 `offset` 和 `limit` 分页读取，搜索可以缩小 `path` 和 `glob`，差异可以按文件或 `stat_only` 查看。按实际问题缩小范围，通常能让后续角色取得更完整的相关内容。

## 常见配置错误

工具名称重复会被拒绝。`limits` 中出现未选中的工具也会报错。`builtin_tools` 接受普通编码工具名称，传入 `spawn_agent` 会报错。Team 中需要使用注册名，并确保通信拓扑允许相关操作。

模型只输出文字时，先检查实际发出的工具 schema，再判断提示与工具选择设置。工具列表决定可请求的能力，工具回执记录实际执行的操作。

## 源码与进一步阅读

[opencollab/tools.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/tools.py) · [opencollab/bootstrap/tool_registry.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/tool_registry.py) · [opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md)
