"""Native tools with execution observations for the weave workflow."""

from __future__ import annotations

import math
import shlex
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from opencollab.tools import evidence_tools, profile_tool_limits, profile_tool_names


class _Proxy:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

class _WriteObserver(_Proxy):
    def __init__(self, delegate: Any, observed: Callable[[str | None], None]) -> None:
        super().__init__(delegate)
        self._observed = observed

    def record_write(self, *, completed: bool, changed: bool | None, path: str | None = None) -> None:
        if self._delegate is not None:
            self._delegate.record_write(completed=completed, changed=changed, path=path)
        if completed and changed is True:
            self._observed(path)

class _ProgressTool(_Proxy):
    def __init__(self, delegate: Any, observed: Callable[[str | None], None]) -> None:
        super().__init__(delegate)
        self._observed = observed

    async def execute_with_runtime(self, params: dict[str, Any], runtime: Any) -> str:
        observed = _Proxy(runtime)
        observed.observations = _WriteObserver(getattr(runtime, "observations", None), self._observed)
        before = len(getattr(self._delegate, "verification_records", ()))
        result = await self._delegate.execute_with_runtime(params, observed)
        records = getattr(self._delegate, "verification_records", ())[before:]
        if any(record.get("verified") for record in records):
            self._observed(None)
        return result

def _coding_tools(observed: Callable[[str | None], None]) -> tuple[Any, ...]:
    tools = evidence_tools(
        *profile_tool_names("single2"), headless=False, limits=profile_tool_limits("single2"),
    )
    return tuple(_ProgressTool(tool, observed) for tool in tools)

class _CaptureEnvironment(_Proxy):
    def __init__(self, delegate: Any, results: list[Any]) -> None:
        super().__init__(delegate)
        self._results = results

    async def exec_cmd(self, command: str, timeout: float = 120.0) -> Any:
        result = await self._delegate.exec_cmd(command, timeout=timeout)
        self._results.append(result)
        return result

class _CommandProbe(_Proxy):
    def __init__(self) -> None:
        super().__init__(evidence_tools("bash", headless=False)[0])
        self.results: list[Any] = []

    async def execute_with_runtime(self, params: dict[str, Any], runtime: Any) -> str:
        captured = _Proxy(runtime)
        environment = runtime.environment
        captured.environment = _CaptureEnvironment(environment, self.results) if environment is not None else None
        return await self._delegate.execute_with_runtime(params, captured)

    def prepare_command(self, command: str) -> str:
        return self._delegate.with_pass_reporting(command)

    def requires_test_evidence(self, command: str) -> bool:
        return any(self._delegate.invokes_pytest(_command_words(shlex.split(segment)))
                   for segment in _command_segments(command))

def _command_spec(value: Any) -> tuple[str, str | None]:
    if isinstance(value, str):
        return value, None
    if not isinstance(value, Mapping) or set(value) - {"command", "expected_output"}:
        raise ValueError("each check command must be text or a command/expected_output object")
    command, expected = value.get("command"), value.get("expected_output")
    if not isinstance(command, str) or not isinstance(expected, str) or not expected.strip():
        raise ValueError("a command probe requires command text and nonempty expected_output")
    return command, expected

def _command_segments(command: str) -> list[str]:
    quote = None
    escaped = False
    start = 0
    segments = []
    for index, char in enumerate(command):
        if index < start:
            continue
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif char == quote:
            quote = None
        elif quote is None and char in {"'", '"'}:
            quote = char
        elif quote is None and char in "(){}":
            return []
        elif quote is None and char == "#" and (index == 0 or command[index - 1].isspace()):
            if any(line.strip() for line in command[index:].splitlines()[1:]):
                return []
            command = command[:index]
            break
        elif quote != "'" and (char == "`" or command[index:index + 2] == "$("):
            return []
        elif quote is None and command[index:index + 2] == "&&":
            if command[start:index].strip():
                segments.append(command[start:index])
            start = index + 2
        elif quote is None and command[index:index + 3] == "<<<":
            return []
        elif quote is None and char in ";|&":
            return []
        elif quote is None and char in "\n\r" and command[:index].strip() and command[index:].strip():
            return []
    if command[start:].strip():
        segments.append(command[start:])
    return segments

_WRAPPERS = frozenset({
    "exec", "eval", "nice", "nohup", "time", "timeout", "setsid", "sudo",
    "ionice", "chrt", "taskset", "flock", "xargs", "stdbuf", "busybox",
    "unbuffer", "script",
})
_SHELLS = frozenset({"bash", "sh", "dash", "zsh", "ksh", "ash"})
_STATIC_COMMANDS = frozenset({"true", "false", ":", "echo", "printf", "test", "["})
_TEXT_TOOLS = frozenset({"awk", "gawk", "nawk", "mawk", "sed", "jq"})
_INLINE_FLAGS = {
    "perl": ("-e",),
    "ruby": ("-e",),
    "node": ("-e", "--eval"),
    "nodejs": ("-e", "--eval"),
    "php": ("-r",),
    "lua": ("-e",),
}
_FILE_READ_MARKERS = ("open(", "Path(", "stdin", "argv", "read_text", "read_bytes", "readFile")


