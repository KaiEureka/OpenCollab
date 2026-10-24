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

下面的工具查询固定的测试策略，便于先验证接口接线。把它保存为普通 Python 模块即可。

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

内存中的查询可以完全忽略 runtime。操作文件或命令的工具应使用 runtime 提供的执行环境，让路径与资源跟随当前 Agent。直接使用宿主 `open()` 或 `subprocess` 会把操作放在 Python 进程所在机器上，与任务容器中的目录不同。

自定义工具的权限、外部请求和副作用由实现者设计。把函数注册成工具，并不会自动让任意业务副作用获得隔离与回滚能力。准备写入能力时，应明确作用范围、失败后保留内容以及重复执行的行为。

## 如何说明工具

名称应稳定、唯一，并容易从日志中识别。描述说明什么时候使用和会返回什么。参数约束尽量用类型、必填字段与枚举表达。较长的使用方法可以放到项目 Skill，工具仍然负责实际执行。

## 如何验证接线

先用合法参数直接测试工具的返回结果，再覆盖缺字段、错误类型、执行异常和超时。随后使用本地模型替身或固定工具调用验证 Agent 是否收到 schema、能否执行，并将结果送回下一轮。真实模型是否选择该工具是另一层任务设计问题。

若要把新工具贡献为内置能力，继续遵循仓库的依赖方向和工具注册约定。应用私有工具通常可直接通过公开接口组合。

## 源码与进一步阅读

[opencollab/tools.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/tools.py) · [opencollab/application/tool_execution.py](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/application/tool_execution.py) · [opencollab/README.md](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/README.md)
