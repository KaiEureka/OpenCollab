"""Bind a Team tool to the existing Workflow runtime."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from opencollab.adapters.tools.base import Tool
from opencollab.application.run_control import RunControl, RunEvent
from opencollab.application.tool_execution import ToolRuntime
from opencollab.application.workflow_registry import Registry
from opencollab.bootstrap.container import build_workspace_safety_policy
from opencollab.bootstrap.session_factory import build_session
from opencollab.bootstrap.workflow_runtime import run_workflow

if TYPE_CHECKING:
    from opencollab.application.session import Session
    from opencollab.bootstrap.context_builder import SpawnConfig


class TeamWorkflowTool(Tool):
    name = "run_workflow"
    description = (
        "Run a registered Workflow in your current workspace and wait for its result. "
        "Pass its inputs in args (usually goal). The workflow may compose sub-workflows. "
        "Its tokens are charged to your remaining Team budget."
    )
    disable_outer_timeout = True

    def __init__(
        self, get_session: Callable[[], Session], registry: Registry,
        config: SpawnConfig, save_dir: str | None,
    ) -> None:
        self._get_session = get_session
        self._registry = registry
        self._config = config
        self._save_dir = save_dir
        self.parameters = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "enum": [spec.name for spec in registry.list_specs()],
                    "description": "\n".join(
                        f"{spec.name}: {spec.description}" for spec in registry.list_specs()
                    ),
                },
                "args": {"type": "object", "description": "Workflow inputs, including goal."},
                "budget": {"type": "integer", "minimum": 1},
                "concurrency": {"type": "integer", "minimum": 1, "default": 4},
            },
            "required": ["name", "args"],
            "additionalProperties": False,
        }

    async def execute_with_runtime(self, params: dict[str, Any], runtime: ToolRuntime) -> str:
        spec = self._registry.get(params["name"])
        parent = self._get_session()
        budget = params.get("budget")
        if parent.max_budget_tokens is not None:
            remaining = parent.max_budget_tokens - parent.used_tokens
            if remaining <= 0:
                return "Error: Team session token budget is exhausted."
            budget = remaining if budget is None else min(budget, remaining)

        def build_child(**kwargs: Any) -> Session:
            control = kwargs.get("run_control") or RunControl()

            def on_event(event: RunEvent) -> None:
                if event.type == "usage":
                    parent.state.add_used_tokens(event.data["total_tokens"])
                    parent.enqueue_auto_save()
                if control.on_event is not None:
                    control.on_event(event)

            kwargs["run_control"] = replace(control, on_event=on_event)
            kwargs["permission_policy"] = runtime.permission_policy
            kwargs["safety_policy"] = (
                runtime.safety_policy if kwargs["env"] is runtime.environment
                else build_workspace_safety_policy(kwargs["env"])
            )
            return build_session(**kwargs)

        save_dir = (
            tempfile.mkdtemp(prefix=f"workflow-agent-{runtime.aid}-", dir=self._save_dir)
            if self._save_dir is not None else None
        )
        config = {**vars(self._config), **vars(parent.agent)}
        config["max_output_tokens"] = parent.agent.max_tokens_per_step
        result = await run_workflow(
            spec, params["args"], cfg=config,
            workspace=runtime.environment.workspace if runtime.environment is not None else None,
            env=runtime.environment, cleanup_environment=False,
            budget=budget, limit_mode="explicit",
            max_concurrency=params.get("concurrency", 4),
            max_steps=parent.max_steps,
            llm=parent._llm, session_builder=build_child,
            save_dir=save_dir, trace=self._config.tracer is not None,
            return_details=True,
        )
        return json.dumps({
            "output": result.output,
            "tokens": result.tokens,
            "stop_reason": result.stop_reason,
            "agent_failures": result.agent_failures,
            "artifacts": save_dir,
        }, ensure_ascii=False, default=str)
