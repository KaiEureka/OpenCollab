# Model-facing capability audit

[Chinese](llm-discoverability.zh-CN.md)

This October 24, 2026 audit follows a capability from its implementation through
the role configuration, model-visible instructions, tool schema, execution, and
returned observations. It covers the runtime features already merged through
PR 244 and the VM execution series through PR 247.

The audit identifies nine families of defects. They include missing discovery
information and instructions that advertise operations unavailable in the current
configuration. Fixes derive model-facing information from the configuration that
actually supplies the tools and execution environments.

## Confirmed findings and repairs

| ID | Normal trigger and consequence | Repair | Executable evidence |
| --- | --- | --- | --- |
| D1 | A Team member can launch an authored Workflow with an isolation string, but receives neither the configured choices nor the authoring interface. Selecting a VM or custom provider requires knowledge outside the request. | `manage_workflow` advertises the configured isolation names and default. Its `describe` action returns strategy semantics, inherited tool permissions, launch prerequisites, and an executable authoring example. | A public Team call inspects configured VM names without allocating a guest. Another call discovers the example, writes the source, starts the real Python child, and collects two agent results. |
| D2 | `run_workflow` exposes names but describes every input as an object usually containing a goal. Weave requires groups and checks; Reconstruct requires a task integration. | `run_workflow(action="describe", name=...)` returns the callable's input instructions and phases. Built-in instructions state their required inputs and representative calls. | A public Team discovers a custom function's input and executes it successfully. All three built-ins expose their required input terms without executing their bodies or spending model tokens. |
| D3 | An open topology omits the configured role catalog. Restricted roles can be told to use coordination tools they lack, while prebuilt teams receive spawning guidance even though their seats already exist. | Team context lists reachable configured role names, mentions granted coordination tools, and explains the prebuilt roster. Each role retains its own private prompt. | Tests capture the public model request for a prebuilt Team, inspect an open topology, check a message-only role, and ensure another role's prompt stays private. |
| D4 | A caller can supply `UseSkillTool` to an Agent or Workflow without a Team context builder. The tool requires a catalog name but never supplies the catalog. | The tool's own schema carries current skill names and descriptions. An empty catalog is identified explicitly. | A test discovers a skill name solely from the schema and loads its exact body. Catalog refresh and protocol conversion preserve the choices. |
| D5 | `FileWriteTool(allow_create=False)` still advertises whole-file creation. Child-run inheritance can also restrict an already-created tool after construction. | The exported schema reflects the current permission and advertises targeted replacement only when creation is disabled. Runtime write checks remain in place. | A test selects the advertised mode and performs a real replacement. A separate test tightens a tool after creation and confirms other instances retain their own modes. |
| D6 | Base/Single2's fixed prompt claims six tools and a shell even for `tools="read"` or caller-supplied tool sets. | The profile refers to the supplied tool definitions, makes shell instructions conditional, and preserves its task, safety, and delivery instructions. | A public read-only Agent request contains the actual read tools and no unconditional claim of shell availability. |
| D7 | A headless Analyst is told to call `ask_user` before planning, although that tool is removed when there is no human input channel. | Clarification guidance uses the tool when available and describes how to report a required unresolved decision. | A headless default Analyst has conditional instructions and no unavailable question tool. |
| D8 | A serialized Team's message acknowledgement requires `submit`, even when the sender has no such tool. The sender needs to finish its turn before the recipient can run. | The acknowledgement explains that a final response without tool calls yields the turn, with `submit` as an available-tool alternative. | Message execution returns an actionable instruction for a sender with no submit tool. Existing serialized scheduler tests continue to exercise delivery after the sender yields. |
| D9 | A headless local session advertises a Bash or adoption tool as executable even when that tool requires an OS process sandbox and the current environment has none. | The outgoing tool description states the current execution restriction. The execution check remains authoritative, and the tool instance is unchanged. | A public Agent avoids the blocked operation after reading the actual schema. The authorized local-shell control runs a real command; a direct blocked call still refuses execution. |

## Audited surfaces

The inventory contains 19 core model-callable tools and four example-specific
tools. Tool wrappers that preserve another tool's interface were checked at the
delegation boundary.

| Surface | Scope and outcome |
| --- | --- |
| Workspace tools | Bash, file reads, file edits, patch application, Git diff, and commit adoption. Parameter names were compared with the arguments consumed by execution. D5 and D9 cover configured edit and execution permissions. |
| Team coordination | Spawn, spawn with review, messages, roster lookup, child adoption, and submit. D3 and D8 cover runtime-specific guidance. Existing topology enforcement and asynchronous result delivery are retained. |
| Human input and skills | Question-tool availability and skill catalog delivery. D4 and D7 cover the gaps. Skill bodies continue to be returned unchanged. |
| Workflow entry points | Registered and member-authored Workflows, discovery, input guidance, isolation selection, lifecycle actions, and result collection. D1 and D2 cover the gaps. |
| Structured completion | `structured_output` and `submit_findings` already publish their schemas and receive explicit terminal instructions from the runtime. Existing validation and forced-submission behavior are retained. |
| Agent instructions | Analyst, Coder, Tester, generic role, Base/Single2, and Workflow role composition. D3, D6, and D7 address the configuration-dependent contradictions. |
| Context and transport | Context assembly, skill refresh, workflow discovery wrappers, and Chat Completions, Responses, and Anthropic tool conversion. The adapters preserve the new names and descriptions. |
| Bundled workflows | Duo, Weave, and Reconstruct expose input instructions through discovery. Their solving, selection, and verification algorithms remain unchanged. |
| IO2Code example | `query_oracle` and `check_candidate` publish task-specific input formats and return query or validation feedback. Active and passive modes supply their corresponding instructions and tools. |
| ARC-Bench example | `checkpoint` and `run_acceptance` expose states, scopes, and argument shapes. Requirement identities are supplied by the task and progress context. Existing acceptance semantics are retained. |

## Using the discovery interfaces

Call `manage_workflow` with `action="describe"` before authoring a child. The
response contains the configured strategy names, inherited default, permitted
child tool names, the subset constructible with `opencollab.tools.builtin_tools`,
and the callable entry shape. The example can be written to a Python file and
launched with an `args` object containing `tasks`.

Strategy availability is reported as `checked_on_allocation`. A configured Docker
provider or VM resource describes an execution option; the provider performs
connection, capacity, and allocation checks when the option is used. Discovery
performs no hardware probe and acquires no container or VM. The current launch
permission reflects the member's Bash permission and execution environment.

Call `run_workflow` with `action="describe"` and a registered name to inspect its
inputs. Then invoke the same tool with `args` and the default `run` action. Custom
Workflow authors put caller-facing input instructions in the function docstring;
discovery retains them through the module wrapper.

## Configuration boundaries

Model routes, request capacity, environment provisioning, and runtime defaults
remain programmer-owned settings. This change exposes information needed to use
existing agent actions. A new role-specific environment authorization policy or
a live hardware inventory would require its own behavior and API design.

Configured strategies continue through the existing execution checks. Child tools
retain the parent tool restrictions. Describing a capability leaves models,
budgets, hardware allocation, and candidate state unchanged.

## Verification

The focused regressions are in
[`test_model_discoverability.py`](../tests/interfaces/test_model_discoverability.py).
They use scripted model responses to drive real SDK sessions, tool execution,
and an owned Python child process. Provider conversion tests inspect the outgoing
schemas for Chat Completions, Responses, and Anthropic.

Run them with `uv run pytest -q tests/interfaces/test_model_discoverability.py`.
The pull request records the full-suite, architecture, dependency, packaging,
and CI results for its final revision.
