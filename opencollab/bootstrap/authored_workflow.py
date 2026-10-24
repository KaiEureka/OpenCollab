"""Own member-authored Workflow processes through an existing Environment."""

from __future__ import annotations

import asyncio
import json
import math
import posixpath
import re
import shlex
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from opencollab.adapters.session_environments import SessionEnvironmentPool
from opencollab.adapters.tools._paths import checked_path
from opencollab.adapters.tools.base import Tool
from opencollab.application.async_timeout import await_owned_operation
from opencollab.application.tool_execution import ToolRuntime
from opencollab.bootstrap.authored_workflow_bridge import AuthoredWorkflowBridge
from opencollab.bootstrap.tool_registry import TOOL_LIMIT_FIELDS

if TYPE_CHECKING:
    from opencollab.application.ports import EnvironmentPort
    from opencollab.application.session import Session
    from opencollab.bootstrap.context_builder import SpawnConfig

_RUNNER_STOP_GRACE_SECONDS = 5.0


@dataclass
class _Child:
    id: str
    environment: EnvironmentPort
    artifacts: str
    budget: int | None
    task: asyncio.Task[None] | None = None
    execution: asyncio.Task[Any] | None = None
    stop_requested: bool = False
    status: str = "running"
    tokens: int = 0
    result: dict[str, Any] | None = None
    cleanup_error: Exception | None = None
    bridge: AuthoredWorkflowBridge | None = None
    saved_artifacts: str | None = None
    artifacts_removed: bool = False


