---
{
  "id": "worktree-workspaces",
  "title": "多个任务怎样在独立工作区改代码",
  "category": "isolation",
  "summary": "并行修改同一个项目时保留清晰的文件起点并理解结果如何返回。",
  "aliases": [
    "分目录",
    "独立分支",
    "Git worktree",
    "WorktreeEnvironment",
    "WorktreePool",
    "use_worktrees",
    "source workspace has uncommitted changes"
  ],
  "keywords": [
    "工作树",
    "并行改代码",
    "worktree"
  ],
  "questions": [
    "并行修改同一个项目时保留清晰的文件起点并理解结果如何返回？"
  ],
  "related": [
    "isolation-overview",
    "candidate-delivery",
    "workspace-recovery"
  ],
  "verified_ref": "e7946a42"
}
---

# 多个任务怎样在独立工作区改代码

独立工作树让多个任务在不同目录里修改同一个项目，适合并行实现与候选比较。Git 项目共享对象存储，每个任务有自己的文件内容和索引。工作树位于父执行环境里，父任务在容器中时，子工作树继续在该容器里执行。

## 工作区从哪里开始

传统工作树根据 Git 提交创建，源仓库需要有可解析的 `HEAD`，并且保持干净。错误 `source workspace has uncommitted changes` 会附带相关文件线索。可以先查看 `git status --short`，再按项目工作方式提交、暂存处理或保留现有修改。运行自己创建的产物目录有专门排除规则，已跟踪文件的改动仍参与检查。

声明 `adopt_agent` 的 Team 使用候选快照准备子任务，会把当前交付目录中的用户修改和先前已采纳结果一并带入。审阅任务还可以以被审阅成员的真实环境为起点。因此，看到同一个 `worktree` 名字时，应结合入口判断它采用传统干净仓库路径，还是带采纳能力的当前内容快照路径。

下面通过公开工厂观察传统工作树。请在准备好的 Git 项目或普通目录中执行。

```python
import asyncio
from opencollab.environments import worktree_environment

async def main():
    environment = worktree_environment(".")
    try:
        await environment.setup()
        print(environment.workspace)
        result = await environment.exec_cmd("pwd")
        print(result.returncode, result.stdout)
    finally:
        await environment.cleanup()

asyncio.run(main())
```

工厂调用只创建对象，`setup()` 才准备目录。`workspace` 是工具实际使用的工作路径。如果从仓库子目录启动，新工作树仍保持该子目录相对仓库根的位置，方便已有脚本继续使用相对路径。

## 普通目录、子模块与共享资源

源目录没有 Git 时，环境创建独立副本，并以文件差异记录改动。Git 项目还需要关注已初始化子模块、可执行位和实际落盘内容。候选准备会处理可用的源子模块，并校验物理内容能否重建。出现子模块准备错误时，应确认源端已经初始化到需要的状态，并查看报错指出的子模块路径。

工作树内的文件编辑彼此独立，但操作系统进程和网络沿用父环境。两个任务启动相同端口的服务仍可能冲突，使用共同的外部缓存也可能互相影响。需要独立进程范围时，按 [隔离怎么选](isolation-overview.md) 选择容器策略。

## 找到最终交付

子成员返回的完成文本可以包含工作树 diff。声明采纳工具的入口成员需要显式采用成功 Coder 的结果，之后新的 Tester 才会从已经更新的交付内容开始检查。单独创建的工作树对象由调用方管理，导出需要的改动后再清理。遇到补丁捕获失败或保留提示时，先按 [找回隔离任务的改动并清理资源](workspace-recovery.md) 导出完整材料。

具体创建路径见[工作树环境](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/_env_worktree.py)和[工作树池](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/worktree_pool.py)，交付入口见 [把候选结果采纳到交付目录](candidate-delivery.md)。
