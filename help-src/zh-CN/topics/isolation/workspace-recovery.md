---
{
  "id": "workspace-recovery",
  "title": "找回隔离任务的改动并清理资源",
  "category": "isolation",
  "summary": "在取消、输出截断或清理失败后找到完整改动，并继续处理属于该运行的资源。",
  "aliases": [
    "文件找不到",
    "容器残留",
    "worktree retained",
    "recovery_location",
    "retain_changes",
    "entry_patch",
    "CandidateCaptureError",
    "CandidateWorkspaceTrackingError"
  ],
  "keywords": [
    "恢复工作区",
    "清理失败",
    "补丁保留"
  ],
  "questions": [
    "在取消、输出截断或清理失败后找到完整改动，并继续处理属于该运行的资源？"
  ],
  "related": [
    "candidate-delivery",
    "docker-isolation",
    "execution-environments"
  ],
  "verified_ref": "e7946a42"
}
---

# 找回隔离任务的改动并清理资源

隔离任务停止或补丁导出失败后，先保存完整改动，再清理工作树和容器。运行结果、错误信息和 SDK 指标会指出入口补丁或保留位置。父成员看到的摘要可能已截断，恢复时应读取完整补丁或保留目录里的实际文件。

## 从结果找到文件

显式隔离的 Team 入口会在回收前保存补丁，位置可从 `result.metrics.get("entry_patch")` 读取。调用方关闭轨迹后仍可获得这一份恢复材料。子任务出现失败或停止时，会尽可能在结果中交付已有改动，较大补丁则可能报告保留工作区。

本地恢复位置是工作树路径。容器恢复位置形如完整容器 ID 加容器内目录，错误和保留提示会给出真实值。可以用 `docker cp CONTAINER_ID:/reported/workspace ./recovered-workspace` 把目录导出到一个新的恢复目录，再检查 Git 差异与任务产物。替换命令中的标记时，应逐项使用报错给出的值。

对于已经获得的补丁，先阅读内容，再在目标目录执行 `git apply --check /path/to/recovery.patch`，确认可应用后执行实际采纳。需要保留本机正在修改的文件时，先把恢复材料放在独立目录进行比较，再决定合并方式。

## 区分展示长度与捕获失败

父任务通知和工具输出会限制展示长度，这有助于控制模型上下文。完整补丁捕获在独立的交付路径完成。导出失败、来源检查失败或完整内容无法进入通知时，原始文件仍是恢复依据。

带采纳能力的工作区在交付未完成时会保留物理内容，并拒绝过早释放。原生工作树在后续成功取得完整 `get_diff()` 后，可以清除相关保留状态并继续清理。自定义提供器也应提供对应的导出与重试流程。Team 采用冲突时给出的完整恢复补丁，应在释放运行资源前另行保存。

## 取消与资源所有权

取消先撤销环境后续操作，并处理在途命令和文件操作，再进入资源释放。已撤销环境会阻止后续模型调用继续产生工具副作用。Workflow 中排队等待的调用也会检查环境状态。

运行自己创建的工作树或容器由相应池拥有。外部传入的父环境由调用方拥有，连接已有容器时该容器仍由原服务管理。某个审阅环境依赖另一候选目录时，清理会先处理依赖者，保留来源直到导出与释放都结束。具体接法见 [让任务在指定目录或现有容器里执行](execution-environments.md)。

如果清理再次失败，保存原报错、完整资源 ID 和已导出的材料，并对同一资源继续处理。用 `git worktree list`、目标目录文件和 `docker inspect` 核对实际状态，之后再进行针对性的回收。出现仍在运行的任务或未导出的改动时，应先完成它们的停止与保存。

恢复行为可对照[隔离指南](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/session-isolation.zh-CN.md)、[候选环境保留机制](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/_team_candidate_environment.py)及[Docker 池清理](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/adapters/docker_isolation.py)。完整交付过程见 [把候选结果采纳到交付目录](candidate-delivery.md)。
