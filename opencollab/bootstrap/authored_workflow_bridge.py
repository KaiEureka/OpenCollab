from __future__ import annotations

import asyncio
import base64
import builtins
import json
import math
import posixpath
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from opencollab.adapters._env_base import Environment, ExecResult, TextFileRange
from opencollab.adapters.llm import errors as llm_errors
from opencollab.adapters.llm.client import LLMClient
from opencollab.adapters.llm.types import LLMResponse, Usage, to_plain_data
from opencollab.application.async_timeout import await_owned_operation

_METADATA = {"context_window", "estimate_request_tokens", "estimate_context_tokens", "minimum_output_tokens"}
_COMPLETION = {"messages", "tools", "temperature", "thinking", "thinking_params", "reasoning_effort",
               "tool_choice", "top_p", "max_output_tokens", "response_session_id"}
_ENVIRONMENT = {"exec_cmd", "read_file", "read_text_range", "write_file", "write_temp_file", "remove_file"}


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def _restore_error(value: dict[str, Any]) -> Exception:
    name = value.pop("type")
    cls = getattr(llm_errors, name, getattr(builtins, name, RuntimeError))
    if not isinstance(cls, type) or not issubclass(cls, Exception):
        cls = RuntimeError
    error = cls(value.pop("message"))
    for name, item in value.items():
        setattr(error, name, Usage(**item) if name == "usage" else item)
    return error


