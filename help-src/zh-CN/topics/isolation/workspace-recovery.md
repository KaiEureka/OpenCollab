---
{
  "id": "workspace-recovery",
  "title": "找回隔离任务的改动并清理资源",
  "category": "isolation",
  "summary": "任务被取消、输出被截断或清理失败后，找回完整改动，再把残留的工作树、容器或 VM 租约清理好。",
  "aliases": [
    "文件找不到",
    "容器残留",
    "worktree retained",
    "recovery_location",
    "retain_changes",
    "entry_patch",
    "CandidateCaptureError",
    "CandidateWorkspaceTrackingError",
    "VM 租约保留",
    "pending_operations",
    "recover_operation",
    "VMTransportError"
  ],
  "keywords": [
    "恢复工作区",
    "清理失败",
    "补丁保留",
    "找回改动"
  ],
  "questions": [
    "在取消、输出截断或清理失败后找到完整改动，并继续处理属于该运行的资源？",
    "任务中途停了，改的代码还在吗？",
    "补丁显示被截断了，完整的去哪找？",
    "容器或工作树残留了怎么清理？"
  ],
  "related": [
    "candidate-delivery",
    "docker-isolation",
    "execution-environments",
    "vm-environment-extension",
    "member-authored-workflow"
  ],
  "verified_ref": "0c47f74a"
}
---

# 找回隔离任务的改动并清理资源

隔离任务被取消、补丁导出失败，或者清理出错时，改动通常还在，只是放在了保留的工作树、容器或 VM 工作区里。处理顺序永远是：先把完整改动保存下来，再清理工作树、容器或 VM 租约。

OC 给父成员的摘要有长度限制，可能只有半截补丁。别直接用它，去读完整补丁或保留目录里的实际文件。

## 步骤一：找到改动在哪

| 来源 | 去哪找 |
| --- | --- |
| 显式隔离的 Team 入口 | `result.metrics.get("entry_patch")`，回收前就保存了；关闭轨迹记录后仍可获得 |
| 失败或停止的子任务 | 结果里会尽可能带上已有改动；补丁较大时可能报告保留的工作区 |
| 错误或保留提示 | 直接给出恢复位置的真实值 |

恢复位置的形式：

- 本地：工作树路径。
- 容器：完整容器 ID 加容器内目录。
- VM：OC 会把完整工作区导出到控制端（运行 OC 的机器）运行产物下的 `vm/<租约 token>/<角色目录>`。导出成功后，直接在本机看文件和 Git 差异。

如果 VM 导出失败：记下错误里的机器 ID、来宾目录、操作 ID 和本机产物路径。等 SSH 连接恢复后，再继续导出。

## 步骤二：导出

从容器导出到一个新的恢复目录：

```bash
# CONTAINER_ID 和 /reported/workspace 换成报错给出的真实值
docker cp CONTAINER_ID:/reported/workspace ./recovered-workspace
```

然后检查里面的 Git 差异和任务产物。

## 步骤三：应用补丁

1. 先通读补丁内容。
2. 在目标目录检查能否应用：

   ```bash
   git apply --check /path/to/recovery.patch
   ```

3. 检查通过后，再实际采纳。

如果本机还有正在改的文件要保留，先把恢复材料放到单独的目录里比较，再决定怎么合并。

## 步骤四：清理残留资源

1. 保存报错、完整资源 ID 和已经导出的材料。
2. 确认资源状态：
   - `git worktree list`
   - 看目标目录里的文件
   - `docker inspect`
3. 停止还在运行的任务，导出改动。
4. 重试清理对应的资源。

## VM 断线与租约恢复

VM 的问题分三种情况处理。租约是 OC 在控制端 SQLite 文件里写的一条记录，表示某台 VM 正被占用。

### 情况一：你直接用 `VMEnvironment`，SSH 断了

SSH 出错时，OC 抛出 `VMTransportError`，里面带着 `operation_id`。远端命令不会因为断线而丢失。

