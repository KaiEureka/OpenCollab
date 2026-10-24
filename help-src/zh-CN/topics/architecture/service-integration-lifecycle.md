---
{
  "id": "service-integration-lifecycle",
  "title": "从示例接入自己的服务",
  "category": "architecture",
  "summary": "把一次 SDK 演示接入业务服务，并明确运行身份、结果处理与资源清理责任。",
  "aliases": [
    "Web 服务",
    "嵌入应用",
    "RunResult",
    "RunError",
    "run_id",
    "artifacts",
    "CancelledError",
    "cleanup_timeout",
    "session_quiesced",
    "create_model_client"
  ],
  "keywords": [
    "服务集成",
    "SDK 接入",
    "资源所有权"
  ],
  "questions": [
    "把一次 SDK 演示接入业务服务，并明确运行身份、结果处理与资源清理责任？"
  ],
  "related": [
    "session-state-lifecycle",
    "execution-environments",
    "workspace-recovery",
    "isolation-overview"
  ],
  "verified_ref": "e7946a42"
}
---

# 从示例接入自己的服务

把 OpenCollab 接入服务时，应把每个业务请求关联到一次明确的运行，再保存运行结果、实际产物和清理状态。`agent` 适合一次独立任务，`team` 适合角色按进展协作，`workflow` 适合由服务代码规定步骤、分支和重试。

## 让业务请求和运行记录对应

每次公开 SDK 运行都会生成 `run_id`，调用方也可以传入自己的请求 ID。结果的 `metrics["run_id"]` 与会话快照、团队或 Workflow 清单、已开启的轨迹保持对应。每次运行的 `artifacts` 目录需要新建或为空，方便把请求与它产生的记录放在一起。

下面是只读项目分析服务中的调用函数。它使用显式读取工具，创建独立产物目录，并在结束时清理自己创建的环境。

```python
from pathlib import Path
from tempfile import mkdtemp

from opencollab import OpenCollab
from opencollab.environments import local_environment
from opencollab.tools import builtin_tools

async def inspect_request(project, request_id, task):
    artifact_root = Path("artifacts/service").resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifacts = mkdtemp(prefix="inspection-", dir=artifact_root)
    environment = local_environment(project)
    try:
        await environment.setup()
        client = OpenCollab(project, environment=environment)
        result = await client.agent(
            task,
            tools=builtin_tools("file_read", "grep"),
            budget=50_000,
            max_steps=40,
            timeout=900,
            cleanup_timeout=10,
            artifacts=artifacts,
            run_id=request_id,
        )
        return {
            "run_id": result.metrics["run_id"],
            "status": result.status,
            "reason": result.reason,
            "output": result.output,
            "artifacts": str(result.artifacts),
            "agent_failures": result.agent_failures,
        }
    finally:
        await environment.cleanup()
```

`project` 是服务允许使用的项目目录，`request_id` 应为非空文本。执行修改任务时，可改为独立工作树或容器，并明确候选采纳到业务目录的过程。多请求共享同一个可写目录时，需要根据业务安排修改次序，具体见 [隔离怎么选](../isolation/isolation-overview.md)。

## 让结果进入业务判断

`RunResult.status` 有 `completed`、`stopped` 和 `failed`。`completed` 对应 `ok=True`，表示运行正常完成。`stopped` 保留例如 `timeout`、预算或步数限制的 `reason`，并可能携带部分工作。`failed` 携带错误信息。`agent_failures` 保存经过脱敏的子成员失败摘要，适合判断团队或 Workflow 内部是否出现故障。

业务可以显式判断状态，也可以调用 `raise_for_status()`，让 stopped 或 failed 变为带原结果的 `RunError`。参数错误通常在运行前抛出，生命周期或持久化故障也可能直接抛出 `RunError`，服务应把相关错误与请求 ID 一起保存。

