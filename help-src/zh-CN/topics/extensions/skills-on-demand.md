---
{
  "id": "skills-on-demand",
  "title": "使用与编写 Skill",
  "category": "extensions",
  "summary": "给角色增加按需加载的操作说明，并知道技能内容何时进入会话。",
  "aliases": [
    "SKILL.md",
    "use_skill",
    "技能包",
    "技能不加载",
    "操作手册"
  ],
  "keywords": [
    "Skill",
    "技能"
  ],
  "questions": [
    "给角色增加按需加载的操作说明，并知道技能内容何时进入会话？"
  ],
  "related": [],
  "verified_ref": "e7946a42"
}
---

# 使用与编写 Skill

Skill 是按需加载的操作说明。它适合保存审阅步骤、项目约定或某类任务的工作方法。角色通过 `use_skill` 读取技能正文，再使用已经授予的工具执行任务。

## 放置一个技能

当前加载器读取任务工作区的 `skills/`。每个直接子目录放置一个 `SKILL.md`，可以同时保存辅助脚本或模板。

```text
skills/
  review-change/
    SKILL.md
```

```markdown
---
name: review-change
description: 在用户要求审查代码变更时，核对正常使用影响与实际验证证据。
---

先明确变更要解决的用户问题，再阅读对应实现与调用者。
用正常使用过程说明触发条件，并验证修复前后的行为。
最后汇报影响、验证结果以及仍需用户决定的事项。
```

`name` 是调用键，建议与目录同名。`description` 出现在目录摘要里，应说明具体触发场景。

## 让角色看到技能

在 Team 角色的 `tools` 中加入 `use_skill`，同时提供该角色实际需要的读取、编辑或命令工具。启动时，接受的技能目录进入系统提示。模型选择技能后，以名称调用 `use_skill`，获得正文。

```yaml
entry: reviewer
roles:
  reviewer:
    prompt: 审阅给定变更，任务符合 review-change 时加载对应技能。
    tools: [file_read, grep, git_diff, use_skill]
```

角色请求技能时提交下面的参数。名称来自已加载的目录摘要，查询失败会返回当前可用名称，便于检查拼写和加载结果。

```json
{"name":"review-change"}
```

辅助文件按普通工作区文件读取，例如让角色用 `file_read` 打开 `skills/review-change/templates/report.md`。脚本由角色已授权的命令工具执行，`use_skill` 的直接结果是 `SKILL.md` 正文。

技能正文会作为工具结果进入当前会话，遵循普通上下文整形规则。它不会永久固定在每一轮请求里。新增或修改技能目录后重新启动运行，可以获得新的目录信息。

## 技能与程序接口的关系

技能可以告诉 Agent 如何设计 Workflow，程序员也可以直接阅读开发手册编写 Python 工作流。Workflow 需要通过具体代码和接口确定执行顺序、并发与失败处理，Skill 为这种工作提供指导。

技能本身无法授予工具。一个只拥有 `file_read` 和 `use_skill` 的角色，即使加载了写代码的步骤，也仍需在角色配置中获得写入能力。

## 当前加载约束

正文上限为 8,000 字符，文件上限为 64 KiB。描述超过 500 字符时会截取用于目录。当前根目录最多扫描 256 个技能包目录和 4,096 个条目。错误的 frontmatter、重复名称、链接文件或大小超限都需要根据加载诊断处理。

当前实现以工作区技能目录为入口。需要跨项目共享时，可以由部署流程将已维护的技能放入各项目，后续再按实际扩展能力调整组织方式。

## 源码与进一步阅读

[skills/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/skills/README.md) · [opencollab/adapters/skills/file_skill_store.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/skills/file_skill_store.py) · [opencollab/adapters/tools/use_skill.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/tools/use_skill.py)
