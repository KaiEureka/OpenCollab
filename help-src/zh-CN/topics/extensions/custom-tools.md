---
{
  "id": "custom-tools",
  "title": "把自己的函数接成工具",
  "category": "extensions",
  "summary": "通过公开 Tool 接口加入业务能力并让 Agent 在正确环境中调用。",
  "aliases": [
    "Tool",
    "execute_with_runtime",
    "JSON Schema",
    "接入自己的API"
  ],
  "keywords": [
    "自定义工具",
    "工具扩展"
  ],
  "questions": [
    "通过公开 Tool 接口加入业务能力并让 Agent 在正确环境中调用？"
  ],
  "related": [],
  "verified_ref": "e7946a42"
}
---

# 把自己的函数接成工具

当任务需要新的业务操作时，可以实现公开的 `opencollab.tools.Tool` 结构接口。工具提供名称、JSON Schema 和异步执行方法，调用方再把实例加入 Agent 或 Workflow 的工具集合。

## 一个最小只读工具

下面的工具查询固定的测试策略，用于验证工具接口。把它保存为普通 Python 模块即可。

```python
class ProjectPolicy:
    name = "project_policy"
    description = "查询项目建议使用的测试命令。"
    parameters = {
        "type": "object",
        "properties": {"language": {"type": "string", "enum": ["python", "javascript"]}},
        "required": ["language"],
        "additionalProperties": False,
    }
    default_timeout = 30.0
    disable_outer_timeout = False

    def to_openai_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def execute_with_runtime(self, params, runtime):
        commands = {"python": "python -m pytest", "javascript": "npm test"}
        return commands[params["language"]]
```

在已有异步应用函数中调用 `await oc.agent("找出测试命令", tools=[ProjectPolicy()])`，或在工作流中把同一个工具实例传给 `ctx.agent`。模型提交的参数通过声明的 schema 检查，执行结果以文字返回会话。

## 什么时候需要 runtime

内存中的查询可以直接返回结果，无需访问 runtime。操作文件或命令的工具应使用 runtime 提供的执行环境，让路径与资源跟随当前 Agent。直接使用宿主 `open()` 或 `subprocess` 会把操作放在 Python 进程所在机器上，与任务容器中的目录不同。

下面的工具复用前一个类的 schema 转换方法，从绑定环境读取固定项目文件。把两个类放进同一个模块后，可将 `ReadProjectPolicy()` 直接放入工具列表。

```python
class ReadProjectPolicy(ProjectPolicy):
    name = 'read_project_policy'
    description = '读取当前任务工作区的 PROJECT_POLICY.md。'
    parameters = {'type': 'object', 'properties': {}, 'additionalProperties': False}

    async def execute_with_runtime(self, params, runtime):
        if runtime.environment is None:
            return 'Error: no execution environment available.'
        return await runtime.environment.read_file('PROJECT_POLICY.md')
```

`runtime.environment` 提供 `read_file`、`write_file` 和 `exec_cmd` 等执行入口，`runtime.aid` 与 `runtime.tool_call_id` 可关联当前调用。工具的普通返回值采用字符串，业务对象可先用 `json.dumps` 序列化。模型参数在 OC 工具分派时按 schema 检查，应用直接调用 `execute_with_runtime` 时，应自行提供已校验的参数。

`default_timeout` 为工具设置默认执行秒数，`disable_outer_timeout` 决定是否交由工具自己管理外层期限。普通短工具可沿用示例的有限时限和 `False`，让运行时处理取消及清理。状态型工具在多个会话或候选中复用时，应由实现者管理状态归属。需要候选测试记录的工具还应按公开 `VerificationTool` 提供 `verified_targets`，通过真实验证填充记录。

工具实现需要管理自己的权限、外部请求和副作用。涉及写入时，在实现中确定作用范围、失败后的数据保留方式，以及重复执行时怎样处理已有结果。需要隔离或回滚的操作也由实现者接入相应机制。

## 如何说明工具

名称应稳定、唯一，并容易从日志中识别。描述说明什么时候使用和会返回什么。参数约束尽量用类型、必填字段与枚举表达。较长的使用方法可以放到项目 Skill，工具仍然负责实际执行。

## 验证工具调用

先用合法参数直接测试返回结果，再覆盖缺字段、错误类型、执行异常和超时。随后使用本地模型替身或固定工具调用，验证 Agent 收到了 schema、调用成功，且结果进入了下一轮会话。最后用真实任务观察模型是否会在合适时机选择该工具。

若要把新工具贡献为内置能力，继续遵循仓库的依赖方向和工具注册约定。应用私有工具通常可直接通过公开接口组合。

## 源码与进一步阅读

[opencollab/tools.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/tools.py) · [opencollab/application/tool_execution.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/tool_execution.py) · [opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md)
