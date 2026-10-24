---
{
  "id": "file-editing-safe-workspace",
  "title": "安全地查找读取和修改文件",
  "category": "extensions",
  "summary": "在 Agent 实际工作区内定位文件、准确编辑内容并理解路径或覆盖错误。",
  "aliases": [
    "越界路径",
    "符号链接",
    "file_read",
    "file_write",
    "grep",
    "apply_patch",
    "str_replace",
    "line_replace",
    "unified_diff",
    "overwrite"
  ],
  "keywords": [
    "文件工具",
    "读写文件",
    "路径限制"
  ],
  "questions": [
    "在 Agent 实际工作区内定位文件、准确编辑内容并理解路径或覆盖错误？"
  ],
  "related": [
    "execution-environments",
    "worktree-workspaces",
    "shell-permissions-execution"
  ],
  "verified_ref": "e7946a42"
}
---

# 安全地查找读取和修改文件

文件工具以 Agent 的执行工作区解释路径。先确认 Agent 所在目录，再读取、定位并修改具体内容，可以减少把改动写到错误候选或父目录的情况。

## 读取与定位

`file_read` 使用 `path`，并提供 `offset` 和 `limit` 进行分页读取。大文件返回时要看实际显示范围及下一段位置，再继续读取。`grep` 根据模式、路径和 glob 搜索，可先定位函数再读取相邻内容。

下面的 JSON 展示一次文件读取的调用参数，由 OC 工具运行器执行。

```json
{"path": "src/parser.py", "offset": 1, "limit": 120}
```

读取结果过长时，单工具输出上限和历史压缩可能限制当前模型看到的内容。完整文件仍在执行环境中，可以用更窄的范围重新读取。

## 编辑文件

`file_write` 提供创建、整体写入和字符串替换等模式。字符串替换要提供可定位的原文与新文，整体覆盖前确认已有内容与覆盖授权。`apply_patch` 更适合清楚表达若干局部变更。具体参数和格式以所选工具的 schema 为准。

编辑之后使用 `git_diff` 或原生 Git 命令确认修改范围，并执行与变更相关的测试。文件写入成功说明写操作已经完成，功能行为还需要项目的实际验证。

## 按实际参数选择编辑模式

创建文件使用 `file_write` 的 `create` 模式，完整内容放进 `content`。该模式也能整体替换现有文件，大幅缩短已有长文件时需要显式 `overwrite=true`。局部修改使用 `str_replace`，`old_str` 必须在文件中恰好出现一次，可以增加邻近代码以获得唯一匹配。

```json
{"path":"settings.py","mode":"create","content":"RETRIES = 2\n"}
```

```json
{"path":"settings.py","mode":"str_replace","old_str":"RETRIES = 2","new_str":"RETRIES = 3"}
```

按行修改使用 `apply_patch` 的 `line_replace`，行号从 1 开始，`end_line` 包含在替换范围内。`expected_str` 校对当前范围，适合工具读过文件后再编辑。坐标过期且原文在文件中唯一时，可以显式开启 `relocate_expected`，让工具定位原文后编辑。若 `end_line` 等于 `start_line - 1`，则在该行之前插入内容。

```json
{"path":"settings.py","mode":"line_replace","start_line":1,"end_line":1,"expected_str":"RETRIES = 3","new_str":"RETRIES = 4","relocate_expected":true}
```

同一文件的多处改动可用 `unified_diff`。`patch` 接受标准 `@@ -a,b +c,d @@` hunk，文件头可省略。工具按上下文和旧内容匹配，容许小幅行号漂移。显式 `normalize_hunks=true` 会重算 hunk 行数，并允许上下文唯一的裸 `@@` 标头。任一 hunk 或范围匹配失败时，整个编辑保持原文件内容。

```json
{"path":"settings.py","mode":"unified_diff","patch":"@@ -1 +1 @@\n-RETRIES = 4\n+RETRIES = 5\n"}
```

`grep` 接受正则 `pattern`，并可配 `path`、`glob` 与 `max_results`，后者默认 50。`git_diff` 可用 `path` 聚焦文件，`staged=true` 查看暂存内容，`stat_only=true` 查看文件级统计，`include_status=false` 省略状态部分。普通模式显示相对 HEAD 的未提交改动，并补充未忽略的未跟踪文件。一次工具结果可能截断，后续可按目标文件继续读取。

## 工作区边界与链接

内置本地文件适配器会检查路径、链接和普通文件类型，容器中的文件路径也由对应环境检查。遇到链接或越出工作区的路径被拒绝时，应先确认目标是否属于任务需要的文件，再通过环境配置安排正确的工作目录或资产挂载。

宿主 Bash 仍具有宿主进程权限，原生文件工具的路径限制与 shell 的实际权限范围应分别理解。执行未知项目代码时，优先配置适合任务的进程与网络隔离。

## 多个 Agent 同时修改

共享目录中的成员会看到同一份文件。文件写锁有助于保护一次原生编辑，多个任务的业务修改仍可能互相影响。希望先独立实现再挑选结果时，应使用工作树或候选环境，通过明确的采用动作交付。

出现“Agent 已改好但源目录没变”，先查看当前环境和候选交付状态，再排查编辑工具。隔离任务在自己的目录修改后，需要通过采用或恢复补丁把结果带回目标目录。

## 源码与进一步阅读

[opencollab/adapters/tools/fs.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/tools/fs.py#L42) · [opencollab/adapters/tools/apply_patch.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/tools/apply_patch.py#L28) · [opencollab/adapters/safe_anchored_files.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/safe_anchored_files.py#L27) · [opencollab/adapters/_env_docker.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/_env_docker.py#L748)

[让任务在指定目录或现有容器里执行](../isolation/execution-environments.md) · [多个任务怎样在独立工作区改代码](../isolation/worktree-workspaces.md) · [管理终端权限并确认命令结果](shell-permissions-execution.md)