def _command_basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _effective_tokens(tokens: list[str]) -> list[str] | None:
    """Return the program argv, or None when a wrapper can hide a printer.

    Assignments and ``env`` / ``command`` / ``builtin`` are skipped, as before.
    Their options stay in place, so ``env -i python`` remains unverified.
    ``exec``, ``bash -c``, ``busybox``, and similar wrappers are rejected
    outright: peeling their options can mistake the next word for the program.
    """
    index = 0
    while index < len(tokens):
        token = tokens[index]
        variable, separator, _ = token.partition("=")
        if separator and variable.isidentifier():
            index += 1
            continue
        name = _command_basename(token)
        if name in {"env", "command", "builtin", "--"}:
            index += 1
            continue
        if name in _WRAPPERS or name in _SHELLS:
            return None
        return tokens[index:]
    return []


def _command_words(tokens: list[str]) -> list[str]:
    words = _effective_tokens(tokens)
    return [] if words is None else words

def _has_behavior_command(tokens: list[str]) -> bool:
    words = _effective_tokens(tokens)
    if not words or words[0].startswith("-"):
        return False
    name = _command_basename(words[0])
    return name not in _STATIC_COMMANDS and name not in _TEXT_TOOLS


def _static_printer_supplies(command: str, expected: str) -> bool:
    """True when a printer or one-liner already contains the expected text.

    ``echo CHECK_OK && python -c 'exit(0)'`` would otherwise match CHECK_OK
    from the printer while the second command only has to exit zero.
    """
    if not expected:
        return False
    for segment in _command_segments(command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            return True
        words = _effective_tokens(tokens)
        # A here-string, assignment, or quoted program can carry the expected
        # text. ``echo 'Starting check' && python check.py`` does not.
        if any(expected in token for token in tokens):
            return True
        script = _inline_script(words) if words else None
        if script is not None and not any(marker in script for marker in _FILE_READ_MARKERS):
            return True
    return False


def _inline_script(words: list[str]) -> str | None:
    """Return an interpreter one-liner, leaving ``python -m pytest -c`` alone."""
    name = _command_basename(words[0])
    if name.startswith("python"):
        if "-m" in words or "-c" not in words:
            return None
        index = words.index("-c")
        return words[index + 1] if index + 1 < len(words) else ""
    for flag in _INLINE_FLAGS.get(name, ()):
        if flag in words:
            index = words.index(flag)
            return words[index + 1] if index + 1 < len(words) else ""
    return None

def _verification_mode(command: str) -> bool:
    try:
        segments = [shlex.split(segment) for segment in _command_segments(command)]
    except ValueError:
        return False
    return any(_has_behavior_command(tokens) for tokens in segments) and not any(
        token in {"-h", "--help", "--collect-only", "--co", "--version"}
        or token.startswith("--collect-only=") for tokens in segments for token in tokens
    )

async def _verify_commands(ctx: Any, commands: Sequence[Any], seconds: float | None) -> dict[str, Any]:
    deadline = math.inf if seconds is None else time.monotonic() + seconds
    checks = []
    for value in commands:
        command, expected = _command_spec(value)
        row: dict[str, Any] = {"command": command, "ok": False, "executed": False}
        remaining = deadline - time.monotonic()
        if not _verification_mode(command) or remaining <= 0:
            row["reason"] = "unverified command or exhausted verification time"
            checks.append(row)
            continue
        tool = _CommandProbe()
        executed_command = tool.prepare_command(command)
        if executed_command != command:
            row["executed_command"] = executed_command
        params: dict[str, Any] = {"command": executed_command}
        if math.isfinite(remaining):
            params["timeout"] = remaining
        row["output"] = await ctx.execute_verification(tool, params)
        records = list(tool.verification_records)
        row["test_records"] = records
        if tool.results:
            result = tool.results[-1]
            row["executed"] = True
            row["exit_code"] = result.returncode
            truncated = any(getattr(result, key, False) for key in (
                "stdout_truncated", "stderr_truncated", "stdout_dropped_bytes", "stderr_dropped_bytes",
            ))
            completed = result.returncode == 0 and not truncated and not getattr(result, "timed_out", False)
            if records:
                row["ok"] = completed and all(record.get("verified") is True for record in records)
            requires_test_evidence = tool.requires_test_evidence(command)
            if not records and (expected is None or requires_test_evidence):
                row["reason"] = ("pytest command requires per-target test evidence from a supported invocation"
                                 if requires_test_evidence else
                                 "command requires parsed test evidence or an explicit expected_output probe")
            if expected is not None:
                output = result.stdout + ("\n" + result.stderr if result.stderr else "")
                row["expected_output"] = expected
                row["matched"] = expected in output
                row["ok"] = (row["ok"] if records else not requires_test_evidence) and completed and row["matched"]
                if row["ok"] and _static_printer_supplies(command, expected):
                    row["ok"] = False
                    row["reason"] = "expected output is supplied by a static printer"
        checks.append(row)
    return {
        "ok": bool(checks) and all(row["ok"] for row in checks),
        "executed": bool(checks) and all(row["executed"] for row in checks),
        "checks": checks,
    }
