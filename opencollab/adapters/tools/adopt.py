"""Adopt an existing commit through the configured command execution policy.

Checkout shares the shell process-isolation setting and command confirmation.
Initialized submodules follow the selected commit's gitlinks.
The existing Git configuration checks reject filters and redirected worktrees,
and hooks are disabled for the operation. The result compares the commit with
the first HEAD this tool observed.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from typing import Any

from opencollab.adapters._git_patch_bytes import _complete
from opencollab.adapters.git_patch import _git_command_and_config_guard
from opencollab.adapters.tools._output import require_positive_int, truncate
from opencollab.adapters.tools.base import Tool
from opencollab.application.patch_paths import decode_git_c_path
from opencollab.application.tool_execution import ToolRuntime

MAX_DIFF_CHARS = 8_000
MAX_ERROR_CHARS = 2_000
#: A full or abbreviated commit id, and nothing git would read as anything else:
#: no ref names, no revision syntax, no option.
_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")


async def _check_submodule_checkout(environment: Any, git: str, module: str, target: str) -> None:
    prefix = f"cd -- {shlex.quote(module)} && "
    ignore_case = _complete(await environment.exec_cmd(
        f"{prefix}{git} config --type=bool --get core.ignorecase", timeout=30,
    ), "submodule filename matching", allowed=(0, 1)).strip() == "true"
    magic = "icase,literal" if ignore_case else "literal"
    select_paths = (
        'adopt_count=$#; [ "$adopt_count" -gt 0 ] || exit 0; '
        'while [ "$adopt_count" -gt 0 ]; do adopt_path=$1; shift; '
        f'set -- "$@" ":({magic})$adopt_path"; adopt_parent=$adopt_path; '
        'while [ "${adopt_parent%/*}" != "$adopt_parent" ]; do adopt_parent=${adopt_parent%/*}; '
        'if [ -L "$adopt_parent" ] || { [ -e "$adopt_parent" ] && [ ! -d "$adopt_parent" ]; }; then '
        f'set -- "$@" ":({magic})$adopt_parent"; fi; done; '
        'adopt_count=$((adopt_count - 1)); done; '
        f'{git} ls-files --others -z -- "$@"'
    )
    # read-tree preserves checkout's case-only renames. Its ignored-file overwrite
    # behavior needs an additional Git path query for files the index does not own.
    _complete(await environment.exec_cmd(
        'adopt_paths=$(mktemp) || exit 125; '
        'adopt_overlaps=$(mktemp) || { rm -f -- "$adopt_paths"; exit 125; }; '
        'trap \'rm -f -- "$adopt_paths" "$adopt_overlaps"\' EXIT; '
        f"{prefix}{git} read-tree --dry-run -m -u --no-recurse-submodules HEAD {target} && "
        f"{git} diff --name-only --no-renames --no-ext-diff --no-textconv --no-color "
        f"--diff-filter=ACMT -z HEAD {target} > \"$adopt_paths\" && "
        f"xargs -0 -s 32768 sh -c {shlex.quote(select_paths)} sh "
        '< "$adopt_paths" > "$adopt_overlaps" && '
        'if [ -s "$adopt_overlaps" ]; then '
        "echo 'untracked submodule files would be overwritten by checkout' >&2; exit 125; fi",
        timeout=30,
    ), f"submodule checkout simulation {module}")


async def _submodule_checkout_options(environment: Any, git: str, commit: str) -> str:
    root = _complete(await environment.exec_cmd(f"{git} rev-parse --show-toplevel", timeout=30),
                     "checkout repository").removesuffix("\n")
    pending = [(root, commit)]
    active_keys = set()
    _, module_guard = _git_command_and_config_guard(allow_core_worktree=True)
    while pending:
        repository, revision = pending.pop()
        repository_prefix = f"cd -- {shlex.quote(repository)} && "
        configured = _complete(await environment.exec_cmd(
            f"{repository_prefix}{git} config --null --name-only --get-regexp '^submodule\\..*\\.active$'",
            timeout=30,
        ), "submodule activation settings", allowed=(0, 1))
        active_keys.update(key for key in configured.split("\0") if key)
        entries = _complete(await environment.exec_cmd(
            'adopt_tree=$(mktemp) || exit 125; trap \'rm -f -- "$adopt_tree"\' EXIT; '
            f"{repository_prefix}{git} -c core.quotePath=true ls-tree -r {shlex.quote(revision)} "
            '> "$adopt_tree" && awk \'$1 == "160000"\' "$adopt_tree"', timeout=30,
        ), "checkout submodule tree")
        for record in entries.splitlines():
            metadata, path = record.split("\t", 1)
            _mode, _kind, target = metadata.split()
            relative = decode_git_c_path(path)
            previous = _complete(await environment.exec_cmd(
                f"{repository_prefix}{git} --literal-pathspecs ls-tree HEAD -- {shlex.quote(relative)}", timeout=30,
            ), "current submodule gitlink")
            if previous.startswith("160000 ") and previous.split("\t", 1)[0].split()[2] == target:
                continue
            module = posixpath.join(repository, relative)
            prefix = f"cd -- {shlex.quote(module)} && "
            actual_root, current_head = _complete(await environment.exec_cmd(
                f"{prefix}{git} rev-parse --show-toplevel && printf '\\0' && {git} rev-parse HEAD", timeout=30,
            ), f"initialized submodule {module}").rsplit("\0", 1)
            if actual_root.removesuffix("\n") != module:
                raise RuntimeError(f"submodule is not initialized at {module}")
            # Initialized modules declare core.worktree in their private metadata.
            # The root comparison above verifies its destination before reuse.
            if current_head.strip() != target:
                _complete(await environment.exec_cmd(f"{prefix}{module_guard}:", timeout=30),
                          f"submodule Git configuration {module}")
                _complete(await environment.exec_cmd(
                    f"{prefix}{git} cat-file -e {shlex.quote(target + '^{commit}')}", timeout=30,
                ), f"local submodule target {module}")
                await _check_submodule_checkout(environment, git, module, target)
            pending.append((module, target))
    return " -c submodule.active=." + "".join(f" -c {shlex.quote(key + '=true')}" for key in sorted(active_keys))


class AdoptTool(Tool):
    """Check out a teammate's commit in the tree that is read as the answer."""

    name = "adopt"
    description = (
        "Check out an existing commit in your repository -- the tree that is read "
        "as the answer -- given its sha (7 to 64 hex characters), and show what that "
        "commit changes from where this run started. "
        "Recursively updates initialized submodules with the selected commit. It uses the configured "
        "command policy and process isolation, and refuses when uncommitted changes would "
        "be overwritten. Calling it again with another sha replaces the first."
    )
    parameters = {
        "type": "object",
        "properties": {
            "sha": {
                "type": "string",
                "description": "The commit to check out, as a teammate sent it.",
            },
        },
        "required": ["sha"],
    }

    def __init__(
        self, max_diff_chars: int = MAX_DIFF_CHARS, *, require_process_isolation: bool = True
    ):
        self.max_diff_chars = require_positive_int(max_diff_chars, "max_diff_chars")
        self.require_process_isolation = require_process_isolation
        self._start: str | None = None

    async def execute_with_runtime(
        self,
        params: dict[str, Any],
        runtime: ToolRuntime,
    ) -> str:
        env = runtime.environment
        if env is None:
            return "Error: no execution environment available."
        sha = str(params.get("sha") or "").strip()
        if not _SHA.match(sha):
            return (
                f"Error: {sha!r} is not a commit sha. Pass the 7-64 hex characters "
                "a teammate sent you; ref names and revision syntax are not accepted."
            )

        if self.require_process_isolation and not getattr(env, "process_isolated", False):
            return "Error: adopt is disabled because this execution environment does not provide an OS process sandbox."
        git_command, config_check = _git_command_and_config_guard()
        git_command += " -c core.hooksPath=/dev/null -c core.fsmonitor=false"
        checkout_command = config_check + (
            f"{git_command} checkout --quiet --detach --recurse-submodules --no-overwrite-ignore {sha}"
        )
        if runtime.safety_policy is not None:
            await runtime.safety_policy.check_cmd_interactive(checkout_command, runtime.confirm_fn())
        checked = await env.exec_cmd(config_check, timeout=30)
        if checked.returncode != 0:
            return "Error: adopt refused unsafe Git configuration.\n" + truncate(
                checked.stderr or checked.stdout, MAX_ERROR_CHARS
            )

        if self._start is None:
            head = await env.exec_cmd(f"{git_command} rev-parse --verify HEAD", timeout=30)
            if head.returncode != 0:
                return "Error: this repository has no HEAD to start from."
            self._start = head.stdout.strip()

        resolved = await env.exec_cmd(
            f"{git_command} rev-parse --verify --quiet {sha}^{{commit}}", timeout=30
        )
        commit = resolved.stdout.strip()
        if resolved.returncode != 0 or not commit.startswith(sha.lower()):
            return f"Error: no commit {sha} in this repository's object store."

        try:
            submodule_options = await _submodule_checkout_options(env, git_command, commit)
        except RuntimeError as exc:
            return "Error: git checkout refused; HEAD is unchanged.\n" + truncate(str(exc), MAX_ERROR_CHARS)

        checkout = await env.exec_cmd(
            config_check + (
                f"{git_command}{submodule_options} checkout --quiet --detach "
                f"--recurse-submodules --no-overwrite-ignore {commit}"
            ), timeout=900
        )
        if checkout.returncode != 0:
            error = (checkout.stderr or checkout.stdout).strip()
            return "Error: git checkout refused; HEAD is unchanged.\n" + truncate(
                error, MAX_ERROR_CHARS
            )

        subject = await env.exec_cmd(f"{git_command} --no-pager log -1 --format=%s {commit}", timeout=30)
        stat = await env.exec_cmd(
            f"{git_command} --no-pager diff --stat --no-ext-diff --no-textconv {self._start} {commit}", timeout=30
        )
        diff = await env.exec_cmd(
            f"{git_command} --no-pager diff --no-ext-diff --no-textconv {self._start} {commit}", timeout=30
        )
        audit_problems = []
        for label, result in (("commit subject", subject), ("change summary", stat), ("full diff", diff)):
            if result.returncode != 0:
                error = (result.stderr or result.stdout).strip()
                detail = f": {truncate(error, 300)}" if error else ""
                audit_problems.append(f"{label} command failed{detail}")
            elif getattr(result, "stdout_truncated", False):
                audit_problems.append(f"{label} output was truncated")
        checkout_message = f"Checked out {commit}. HEAD is now that commit."
        if audit_problems:
            return "\n\n".join(
                (
                    checkout_message,
                    "Change summary unavailable or incomplete: " + "; ".join(audit_problems),
                )
            )

        parts = [
            f"Checked out {commit} ({subject.stdout.strip()}). "
            "HEAD is now that commit, and its tree is the one read as the answer.",
            f"Changes from where this run started ({self._start[:12]}):",
            stat.stdout.strip() or "(none)",
        ]
        if diff.stdout.strip():
            parts.append(truncate(diff.stdout.strip(), self.max_diff_chars))
        return "\n\n".join(parts)