运行完成后，再用任务专用方式检查产物。代码任务在最终交付目录执行项目测试，业务工具任务读取目标系统中的最终状态，抽取任务核对要求的字段与来源。这样能把运行成功和业务验收分别记录清楚。

## 时间限制分别设置

SDK `timeout` 约束完整运行，Workflow 单次角色也可以设置自己的 `timeout`。模型请求、连接、首个流事件和流空闲超时由 transport 设置。`cleanup_timeout` 约束执行结束后的拥有资源清理。只增大模型请求超时，会继续受到完整运行 deadline 的约束。

如果 Workflow 需要严格保留调用方传入的 token 与步数额度，应选择 `limit_mode="explicit"`。默认 environment 模式会考虑 `OPENCOLLAB_UNBOUNDED_LIMITS`。所有限制都应结合一次真实请求观察，确认等待、停止与部分产物处理符合服务需要。

## 在服务中安排环境与记录

OpenCollab 客户端在构造时读取有效配置。服务更新模型路由或环境变量后，应按自己的配置更新流程创建相应客户端，让后续请求使用需要的设置。模型端点、实际请求并发和任务隔离分别配置，容器内能否联网也需要按工具访问目标验证。

产物可以关联服务请求，但完整会话可能包含业务输入和文件内容。服务应沿用自己的访问权限和保留规则存放这些材料，并把面向用户的摘要与内部排错材料放在适合的访问位置。需要减少轨迹时可以使用 trace=False，保存的会话和运行清单仍按各入口的产物行为保留。

统计成本时，运行 tokens 是累计输入与输出，缓存读取已经计入输入，推理 token 按提供方的输出统计处理。需要费用时使用有效价格与提供方原始用量计算，区分带 estimated 标记的估算记录。一次失败或超时也可能已经产生用量，最终记录有助于把资源消耗对应到真实请求。

## 接入已有模型客户端

应用需要在原生 transport 外增加观察或适配时，可以先调用 `create_model_client()`，再把满足模型调用协议的客户端通过 `agent(llm=...)` 传入。下面片段放在已构造 `client` 的异步函数中。

```python
model = client.create_model_client()
try:
    result = await client.agent(
        "说明项目的公开入口",
        tools=builtin_tools("file_read", "grep"),
        llm=model,
        budget=50_000,
        max_steps=40,
        timeout=900,
    )
finally:
    await model.close()
```

`create_model_client()` 沿用该 OpenCollab 对象解析后的配置，创建者负责在所有使用结束后关闭它。应用注入的 environment 同样由应用安排 setup、cleanup 和最终资源检查。按需创建并关闭 transport、按请求分配运行身份和产物目录，可以把连接生命周期与业务请求对应起来。

## 取消、结算与资源结束

调用方取消会在运行拥有的清理结束后继续传播 `asyncio.CancelledError`，运行 deadline 则产生 `reason="timeout"` 的停止结果。已经发出的模型请求可能稍后返回用量，框架在自己的 draining 阶段只结算一次。费用解释应读取最终保存的 usage 和清理记录。

`session_quiesced` 描述会话与持久化是否结束。调用方注入 environment 时，`environment_quiesced`、`cleanup_quiesced` 和 `execution_quiesced` 保留为 `None`，环境是否静止由调用方清理后验证。由 SDK 自己创建的运行资源按运行生命周期清理，应用拥有的 transport 和 environment 按前面的所有权安排结束。

本机文件适配器支持 Linux 和 macOS，并依赖平台的描述符相对操作、无跟随读取、文件锁和原子重命名能力。部署目标需要满足这些文件操作条件，处理未知代码应放在可丢弃的隔离环境中。调用方已有容器的资源归属见 [让任务在指定目录或现有容器里执行](../isolation/execution-environments.md)，异常材料恢复见 [找回隔离任务的改动并清理资源](../isolation/workspace-recovery.md)。

状态字段见[公开运行结果](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/sdk/result.py)，取消、运行身份与资源所有权见[运行参考](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/docs/evaluation-runtime.md)。