1. 等连接恢复。
2. 调用 `await environment.recover_operation(error.operation_id)`，拿回这次操作的原始结果。
3. 用 `pending_operations` 看还有哪些操作没处理完。

如果控制端要重建环境对象：用原来的连接、工作区、状态目录和 `run_id`。这样新对象会读到同一份远端记录。

操作状态还不确定时，环境会先等恢复结果，再接受新的工作。

### 情况二：VM 池归还机器时失败

OC 归还一台 VM 要依次做完：导出内容，停止自己发起的操作，清理工作树，运行重置。管理员的 `reset_command` 成功、任务目录删掉之后，池才释放租约。

如果导出、清理、连接或重置失败：租约和恢复资料都会保留。只要池对象还活着，就可以重试清理。

### 情况三：控制进程已经退出

SQLite 里的租约不会自动消失。要手动释放一台机器，按顺序做：

1. 确认这台机器上对应的远端进程已经结束。
2. 导出需要保留的文件。
3. 完成管理员的来宾恢复程序。
4. 打开配置里 `lease_database` 指定的文件，按 `resource_id`、`owner` 和 `workspace` 核对，只删除这台资源对应的那一条租约记录。

```bash
# 表名和列名来自 OC 源码；先查看，再只删确认过的那一条
sqlite3 /home/controller/oc-state/vm-leases.sqlite3 \
  "SELECT resource_id, owner, workspace FROM vm_leases;"
sqlite3 /home/controller/oc-state/vm-leases.sqlite3 \
  "DELETE FROM vm_leases WHERE resource_id = 'worker-01' AND owner = '<租约 token>';"
```

如果还有机器在正常使用，不要删它们的记录。新建的池只要继续用原数据库和同样的 `resource_id`，就能认出当前哪些机器被占用。

准备和配置步骤见 [在现有 Linux VM 中运行任务](vm-environment-extension.md)。

## 为什么看到的补丁不完整

父任务通知和工具输出有显示长度限制，完整补丁是另外保存的。以下情况要从保留的工作区读原始文件：

- 导出失败
- 来源检查失败
- 通知装不下完整补丁

## 进阶：保留和释放的规则

- 带采纳能力的工作区，交付没完成时会保留实际文件，并拒绝过早释放。
- 原生工作树之后如果成功拿到完整的 `get_diff()`，可以清除保留状态，继续清理。
- 自定义提供器也应该提供对应的导出和重试流程。
- Team 采用冲突时会给出完整的恢复补丁。释放运行资源之前，把它另外存好。

## 进阶：取消时发生了什么

1. 先撤销环境的后续操作。
2. 处理正在进行的命令和文件操作。
3. 再释放资源。

环境被撤销后，后续模型调用不会再产生工具副作用。Workflow 里排队等待的调用也会检查环境状态。

## 进阶：资源归谁

| 资源 | 归谁 |
| --- | --- |
| 运行自己创建的工作树或容器 | 对应的环境池 |
| 外部传入的父环境 | 调用方 |
| 连接的已有容器 | 原来的服务 |
| VM 池分配的机器租约 | 对应的环境池，归还前运行管理员的重置程序。VM 本身由你的基础设施管理 |

如果某个审阅环境依赖另一个候选目录，清理会先处理依赖它的一方。被依赖的来源会保留到导出和释放都结束。具体接法见 [让任务在指定目录、容器或 VM 中执行](execution-environments.md)。

## 参考

恢复行为可对照[隔离指南](https://github.com/RISE-X-Lab/OpenCollab/blob/0c47f74a27441888286a22012b23b658185f8b8f/docs/session-isolation.zh-CN.md)、[候选环境保留机制](https://github.com/RISE-X-Lab/OpenCollab/blob/0c47f74a27441888286a22012b23b658185f8b8f/opencollab/adapters/_team_candidate_environment.py)和[Docker 池清理](https://github.com/RISE-X-Lab/OpenCollab/blob/0c47f74a27441888286a22012b23b658185f8b8f/opencollab/adapters/docker_isolation.py)。完整交付过程见 [把候选结果采纳到交付目录](candidate-delivery.md)。