class AuthoredWorkflowTool(Tool):
    name = "manage_workflow"
    description = (
        "Execute a Workflow you wrote in a Python source file in your execution environment. "
        "launch returns a child id immediately. Use status, wait, stop or result with that id. "
        "entry defaults to main and accepts (ctx, args). Tokens share your Team budget. "
        "The environment's Python must have OpenCollab installed."
    )
    disable_outer_timeout = True
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["launch", "status", "wait", "stop", "result"]},
            "id": {"type": "string"},
            "source": {"type": "string", "description": "Python file inside your workspace."},
            "entry": {"type": "string", "default": "main"},
            "args": {"type": "object"},
            "python": {"type": "string", "default": "python3"},
            "budget": {"type": "integer", "minimum": 1},
            "concurrency": {"type": "integer", "minimum": 1, "default": 4},
            "timeout": {"type": "number", "minimum": 0,
                        "description": "Optional wait duration. Expiry leaves the child running."},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(
        self, get_session: Callable[[], Session], config: SpawnConfig,
        save_dir: str | None, *, allow_unisolated_shell: bool = False,
        environment_pool_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._get_session = get_session
        self._config = config
        self._save_dir = save_dir
        self._allow_unisolated_shell = allow_unisolated_shell
        self._environment_pool_factory = environment_pool_factory
        self._children: dict[str, _Child] = {}
        self._registered = False
        self._launch_lock = asyncio.Lock()

    async def execute_with_runtime(self, params: dict[str, Any], runtime: ToolRuntime) -> str:
        parent = self._get_session()
        if not self._registered:
            parent.own_runtime(self.aclose, reserve_tokens=self._reserved_tokens)
            self._registered = True
        action = params["action"]
        if action == "launch":
            async with self._launch_lock:
                child = await self._launch(params, runtime)
        else:
            child = self._children.get(params.get("id", ""))
            if child is None:
                raise ValueError("unknown child run for this Team member")
            if action == "stop":
                await self._stop(child)
            elif action == "wait":
                assert child.task is not None
                timeout = params.get("timeout")
                if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
                    raise ValueError("timeout must be finite and positive")
                try:
                    await asyncio.wait_for(asyncio.shield(child.task), timeout=timeout)
                except asyncio.TimeoutError:
                    pass
            elif action not in {"status", "result"}:
                raise ValueError("unknown Workflow action")
        return json.dumps(self._snapshot(child, include_result=action in {"wait", "stop", "result"}),
                          ensure_ascii=False)

    def _snapshot(self, child: _Child, *, include_result: bool) -> dict[str, Any]:
        record = {"id": child.id, "status": child.status, "tokens": child.tokens,
                  "artifacts": child.saved_artifacts or child.artifacts}
        if include_result:
            record["result"] = child.result
        return record

    def _reserved_tokens(self) -> int:
        return sum(max(0, (item.budget or 0) - item.tokens)
                   for item in self._children.values() if item.status == "running")

    async def _launch(self, params: dict[str, Any], runtime: ToolRuntime) -> _Child:
        parent = self._get_session()
        environment = runtime.environment
        if environment is None or environment.revoked:
            raise RuntimeError("no active execution environment available")
        shell = parent.agent.find_tool("bash")
        if shell is None:
            raise PermissionError("manage_workflow source execution requires the member's bash permission")
        isolated = bool(getattr(environment, "process_isolated", False))
        if (not self._allow_unisolated_shell or getattr(shell, "require_process_isolation", False)) and not isolated:
            raise PermissionError("manage_workflow requires an OS process sandbox or allow_unisolated_shell")
        if parent.runner._run_cancellation_requested() or parent.state.phase.is_terminal():
            raise RuntimeError("parent session is stopping")
        source = params.get("source")
        if not isinstance(source, str) or not source or "\x00" in source:
            raise ValueError("source must name a Python file in the workspace")
        source = checked_path(runtime, source)
        await environment.read_file(source)
        budget = params.get("budget")
        if budget is not None and (isinstance(budget, bool) or not isinstance(budget, int) or budget < 1):
            raise ValueError("budget must be a positive integer")
        remaining = parent.runner.remaining_budget_tokens
        if remaining is not None:
            if remaining <= 0:
                raise ValueError("Team session token budget is exhausted or reserved by child runs")
            budget = max(1, remaining // 2) if budget is None else min(budget, remaining)
        child_id = f"child-{uuid.uuid4().hex}"
        artifacts = posixpath.join(environment.workspace, ".opencollab", "child-runs", child_id)
        child = _Child(child_id, environment, artifacts, budget)
        config = {key: value for key, value in {**vars(self._config), **vars(parent.agent)}.items()
                  if key in {"model", "provider", "wire_protocol", "api_key", "auth_token", "base_url",
                             "temperature", "top_p", "thinking", "thinking_params", "reasoning_effort",
                             "context_window", "llm_timeout", "llm_max_retries", "llm_connect_timeout",
                             "llm_first_event_timeout", "llm_stream_idle_timeout", "llm_stream_chat",
                             "provider_error_time_budget", "thinking_params_explicit", "model_routes",
                             "request_pool", "request_capacity", "logical_model"}}
        config["max_output_tokens"] = parent.agent.max_tokens_per_step
        tool_limits = {
            tool.name: {key: getattr(tool, key)
                        for key in (*TOOL_LIMIT_FIELDS.get(tool.name, ()), "allow_create", "require_process_isolation")
                        if hasattr(tool, key)}
            for tool in parent.agent.tools
        }
        request = {"id": child_id, "source": source, "entry": params.get("entry", "main"),
                   "args": params.get("args", {}), "config": config, "budget": budget,
                   "max_steps": parent.max_steps, "concurrency": params.get("concurrency", 4),
                   "allowed_tools": [tool.name for tool in parent.agent.tools],
                   "tool_limits": tool_limits,
                   "process_isolated": isolated,
                   "artifacts": artifacts}
        command = " ".join([shlex.quote(params.get("python", "python3")), "-m",
                            "opencollab.bootstrap.authored_workflow_runner"])
        if runtime.safety_policy is not None:
            await runtime.safety_policy.check_cmd_interactive(command, runtime.confirm_fn())
        pool = (self._environment_pool_factory(environment, role=parent.agent.name)
                if self._environment_pool_factory is not None else SessionEnvironmentPool(
                    environment.workspace, isolation="shared", base_environment=environment,
                ))
        child.bridge = AuthoredWorkflowBridge(
            environment, artifacts, pool, config, runtime,
            allow_unisolated_shell=self._allow_unisolated_shell
            and not getattr(shell, "require_process_isolation", False),
        )
        request["bridge"] = child.bridge.options()
        request_bytes = json.dumps(request, allow_nan=False).encode("utf-8")
        self._children[child_id] = child
        child.task = asyncio.create_task(self._execute(child, command, request_bytes))
        return child

    async def _refresh_usage(self, child: _Child) -> None:
        try:
            usage = json.loads(await child.environment.read_file(posixpath.join(child.artifacts, "usage.json")))
        except FileNotFoundError:
            return
        self._account_usage(child, usage["tokens"])

    def _account_usage(self, child: _Child, tokens: Any) -> None:
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < child.tokens:
            raise ValueError("invalid child usage total")
        delta = tokens - child.tokens
        if delta:
            parent = self._get_session()
            parent.state.add_used_tokens(delta)
            parent.enqueue_auto_save()
            child.tokens = tokens

    async def _execute(self, child: _Child, command: str, request_bytes: bytes) -> None:
        execution = asyncio.create_task(child.environment.exec_cmd(
            command, timeout=math.inf, input_bytes=request_bytes,
        ))
        child.execution = execution
        if child.stop_requested:
            execution.cancel()
        logs: dict[str, Any] = {}
        status = "running"
        try:
            while not execution.done():
                await asyncio.wait({execution}, timeout=0.1)
                assert child.bridge is not None
                await child.bridge.poll()
                await self._refresh_usage(child)
                if self._get_session().runner._run_cancellation_requested():
                    await child.environment.write_file(posixpath.join(child.artifacts, "stop"), "stop")
            outcome = execution.result()
            logs = {"returncode": outcome.returncode, "stdout": outcome.stdout, "stderr": outcome.stderr}
            if outcome.returncode:
                raise RuntimeError(f"Workflow runner exited {outcome.returncode}: {outcome.stderr[-2000:]}")
            child.result = json.loads(await child.environment.read_file(posixpath.join(child.artifacts, "result.json")))
            self._account_usage(child, child.result["tokens"])
            status = child.result["status"]
        except asyncio.CancelledError:
            status = "stopped"
            child.result = {"status": "stopped", "output": None, "reason": "cancelled"}
        except Exception as exc:
            status = "failed"
            child.result = {"status": "failed", "output": None, "reason": f"{type(exc).__name__}: {exc}"}
        finally:
            if not execution.done():
                execution.cancel()
            settled = await asyncio.gather(execution, return_exceptions=True)
            if isinstance(settled[0], Exception):
                status = "failed"
                child.cleanup_error = settled[0]
                child.result = {"status": "failed", "output": None,
                                "reason": f"child process cleanup failed: {settled[0]}"}
            try:
                assert child.bridge is not None
                await await_owned_operation(child.bridge.aclose(), propagate_cancellation=False)
                await self._refresh_usage(child)
                self._account_usage(child, max(child.tokens, child.bridge.tokens))
            except Exception as exc:
                status = "failed"
                child.cleanup_error = exc
                child.result = {"status": "failed", "output": None,
                                "reason": f"{(child.result or {}).get('reason', '')}; child accounting failed: {exc}"}
            if child.result is not None:
                child.result["tokens"] = child.tokens
            child.status = status
            if self._save_dir is not None:
                try:
                    directory = Path(tempfile.mkdtemp(prefix=f"{child.id}-", dir=self._save_dir))
                    native_files = (child.result or {}).get("artifact_files")
                    if native_files is None:
                        try:
                            native_files = json.loads(await child.environment.read_file(
                                posixpath.join(child.artifacts, "artifact_files.json"),
                            ))
                        except FileNotFoundError:
                            native_files = []
                    for name in native_files:
                        if not isinstance(name, str) or not re.fullmatch(
                            r"(?:workflow\.json|\d+(?:_[^/\n]+)?\.json)(?:\.journal(?:\.lock)?)?", name,
                        ):
                            raise ValueError("invalid child artifact name")
                        try:
                            content = await child.environment.read_file(posixpath.join(child.artifacts, name))
                        except FileNotFoundError:
                            continue
                        (directory / name).write_text(content, encoding="utf-8")
                    child.saved_artifacts = str(directory)
                    (directory / "result.json").write_text(json.dumps(self._snapshot(child, include_result=True)),
                                                           encoding="utf-8")
                    (directory / "process.json").write_text(json.dumps(logs), encoding="utf-8")
                except Exception as exc:
                    child.status = "failed"
                    child.cleanup_error = exc
                    child.result = {"status": "failed", "output": None,
                                    "reason": f"child result persistence failed: {exc}", "tokens": child.tokens}

    async def _stop(self, child: _Child) -> None:
        if child.task is None or child.task.done():
            return
        child.stop_requested = True
        try:
            await child.environment.write_file(posixpath.join(child.artifacts, "stop"), "stop")
            await asyncio.wait_for(asyncio.shield(child.task), timeout=_RUNNER_STOP_GRACE_SECONDS)
        except (OSError, RuntimeError, asyncio.TimeoutError):
            if child.execution is not None and not child.execution.done():
                child.execution.cancel()
            await await_owned_operation(
                asyncio.gather(child.task, return_exceptions=True), propagate_cancellation=False,
            )
        if child.task.cancelled():
            child.status = "stopped"
            child.result = {"status": "stopped", "output": None, "reason": "cancelled", "tokens": child.tokens}

    async def aclose(self) -> None:
        failures = []
        async with self._launch_lock:
            for child in self._children.values():
                try:
                    await self._stop(child)
                except Exception as exc:
                    failures.append(exc)
                if child.cleanup_error is not None:
                    failures.append(child.cleanup_error)
                elif not child.artifacts_removed:
                    try:
                        result = await child.environment.exec_cmd(f"rm -rf -- {shlex.quote(child.artifacts)}")
                        if result.returncode:
                            raise OSError("child runtime artifact cleanup failed")
                        child.artifacts_removed = True
                    except Exception as exc:
                        failures.append(exc)
        if failures:
            raise RuntimeError(f"member-authored Workflow cleanup failed: {failures[0]}") from failures[0]
