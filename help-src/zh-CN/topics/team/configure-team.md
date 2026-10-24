---
{
  "id": "configure-team",
  "title": "Team 完整指南与团队配置",
  "category": "team",
  "summary": "创建可运行的 team.yaml，为每个角色设置职责、工具、模型和允许交接的对象。",
  "aliases": [
    "team init",
    "team show",
    "--team-config",
    "OPENCOLLAB_TEAM_FILE",
    "组织结构",
    "通信拓扑",
    "谁能给谁派活",
    "prebuild_team",
    "run_collab_team.py",
    "固定名册启动",
    "--concurrent"
  ],
  "keywords": [
    "team.yaml",
    "团队配置",
    "角色",
    "topology"
  ],
  "questions": [
    "如何创建自己的多 Agent 团队？",
    "谁可以派任务、谁可以给谁发消息？",
    "固定团队和动态创建成员怎样选择？"
  ],
  "related": [
    "choose-working-mode",
    "dispatch-team-agents",
    "message-team-agents",
    "wait-for-team-results",
    "isolation-configuration",
    "tool-selection",
    "token-step-concurrency-limits"
  ],
  "verified_ref": "e7946a42"
}
---

# Team 完整指南与团队配置

Team 用一份配置声明入口成员、角色能力和通信方向，再由入口成员决定何时派发和怎样汇总结果。它适合让模型根据任务进展选择协作步骤。若步骤、分支和重试需要由调用方的 Python 明确控制，可以使用 [按任务选择默认 CLI、Base、Team 或 Workflow](../orientation/choose-working-mode.md) 中的 Workflow。

## 从当前内置团队开始编辑

```bash
opencollab team init team.yaml
opencollab team show --team-config team.yaml
opencollab --team-config team.yaml --workspace .
```

`team init` 导出当前内置团队并保留已存在的目标文件，`team show` 读取并展示入口、角色工具与拓扑。这两条准备命令可以在配置 API 凭据前执行。最终根命令才启动协作。CLI 的 `--team-config` 或 SDK 的 `team(config=...)` 优先选择团队文件，其次是环境变量 `OPENCOLLAB_TEAM_FILE`。文件名恰好叫 `team.yaml` 并不会让它被自动采用。

省略团队文件时，内置团队由 Analyst、Coder 和 Tester 组成，入口为 `analyst`。Analyst 拥有派发与采纳工具，Coder 负责修改，Tester 执行验证。内置拓扑只允许 Analyst 到达 Coder 和 Tester。角色声明会先出现在团队组织记录中，按需派发后才产生相应的活跃成员。

## 声明角色与实际执行能力

以下配置给入口明确的交付职责，Coder 修改文件，Tester 读取并运行项目命令。

```yaml
entry: manager
roles:
  manager:
    prompt: |
      Delegate implementation to coder. Adopt the completed coder's changes,
      then spawn a fresh tester. Summarize the delivered files and test evidence.
    tools: [spawn_agent, adopt_agent, file_read, team_status]
  coder:
    prompt: Implement the assigned change and report the affected files.
    tools: [file_read, apply_patch, grep]
  tester:
    prompt: Run the project's relevant tests against the delivered files.
    tools: [bash, file_read, git_diff, grep]
topology:
  manager: [coder, tester]
  coder: []
  tester: []
```

`entry` 指定接收用户任务的 agent 0。省略时先寻找名为 `lead` 的角色，再取第一个声明角色。角色名是身份，不能包含路径分隔符、首尾空格或控制字符，Unicode 与大小写归一后发生碰撞也会报错。显示名称不同于用途描述，修改身份会影响拓扑和寻址。

每个角色需要 `prompt` 或 `prompt_file`。文件路径相对团队文件所在目录，并受安全读取与目录范围检查。两者同时出现时，内联 `prompt` 优先。较长职责可以放到专用提示词文件，以便维护。

`tools` 是角色可调用的工具集合，角色可显式配置 `model`、`temperature`、`thinking`、`thinking_params` 和 `profile`。未设置的模型与采样项继承客户端全局值，`temperature: 0.0` 会显式覆盖全局值。profile 提供基础提示词、上下文处理和安全配置，角色职责与工具选择仍共同决定本次能力。工具列表中的 Bash 还需满足执行环境与权限要求。

## 拓扑同时约束派发和消息

`topology` 是有向图。上例的 manager 可以创建 coder 和 tester，也可以在具备 `message_agent` 工具时给这两种角色发消息。角色想回信，需要自己拥有消息工具，并且拓扑允许相反方向。要让 coder 回信，可以给它增加 `message_agent`，并把 `coder: []` 改为 `coder: [manager]`。

工作区共享由隔离配置决定，通信图管理成员之间的显式协作。若角色调用一个 Workflow，Workflow 内部由它自己的 Python 代码组织执行。使用入口见 [派发 Agent 任务并安排审阅](dispatch-team-agents.md) 和 [向队友发消息并继续协作](message-team-agents.md)。

## 选择按需成员或固定名册

