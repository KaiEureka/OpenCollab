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

OpenCollab 支持 Linux 和 macOS，需要 Python 3.10 及以上版本。按所需功能选择发布标签或源码提交，再安装对应依赖。以下命令使用与这些指南对应的源码提交，发布标签及其 wheel 提供该版本的 API。

## 先选定代码版本

重现已有程序时，使用它记录的发布版本和依赖。Weave、`RunControl` 和 `WorkflowContext.agent_run` 在 `v0.9.3` 之后加入，需要这些功能时选择相应的新版本。当前提交的 `pyproject.toml` 声明包版本为 `0.9.5`，已发布的标签和安装附件可在仓库发布页查找。

首次获取源码可以在自己的开发目录执行下面的命令。若已有仓库，进入该仓库后选定目标标签或提交即可。接下来的源码安装命令都在仓库根目录执行。

```bash
git clone https://github.com/RISE-X-Lab/OpenCollab.git
cd OpenCollab
git checkout e7946a42be378e0bf6dc02e24f9ded9752ce2a41
python3 --version
```

最后一行显示 Python 版本，应为 3.10 或更高。切换源码版本后，使用该版本自带的文档和锁文件。

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

这些命令可以确认安装的包版本、公开导入和 CLI 入口。接下来在 `configs/.env` 中配置模型端点与凭据，选自己的项目作为工作区，完成第一次运行。

## 来源

[pyproject.toml](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/pyproject.toml#L5)　[opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md#L17)　[opencollab/adapters/cli/main.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/cli/main.py#L40)

## 相关指南

[在自己的项目中完成第一次运行](first-run-workspace.md)　[从 Python 调用 OpenCollab](python-sdk-entrypoints.md)　[检查有效配置与定位常见启动故障](../operations/configuration-diagnostics.md)
