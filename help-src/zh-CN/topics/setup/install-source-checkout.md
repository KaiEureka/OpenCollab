---
{
  "id": "install-source-checkout",
  "title": "安装前提与版本选择",
  "category": "setup",
  "summary": "选定适合自己的发布版本或当前源码，准备能够启动 OpenCollab 的 Python 环境。",
  "aliases": [
    "怎么装 OC",
    "环境要求",
    "支持 Windows 吗",
    "稳定版",
    "release",
    "current main",
    "虚拟环境",
    "源码安装"
  ],
  "keywords": [
    "安装",
    "Python",
    "Linux",
    "macOS",
    "uv sync --locked",
    "pip install -e .",
    "pyproject.toml"
  ],
  "questions": [
    "选定适合自己的发布版本或当前源码，准备能够启动 OpenCollab 的 Python 环境？"
  ],
  "related": [
    "first-run-workspace",
    "python-sdk-entrypoints",
    "configuration-diagnostics"
  ],
  "verified_ref": "e7946a42"
}
---

# 安装前提与版本选择

在 Linux 或 macOS 上准备 Python 3.10 及以上版本，再按所需功能选择发布标签或当前源码，即可安装 OpenCollab。本文对应固定源码提交。发布标签及其 wheel 保留该标签发布时的 API，当前源码中新增的入口应结合所选版本检查。

## 先选定代码版本

需要重现已有程序时，优先使用它记录的发布版本和依赖。需要 Weave、`RunControl` 或 `WorkflowContext.agent_run` 时，应选择包含这些实现的版本。它们在 `v0.9.3` 之后加入，当前提交的 `pyproject.toml` 声明包版本为 `0.9.5`。包内版本字段用于识别源码，具体 release 的存在及附件应以仓库发布页为准。

首次获取源码可以在自己的开发目录执行下面的命令。若已有仓库，进入该仓库后选定目标标签或提交即可。接下来的源码安装命令都在仓库根目录执行。

```bash
git clone https://github.com/RISE-X-Lab/OpenCollab.git
cd OpenCollab
git checkout e7946a42be378e0bf6dc02e24f9ded9752ce2a41
python3 --version
```

最后一行应显示满足要求的 Python 版本。选择固定提交便于把当前帮助中的行为与代码对应，切换版本后也要使用该版本自带的文档和锁文件。

## 用 uv 或 pip 安装

已有 uv 时，`uv sync --locked` 按仓库锁文件创建和同步项目环境。准备修改框架源码并运行开发检查时，加上开发依赖。

```bash
uv sync --locked
uv run opencollab --help
```

```bash
uv sync --locked --extra dev
```

也可以使用 Python 自带的虚拟环境和 pip。可编辑安装让后续源码修改直接反映在该环境中，适合从源码开发和调试。

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -c 'from opencollab import OpenCollab; print(OpenCollab.__name__)'
.venv/bin/opencollab --help
```

选择一种环境准备方式并持续使用其中的解释器。终端显示安装成功后，若另一个终端找不到 `opencollab`，检查当前环境和可执行文件路径。直接使用 `.venv/bin/opencollab` 可以明确入口所处的环境。发布 wheel 的安装则在所选虚拟环境中使用该发布提供的安装方式，安装后同样读取包版本与帮助。切换源码标签时，重新同步依赖，让解释器环境跟随该版本的依赖声明。

## 核对包与宿主能力

安装后可读取当前环境中的包版本，同时检查公开导入和 CLI 帮助。以下命令适用于 uv 安装路径，pip 路径把 `uv run python` 换成 `.venv/bin/python`。

```bash
uv run python -c 'from importlib.metadata import version; print(version("opencollab"))'
uv run python -c 'from opencollab import OpenCollab, RunResult; print(OpenCollab, RunResult)'
uv run opencollab workflow --help
```

本地文件适配器依赖描述符相对路径操作、禁止跟随链接的文件检查、基于文件描述符的目录枚举、`fcntl.flock` 和原子无覆盖重命名。Linux 使用 `renameat2(RENAME_NOREPLACE)`，macOS 10.12 及以上使用 `renameatx_np(RENAME_EXCL)`。缺少相关系统能力时会出现 capability 错误，应根据实际宿主选择环境。

进入 CLI 帮助页、成功导入公开类和读取版本，说明安装入口已就绪。真正执行任务还需要模型端点、凭据和项目目录。下一步先准备 `configs/.env`，再选自己的项目为工作区完成第一次运行。

## 来源

[pyproject.toml](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/pyproject.toml#L5)　[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md#L17)　[opencollab/adapters/cli/main.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/cli/main.py#L40)

## 相关指南

[在自己的项目中完成第一次运行](first-run-workspace.md)　[从 Python 调用 OpenCollab](python-sdk-entrypoints.md)　[检查有效配置与定位常见启动故障](../operations/configuration-diagnostics.md)
