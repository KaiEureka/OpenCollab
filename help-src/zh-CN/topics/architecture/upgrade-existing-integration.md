---
{
  "id": "upgrade-existing-integration",
  "title": "升级已有安装、团队和 SDK 集成",
  "category": "architecture",
  "summary": "从已有版本迁移到选定版本，核对默认行为和公开接口，并验证原有任务继续按预期运行。",
  "aliases": [
    "更新后报错",
    "旧代码",
    "旧配置",
    "旧教程",
    "release 和 main",
    "升级 SDK",
    "run_tests",
    "profile",
    "导入路径变了"
  ],
  "keywords": [
    "版本升级",
    "迁移指南",
    "CHANGELOG",
    "SemVer",
    "Base",
    "Single2",
    "public API",
    "team.yaml"
  ],
  "questions": [
    "从已有版本迁移到选定版本，核对默认行为和公开接口，并验证原有任务继续按预期运行？"
  ],
  "related": [
    "install-source-checkout",
    "base-single2",
    "configuration-precedence",
    "configure-team",
    "custom-tools",
    "evaluation-boundary"
  ],
  "verified_ref": "e7946a42"
}
---

# 升级已有安装、团队和 SDK 集成

升级时先确定当前安装来源和目标版本，再按迁移记录核对接口与默认行为，最后用原有任务执行一次实际验证。选定发布 tag、附带 wheel 或源码提交后，阅读对应版本的包指南与迁移说明。

## 记录正在运行的安装

在启动 OpenCollab 的同一个 Python 环境里执行下列命令，可以查看安装版本和实际导入位置。源码安装还应在对应仓库记录 Git 提交。

```bash
python -c 'from importlib.metadata import version; import opencollab; print(version("opencollab")); print(opencollab.__file__)'
git rev-parse HEAD
opencollab --help
```

如果 CLI 和服务使用不同虚拟环境，应分别检查。同时确认服务进程实际导入的包位置与版本。源码安装过程见 [安装前提与版本选择](../setup/install-source-checkout.md)。

## 沿迁移记录找到行为变化

[迁移索引](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/README.md)按 0.5、0.6、0.7 和 0.8 串联主要变化。先处理跨版本的接口变化，再查看 [CHANGELOG](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/CHANGELOG.md) 中目标补丁版本的修复。当前签名与默认值以目标版本的包指南和 CLI 帮助为准。

0.8.0 把独立 Agent 的默认实现改为 Base，当前 Base 映射到 Single2。省略 profile，或使用 `base`、`default`、`single`，会跟随这项映射。显式 `single2` 和 `agent2()` 选择具体实现。比较历史实验时，应同时保留源码版本和实际 `metrics["agent_profile"]`。

检查已有工具列表。当前测试操作使用项目原生 Bash 命令，旧配置中的 `run_tests` 需要按 0.7.0 迁移说明更新。Bash 的宿主授权也应重新核对，SDK 默认要求进程隔离，明确允许宿主执行时才使用对应授权或 `headless=False` 工具设置。

## 核对配置与扩展接入

```bash
opencollab team show --team-config team.yaml
opencollab workflow list --workspace .
```

用团队展示命令检查角色、工具和拓扑，查找未知字段与旧工具名。Team 文件须显式选择，角色额度需要完整覆盖并与固定名册调用配合。公开导入应优先使用 `opencollab`、`opencollab.tools`、`opencollab.environments` 和 `opencollab.workflows`。较早集成引用内部路径时，可对照目标版本寻找公开入口。

自定义工具应保留参数 schema 和实际执行结果，环境提供器应继续管理申请、交付和清理。先在小型任务中执行一次文件读取、修改或命令操作，再查看结果与资源状态，确认扩展在新版本中按预期工作。

## 用原任务验证升级

选择原先能稳定执行的任务，沿原 CLI 命令或 SDK 调用再次运行。明确设置 token、步数和时间限制，并核对最终交付文件与实际测试结果。服务有取消路径时，还应触发一次取消，检查保存材料、用量和拥有资源的清理结果。

历史实验继续保留原版本信息，旧结果的模型、任务、提示词与限制也应一起保存。OCE 集成按它记录的框架依赖解释，评测职责见 [用 OCE 评测 OC 的 Agent、Team 和 Workflow](../observability/evaluation-boundary.md)。默认 Agent 的具体变化见[0.8.0 迁移](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/migrations/0.8.0.md)，当前公开接口见[包指南](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md)。
