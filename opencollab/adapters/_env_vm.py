from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import math
import posixpath
import re
import shlex
import uuid
from collections.abc import AsyncIterator
from typing import Any

from opencollab import __version__
from opencollab.adapters._env_base import ENV_FILE_WRITE_LIMIT_BYTES, Environment, ExecResult, TextFileRange
from opencollab.adapters._env_process import ProcessCleanupError, run_process
from opencollab.application.async_timeout import await_owned_operation
from opencollab.domain.vm import VMConnection, validate_vm_path

_MESSAGE_LIMIT = 16 * 1024 * 1024
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


class VMOperationError(RuntimeError):
    def __init__(self, message: str, *, operation_id: str, state: str) -> None:
        super().__init__(message)
        self.operation_id = operation_id
        self.state = state


class VMTransportError(VMOperationError):
    def __init__(self, message: str, *, operation_id: str, returncode: int | None = None) -> None:
        super().__init__(message, operation_id=operation_id, state="unknown")
        self.transport_returncode = returncode


class VMEnvironment(Environment):
    """Borrow an existing Linux guest with the same installed OC version.

    Operation records remain in state_directory/run_id for recovery after a
    controller or SSH connection exits. cleanup stops owned operations and
    removes owned temporary files while preserving the guest and records.
    """

    process_isolated = True

    def __init__(self, connection: VMConnection, workspace: str, *, state_directory: str, run_id: str | None = None):
        super().__init__()
        self.connection = connection
        self.workspace = validate_vm_path(workspace)
        self.source_workspace = self.workspace
        self.state_directory = validate_vm_path(state_directory)
        if self.state_directory == self.workspace or self.state_directory.startswith(self.workspace + "/"):
            raise ValueError("VM operation state must be outside the tool workspace")
        self.run_id = run_id or uuid.uuid4().hex
        if not _COMPONENT.fullmatch(self.run_id):
            raise ValueError("VM run_id must be a simple path component")
        self._identity: dict[str, Any] | None = None
        self._setup_lock = asyncio.Lock()
        self._operations: set[str] = set()
        self._uncertain: set[str] = set()
        self._temporary_files: dict[str, list[int]] = {}

    @property
    def revoked(self) -> bool:
        return self._aborted or bool(self._uncertain)

    @property
    def pending_operations(self) -> tuple[str, ...]:
        return tuple(sorted(self._operations))

    def _ensure_active(self) -> None:
        super()._ensure_active()
        if self._uncertain:
            operation = sorted(self._uncertain)[0]
            raise VMOperationError("Recover uncertain VM operations before starting further work",
                                   operation_id=operation, state="unknown")

    def _ssh_command(self) -> list[str]:
        connection = self.connection
        command = ["ssh", "-T", "-F", "/dev/null", "-o", "BatchMode=yes",
                   "-o", "StrictHostKeyChecking=yes", "-o", "ControlMaster=no", "-o", "ControlPath=none",
                   "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
                   "-o", f"ConnectTimeout={math.ceil(connection.connect_timeout)}",
                   "-p", str(connection.port)]
        if connection.identity_file is not None:
            command += ["-i", connection.identity_file, "-o", "IdentitiesOnly=yes"]
        if connection.known_hosts_file is not None:
            command += ["-o", f"UserKnownHostsFile={connection.known_hosts_file}"]
        command += ["-l", connection.user, connection.host,
                    shlex.join([connection.python, "-m", "opencollab.adapters._vm_remote", "rpc",
                                self.state_directory, self.run_id])]
        return command

    async def _rpc(self, payload: dict[str, Any]) -> Any:
        operation_id = payload.get("operation_id", "setup")
        try:
            result = await run_process(self._ssh_command(), shell=False,
                                       timeout=self.connection.connect_timeout + 15,
                                       input_bytes=json.dumps(payload).encode(), output_limit=_MESSAGE_LIMIT)
        except asyncio.TimeoutError as exc:
            raise VMTransportError("VM SSH request timed out", operation_id=operation_id) from exc
        if result.returncode != 0 or result.stdout_dropped_bytes or result.stderr_dropped_bytes:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise VMTransportError(f"VM SSH request failed with status {result.returncode}: {detail}",
                                   operation_id=operation_id, returncode=result.returncode)
        try:
            response = json.loads(result.stdout)
        except (ValueError, UnicodeError) as exc:
            raise VMTransportError("VM helper response was incomplete", operation_id=operation_id) from exc
        if not response["ok"]:
            error = {"ValueError": ValueError, "PermissionError": PermissionError,
                     "FileNotFoundError": FileNotFoundError,
                     "FileExistsError": FileExistsError, "UnicodeDecodeError": UnicodeError
                     }.get(response["error"], OSError)
            raise error(response["message"])
        return response["value"]

    async def setup(self, mount_dir: str | None = None) -> str:
        self._ensure_active()
        if mount_dir is not None:
            raise ValueError("mount_dir is supported only by container environments")
        async with self._setup_lock:
            if self._identity is None:
                response = await self._rpc({"method": "setup", "workspace": self.workspace})
                if response["version"] != __version__:
                    raise RuntimeError(f"VM OpenCollab version must match controller {__version__}")
                self._identity = response["identity"]
                self.workspace = self._identity["workspace"]
                self.source_workspace = self.workspace
        return self.workspace

    async def _cancel_operation(self, operation_id: str) -> None:
        status = await self._rpc({"method": "cancel", "operation_id": operation_id})
        deadline = asyncio.get_running_loop().time() + 12
        while status["status"] in {"running", "preparing", "cleanup_failed"}:
            if asyncio.get_running_loop().time() >= deadline:
                raise ProcessCleanupError(f"VM operation cleanup did not quiesce, operation {operation_id}")
            await asyncio.sleep(0.05)
            status = await self._rpc({"method": "query", "operation_id": operation_id})
        if status["status"] in {"unknown", "cleanup_failed"}:
            self._uncertain.add(operation_id)
            raise ProcessCleanupError(f"VM operation requires recovery, operation {operation_id}")
        self._operations.discard(operation_id)
        self._uncertain.discard(operation_id)

    async def _wait(self, operation_id: str, status: dict[str, Any]) -> Any:
        while status["status"] in {"running", "preparing"}:
            await asyncio.sleep(0.05)
            status.update(await self._rpc({"method": "query", "operation_id": operation_id}))
        state = status["status"]
        if state in {"unknown", "cleanup_failed"}:
            self._uncertain.add(operation_id)
            raise VMOperationError(status.get("message", "VM operation state requires recovery"),
                                   operation_id=operation_id, state=state)
        self._operations.discard(operation_id)
        self._uncertain.discard(operation_id)
        if state == "cancelled":
            raise VMOperationError("VM operation was cancelled", operation_id=operation_id, state=state)
        if state == "failed":
            error = {"ValueError": ValueError, "PermissionError": PermissionError,
                     "FileNotFoundError": FileNotFoundError, "FileExistsError": FileExistsError,
                     "UnicodeDecodeError": UnicodeError}.get(status["error"], OSError)
            raise error(status["message"])
        return status["value"]

    async def _operate(self, action: str, args: dict[str, Any], operation_id: str | None = None,
                       *, require_active: bool = True) -> Any:
        if require_active:
            self._ensure_active()
            await self.setup()
        operation_id = operation_id or uuid.uuid4().hex
        if not isinstance(operation_id, str) or not _COMPONENT.fullmatch(operation_id):
            raise ValueError("VM operation_id must be a simple path component")
        self._operations.add(operation_id)
        payload = {"method": "start", "operation_id": operation_id,
                   "request": {"action": action, "args": args, "workspace": self.workspace, "identity": self._identity}}
        try:
            # Retrying submission retains the operation primary key and exact
            # request. The guest owns execution independently of the SSH client.
            for attempt in range(3):
                try:
                    status = await self._rpc(payload)
                    return await self._wait(operation_id, status)
                except VMTransportError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0.1)
        except VMTransportError:
            self._uncertain.add(operation_id)
            raise
        except asyncio.CancelledError:
            try:
                await await_owned_operation(self._cancel_operation(operation_id))
            except BaseException as exc:
                self._uncertain.add(operation_id)
                raise ProcessCleanupError(f"VM cancellation requires recovery, operation {operation_id}") from exc
            raise

    async def exec_operation(self, operation_id: str, cmd: str, timeout: float | None = 120.0,
                             *, input_bytes: bytes | None = None) -> ExecResult:
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                    or math.isnan(timeout) or timeout <= 0):
            raise ValueError("timeout must be positive or None")
        if not isinstance(cmd, str) or "\0" in cmd:
            raise ValueError("VM command must be text without NUL bytes")
        if input_bytes is not None and not isinstance(input_bytes, bytes):
            raise TypeError("input_bytes must be bytes")
        encoded_input = base64.b64encode(input_bytes).decode() if input_bytes is not None else None
        value = await self._operate("exec", {"cmd": cmd, "timeout": timeout if timeout != math.inf else None,
                                            "input": encoded_input}, operation_id)
        return self._exec_result(value)

    @staticmethod
    def _exec_result(value: dict[str, Any]) -> ExecResult:
        value = dict(value)
        if value["stdout_bytes"] is not None:
            value["stdout_bytes"] = base64.b64decode(value["stdout_bytes"])
        return ExecResult(**value)

    async def exec_cmd(self, cmd: str, timeout: float | None = 120.0,
                       *, input_bytes: bytes | None = None) -> ExecResult:
        return await self.exec_operation(uuid.uuid4().hex, cmd, timeout, input_bytes=input_bytes)

    async def recover_operation(self, operation_id: str) -> Any:
        self._operations.add(operation_id)
        try:
            status = await self._rpc({"method": "query", "operation_id": operation_id})
            value = await self._wait(operation_id, status)
        except VMTransportError:
            self._uncertain.add(operation_id)
            raise
        action = status.get("action", "exec")
        if action == "temporary":
            self._temporary_files[status["args"]["path"]] = value
        elif action == "remove":
            self._temporary_files.pop(status["args"]["path"], None)
        return self._exec_result(value) if action == "exec" else value

    async def read_file(self, path: str) -> str:
        return await self._operate("read", {"path": path})

    async def read_text_range(self, path: str, *, offset: int, limit: int, max_chars: int) -> TextFileRange:
        return TextFileRange(**await self._operate("range", {"path": path, "offset": offset, "limit": limit,
                                                             "max_chars": max_chars}))

    async def write_file(self, path: str, content: str) -> None:
        await self.write_file_with_change(path, content)

    async def write_file_with_change(self, path: str, content: str) -> bool | None:
        if len(content.encode()) > ENV_FILE_WRITE_LIMIT_BYTES:
            raise ValueError("VM file exceeds write limit")
        return await self._operate("write", {"path": path, "content": content})

    async def write_temp_file(self, content: str, *, prefix: str, suffix: str = ".tmp") -> str:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", prefix) or not re.fullmatch(r"[A-Za-z0-9._-]+", suffix):
            raise ValueError("temporary file prefix and suffix must be simple path components")
        path = f"{prefix}{uuid.uuid4().hex}{suffix}"
        identity = await self._operate("temporary", {"path": path, "content": content})
        self._temporary_files[path] = identity
        return posixpath.join(self.workspace, path)

    async def remove_file(self, path: str) -> None:
        target = posixpath.relpath(path, self.workspace) if path.startswith("/") else posixpath.normpath(path)
        if target not in self._temporary_files:
            raise OSError(f"refusing to remove unowned VM temporary file {path}")
        await self._operate("remove", {"path": target, "identity": self._temporary_files[target]})
        self._temporary_files.pop(target)

    @contextlib.asynccontextmanager
    async def file_write_lock(self, path: str) -> AsyncIterator[None]:
        self._ensure_active()
        await self.setup()
        operation_id = uuid.uuid4().hex
        self._operations.add(operation_id)
        try:
            status = await self._rpc({"method": "start", "operation_id": operation_id,
                "request": {"action": "lock", "args": {}, "workspace": self.workspace, "identity": self._identity}})
            while status["status"] in {"preparing", "running"} and not status.get("lock_acquired"):
                await asyncio.sleep(0.05)
                status = await self._rpc({"method": "query", "operation_id": operation_id})
            if not status.get("lock_acquired"):
                await self._wait(operation_id, status)
                raise RuntimeError("VM edit lock exited before acquisition")
            yield None
        except VMTransportError:
            self._uncertain.add(operation_id)
            raise
        finally:
            if not self._uncertain:
                await await_owned_operation(self._cancel_operation(operation_id))

    async def cleanup(self) -> None:
        self.revoke()
        for operation_id in tuple(self._operations):
            try:
                await await_owned_operation(self._cancel_operation(operation_id), propagate_cancellation=True)
            except BaseException:
                self._uncertain.add(operation_id)
                raise
        for path in tuple(self._temporary_files):
            await self._operate("remove", {"path": path, "identity": self._temporary_files[path]},
                                require_active=False)
            self._temporary_files.pop(path)


__all__ = ["VMEnvironment", "VMOperationError", "VMTransportError"]