class AuthoredWorkflowBridge:
    """Own only this authored run's strategy pools and admitted operations."""

    def __init__(self, environment: Any, artifacts: str, pool: Any, llm: Any, runtime: Any,
                 *, allow_unisolated_shell: bool) -> None:
        relative = posixpath.relpath(artifacts, environment.workspace)
        if relative.startswith("../") or posixpath.isabs(relative):
            raise ValueError("child mailbox must be inside the member workspace")
        self._environment = environment
        self._directory = posixpath.join(artifacts, "bridge")
        self._pool, self._config, self._runtime = pool, llm, runtime
        self._llm = None
        self._allow_unisolated_shell = allow_unisolated_shell
        self._environments = {0: environment}
        self._next_environment = 0
        self._tasks: dict[str, asyncio.Task] = {}

    def _model(self) -> Any:
        if self._llm is None:
            aliases = {"llm_timeout": "request_timeout", "llm_max_retries": "max_retries",
                       "llm_connect_timeout": "connect_timeout", "llm_first_event_timeout": "first_event_timeout",
                       "llm_stream_idle_timeout": "stream_idle_timeout", "llm_stream_chat": "stream_chat"}
            allowed = {"model", "provider", "api_key", "auth_token", "base_url", "wire_protocol", "context_window",
                       "provider_error_time_budget", "request_pool", "request_capacity", *aliases}
            self._llm = LLMClient(**{aliases.get(key, key): value for key, value in self._config.items()
                                    if key in allowed and (value is not None or key == "context_window")})
        return self._llm

    def options(self) -> dict[str, Any]:
        return {"strategies": self._pool.strategy_names, "isolation": self._pool.default_isolation,
                "metadata": tuple(_METADATA),
                "requires_thinking_prefix": None,
                "supports_request_admission": self._config.get("request_pool") is not None,
                "supports_response_session_identity": self._config.get("wire_protocol") == "responses"}

    async def poll(self) -> None:
        try:
            requests = json.loads(await self._environment.read_file(posixpath.join(self._directory, "requests.json")))
        except FileNotFoundError:
            return
        if not isinstance(requests, dict):
            raise ValueError("invalid child bridge requests")
        for identity, request in requests.items():
            if not identity.isdecimal() or len(identity) > 12 or not isinstance(request, dict):
                raise ValueError("invalid child bridge request identity")
            if identity not in self._tasks:
                self._tasks[identity] = asyncio.create_task(self._respond(identity, request))
            if request.get("cancel"):
                self._tasks[identity].cancel()
        for identity, task in tuple(self._tasks.items()):
            if identity not in requests and task.done():
                task.result()
                del self._tasks[identity]

    async def _respond(self, identity: str, request: dict[str, Any]) -> None:
        try:
            reply = {"value": await self._dispatch(identity, request)}
        except BaseException as exc:
            if not isinstance(exc, (Exception, asyncio.CancelledError)):
                raise
            error = {"type": type(exc).__name__, "message": str(exc)}
            for name in ("usage", "status_code", "code", "param", "body"):
                value = getattr(exc, name, None)
                if value is not None:
                    error[name] = to_plain_data(value)
            reply = {"error": error}
        await self._environment.write_file(posixpath.join(self._directory, f"{identity}.json"), json.dumps(reply))

    async def _dispatch(self, identity: str, request: dict[str, Any]) -> Any:
        action, arguments = request["action"], request.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("bridge arguments must be an object")
        if action == "metadata":
            name = arguments["name"]
            if name == "requires_thinking_prefix":
                return self._model().requires_thinking_prefix
            if name not in _METADATA:
                raise ValueError("unknown model metadata operation")
            return getattr(self._model(), name)(*arguments.get("args", []), **arguments.get("kwargs", {}))
        if action == "complete":
            if set(arguments) - _COMPLETION:
                raise ValueError("unknown completion controls")
            notifications = []
            if getattr(self._model(), "supports_request_admission", False):
                def admitted() -> None:
                    notifications.append(asyncio.create_task(self._environment.write_file(
                        posixpath.join(self._directory, f"{identity}.admitted"), "ready",
                    )))
                arguments["on_admitted"] = admitted
            try:
                return to_plain_data(await self._model().complete(**arguments))
            finally:
                if notifications:
                    await await_owned_operation(asyncio.gather(*notifications), propagate_cancellation=True)
        handle = request.get("environment", 0)
        if isinstance(handle, bool) or not isinstance(handle, int) or handle not in self._environments:
            raise ValueError("unknown environment for this authored run")
        environment = self._environments[handle]
        if action == "acquire":
            if set(arguments) != {"strategy", "label"}:
                raise ValueError("acquire accepts a strategy and label")
            acquired = await self._pool.acquire(arguments["label"], source=environment,
                                                isolation=arguments["strategy"])
            self._next_environment += 1
            self._environments[self._next_environment] = acquired
            return {"handle": self._next_environment, "workspace": acquired.workspace,
                    "process_isolated": bool(getattr(acquired, "process_isolated", False))}
        if action == "release":
            if arguments or handle == 0:
                raise ValueError("release needs an owned child environment")
            await self._pool.release_env(environment)
            del self._environments[handle]
            return None
        if action not in _ENVIRONMENT:
            raise ValueError("unknown child environment operation")
        if action == "exec_cmd":
            if not self._allow_unisolated_shell and not getattr(environment, "process_isolated", False):
                raise PermissionError("child command requires process isolation")
            from opencollab.bootstrap.container import build_workspace_safety_policy
            policy = build_workspace_safety_policy(environment)
            if policy is not None:
                await policy.check_cmd_interactive(arguments["cmd"], self._runtime.confirm_fn())
            if arguments.get("timeout", 120) is None:
                arguments["timeout"] = math.inf
            if "input_bytes" in arguments:
                arguments["input_bytes"] = base64.b64decode(arguments["input_bytes"], validate=True)
        result = await getattr(environment, action)(**arguments)
        if isinstance(result, (ExecResult, TextFileRange)):
            result = asdict(result)
            if result.get("stdout_bytes") is not None:
                result["stdout_bytes"] = base64.b64encode(result["stdout_bytes"]).decode()
        return result

    async def aclose(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        results = await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        await self._pool.release()
        if self._llm is not None:
            await self._llm.close()
        failures = [item for item in results if isinstance(item, Exception)]
        if failures:
            raise OSError("child bridge response persistence failed") from failures[0]


class BridgeClient:
    def __init__(self, artifacts: Path, options: dict[str, Any]) -> None:
        self.directory = artifacts / "bridge"
        self.directory.resolve().relative_to(Path.cwd().resolve())
        self.directory.mkdir(parents=True, exist_ok=True)
        self.options = options
        self._next = 0
        self._pending: dict[str, Any] = {}

    def _publish(self) -> None:
        _write_json(self.directory / "requests.json", self._pending)

    def _begin(self, action: str, arguments: dict[str, Any], environment: int) -> str:
        self._next += 1
        identity = str(self._next)
        self._pending[identity] = {"action": action, "arguments": arguments, "environment": environment}
        self._publish()
        return identity

    def _result(self, identity: str) -> Any:
        path = self.directory / f"{identity}.json"
        reply = json.loads(path.read_text(encoding="utf-8"))
        del self._pending[identity]
        self._publish()
        path.unlink()
        if "error" in reply:
            raise _restore_error(reply["error"])
        return reply["value"]

    def metadata(self, name: str, *args: Any, **kwargs: Any) -> Any:
        identity = self._begin("metadata", {"name": name, "args": args, "kwargs": kwargs}, 0)
        while not (self.directory / f"{identity}.json").exists():
            time.sleep(0.01)
        return self._result(identity)

    async def call(self, action: str, arguments: dict[str, Any], environment: int = 0,
                   *, on_admitted: Any = None) -> Any:
        identity = self._begin(action, arguments, environment)
        try:
            while not (self.directory / f"{identity}.json").exists():
                if on_admitted is not None and (self.directory / f"{identity}.admitted").exists():
                    on_admitted()
                    on_admitted = None
                await asyncio.sleep(0.01)
            return self._result(identity)
        except asyncio.CancelledError:
            self._pending[identity]["cancel"] = True
            self._publish()
            raise

    def provider(self, strategy: str, **_kwargs: Any) -> Any:
        client = self

        class Pool:
            async def acquire(self, role: str, *, source: Any = None) -> Any:
                handle = source.handle if isinstance(source, BridgeEnvironment) else 0
                result = await client.call("acquire", {"strategy": strategy, "label": role}, handle)
                return BridgeEnvironment(client, result)

            async def release_env(self, environment: Any) -> None:
                await environment.cleanup()

            async def release(self) -> None:
                pass

        return Pool()


class BridgeEnvironment(Environment):
    def __init__(self, client: BridgeClient, options: dict[str, Any]) -> None:
        super().__init__()
        self.client, self.handle = client, options["handle"]
        self.workspace, self.process_isolated = options["workspace"], options["process_isolated"]
        self._released = False

    def __getattr__(self, name: str) -> Any:
        if name not in _ENVIRONMENT:
            raise AttributeError(name)
        async def invoke(**kwargs: Any) -> Any:
            return await self.client.call(name, kwargs, self.handle)
        return invoke

    async def exec_cmd(self, cmd: str, timeout: float = 120.0, *, input_bytes: bytes | None = None) -> ExecResult:
        arguments: dict[str, Any] = {"cmd": cmd, "timeout": timeout if math.isfinite(timeout) else None}
        if input_bytes is not None:
            arguments["input_bytes"] = base64.b64encode(input_bytes).decode()
        result = await self.client.call("exec_cmd", arguments, self.handle)
        if result.get("stdout_bytes") is not None:
            result["stdout_bytes"] = base64.b64decode(result["stdout_bytes"])
        return ExecResult(**result)

    async def read_file(self, path: str) -> str:
        return await self.client.call("read_file", {"path": path}, self.handle)

    async def read_text_range(self, path: str, **kwargs: Any) -> TextFileRange:
        return TextFileRange(**await self.client.call("read_text_range", {"path": path, **kwargs}, self.handle))

    async def write_file(self, path: str, content: str) -> None:
        await self.client.call("write_file", {"path": path, "content": content}, self.handle)

    async def write_temp_file(self, content: str, *, prefix: str, suffix: str = ".tmp") -> str:
        return await self.client.call("write_temp_file", {"content": content, "prefix": prefix, "suffix": suffix},
                                      self.handle)

    async def remove_file(self, path: str) -> None:
        await self.client.call("remove_file", {"path": path}, self.handle)

    async def cleanup(self) -> None:
        if not self._released:
            await self.client.call("release", {}, self.handle)
            self._released = True


class BridgeLLM:
    def __init__(self, client: BridgeClient) -> None:
        self.client = client
        self.supports_response_session_identity = client.options["supports_response_session_identity"]
        self.supports_request_admission = client.options["supports_request_admission"]

    @property
    def requires_thinking_prefix(self) -> bool:
        return self.client.metadata("requires_thinking_prefix")

    def __getattr__(self, name: str) -> Any:
        if name not in self.client.options["metadata"]:
            raise AttributeError(name)
        return lambda *args, **kwargs: self.client.metadata(name, *args, **kwargs)

    async def complete(self, messages: list[dict[str, Any]], *, on_admitted: Any = None, **kwargs: Any) -> LLMResponse:
        result = await self.client.call("complete", {"messages": messages, **kwargs}, on_admitted=on_admitted)
        result["usage"] = Usage(**result["usage"])
        return LLMResponse(**result)

    async def close(self) -> None:
        pass