SDK 默认 `prebuild_team=False`，只启动入口，其他成员由派发创建。`prebuild_team=True` 会在首次模型调用前为声明的角色建立固定名册，后续派发创建会被拒绝。固定名册适合通过消息反复交接任务，各角色需要相应工具和拓扑。

所有角色都省略额度时，Team 共享运行的 token 池。可以在 SDK 写 `budget=120_000`。团队文件顶层的 `budget: { tokens: 40_000 }` 则给每个角色独立额度，单个角色还可以覆盖自己的 `budget`。只要声明了一项角色额度，就要覆盖全部角色，并使用 `prebuild_team=True`。这种模式由文件给出总额，SDK 再传 `budget=` 会报错。

```python
import asyncio
from opencollab import OpenCollab

async def main():
    client = OpenCollab(".")
    result = await client.team(
        "实现目标修改并验证交付",
        config="team.yaml",
        budget=120_000,
        isolation="docker-session",
        artifacts="artifacts/team-change-001",
    )
    print(result.status, result.output)

asyncio.run(main())
```

示例使用独立容器执行各成员任务，Docker daemon 与任务镜像应提前就绪。`artifacts` 使用新目录，隔离细节见 [默认隔离与单次覆盖怎么配](../isolation/isolation-configuration.md)。`serialize_turns=True` 会让成员轮流执行，消息发送方结束当前回合后，队友才有机会继续。

## 运行自带的固定名册团队

源码中的 `configs/team.collab.yaml` 提供 Analyst、Coder、Tester 三个固定成员，通过双向消息交接任务。在源码根目录执行以下命令，启动脚本会为三个角色建立会话，并默认按串行回合运行。示例明确允许这些成员在可信宿主项目中执行 Git 和测试命令。

```bash
uv run python scripts/run_collab_team.py \
  --workspace /path/to/application \
  --prompt '修复指定问题，由 Coder 实现、Tester 验证，再交付结果。' \
  --budget 300000 --max-steps 100 --timeout 900 \
  --artifacts /absolute/path/artifacts/collab-001 \
  --allow-unisolated-shell
```

将较长任务保存在 UTF-8 文件后，可用 `--prompt-file task.md` 替换 `--prompt`。`--config` 选择另一份固定名册文件，`--concurrent` 允许已唤醒成员与当前成员同时运行。每个角色的步数上限默认为 100。共享 token 池默认为 1,000,000，团队文件声明全部角色额度时由文件决定额度，调用时省略 `--budget`。

这份团队的交付方式写在角色提示中。Coder 在自己的工作树提交改动后，通过消息发送 commit SHA。Tester 和 Analyst 使用该提交取得相同文件，最终交付目录是 Analyst 的工作目录。使用独立工作树时，源仓库应满足工作树准备要求，交接的提交还需要实际进入入口目录。按需派发团队的 `adopt_agent` 用法见 [采用队友改动并验证交付](adopt-team-changes.md)。

SDK 使用这份文件时传入 `config="configs/team.collab.yaml"` 与 `prebuild_team=True`，串行交接再加 `serialize_turns=True`。宿主 shell 权限由 `allow_unisolated_shell=True` 选择。根命令的 `--team-config` 负责选择文件，固定名册创建由 SDK 或这个专用脚本完成。脚本为成员 shell 设置关闭 pytest 缓存和 Python 字节码的默认环境值，并保留调用方已有设置，便于导出工作树改动。

启动参数与结果摘要见[固定名册启动脚本](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/scripts/run_collab_team.py)，角色交接职责见[自带团队文件](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/team.collab.yaml)。

## 自定义程度与结果确认

角色名与职责可以按业务改写，入口也可以换成研究者、分诊成员或调度成员。需要让入口直接处理文件时，给它相应文件工具即可。需要让它选择已注册工作流时，可以添加 run_workflow 工具。成员编写并运行自己的工作单元时，使用对应的工作流管理能力，详见 [让成员编写并管理自己的子 Workflow](../workflow/member-authored-workflow.md)。

按需团队可以对不同对象派发多次同角色任务。相同角色的多个成员各有 aid 和独立会话，消息寻址时应指定具体 aid。固定名册则让同一成员通过后续消息继续处理新工作，这两种安排会形成不同的上下文积累和预算使用方式。

SDK 调用完成后，先读 result.status 与 result.reason，再核对 output 里的完成说明和产物目录。工作树中的实现需要显式采纳，采用后才安排新的验证成员。若入口本身也隔离，交付补丁还需要由调用方应用到最终项目。团队配置确定协作能力，实际执行记录说明这些能力在本次任务中怎样被使用。

配置字段与默认值见[团队配置实现](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/opencollab/bootstrap/team_config.py)和[配置指南](https://github.com/RISE-X-Lab/OpenCollab/blob/e7946a42be378e0bf6dc02e24f9ded9752ce2a41/configs/README.md)。预算安排见 [设置 token 预算、步数与运行并发](../operations/token-step-concurrency-limits.md)。
