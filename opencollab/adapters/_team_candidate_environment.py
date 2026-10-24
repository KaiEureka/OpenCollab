"""Strict team diff delivery over an existing candidate workspace lease."""

from __future__ import annotations

import shlex
from contextlib import AsyncExitStack
from typing import Any

from opencollab.adapters._candidate_physical import _has_worktree_conversions
from opencollab.adapters._git_patch_bytes import _complete, _complete_patch, _patch_temporary
from opencollab.adapters.git_patch import _git_command_and_config_guard, guarded_staged_diff_command


class _TeamCandidateEnvironment:
    """Delegate execution while retaining the team's strict patch evidence."""

    def __init__(self, lease: Any, source_workspace: str, *, source_owner: Any = None) -> None:
        self._lease = lease
        self._source_owner = source_owner
        self.source_workspace = source_workspace
        self._delivery_pending = False
        self.delivery_diff: str | None = None
        self.delivery_paths: tuple[str, ...] = ()

    @property
    def _delegate(self) -> Any:
        if self._lease.environment is None:
            raise RuntimeError("candidate environment setup has not completed")
        return self._lease.environment

    @property
    def workspace(self) -> str:
        if self._lease.environment is None:
            return self._lease.candidate_workspace
        return self._delegate.workspace

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    @property
    def diff_path_prefix(self) -> str:
        return self._lease.workspace_prefix.rstrip("/")

    @property
    def diff_base(self) -> str:
        return self._lease.base_revision

    def retain_changes(self) -> str:
        self._delivery_pending = True
        return self.recovery_location or self.workspace

    @property
    def recovery_location(self) -> str | None:
        if not self._delivery_pending:
            return None
        container = getattr(self._delegate, "container_reference", None)
        return f"{container}:{self.workspace}" if container is not None else self.workspace

    def _capture_environment(self, *, allow_delivered: bool = False) -> Any:
        delegate = self._delegate
        if not delegate.revoked:
            return delegate
        if not self._delivery_pending and not allow_delivered:
            delegate._ensure_active()
        owner = self._lease.base_environment
        owner._ensure_active()
        return owner

    async def get_diff(self) -> str:
        owner = self._capture_environment()
        self._delivery_pending = True
        root = self._lease.candidate_workspace
        result = await owner.exec_cmd(
            f"cd -- {shlex.quote(root)} && "
            + guarded_staged_diff_command(base_revision=self._lease.base_revision),
        )
        patch = await _complete_patch(
            result, "worktree diff extraction", owner, root,
            self._lease.base_revision, None, captured_blobs=True,
        )
        await self._lease._assert_physical_submodules()
        self._delivery_pending = False
        return patch

    async def capture_changes(self) -> str:
        validated = await self.get_diff()
        self._delivery_pending = True
        if self._lease.physical_base_revision is None and not (
            await _has_worktree_conversions(self._lease.base_environment, self._lease.candidate_workspace, strict=True)
        ):
            self.delivery_diff, self.delivery_paths = validated, ()
            self._delivery_pending = False
            return validated
        patch = await self._lease.capture_changes(force_physical=True)
        self.delivery_diff = self._lease.delivery_diff
        self.delivery_paths = self._lease.delivery_paths
        self._delivery_pending = False
        return patch

    async def diff_captured(self, before_patch: str, after_patch: str, *, physical: bool = False) -> str:
        """Subtract saved cumulative patches using the lease's original tree."""
        delegate = self._capture_environment(allow_delivered=True)
        root = self._lease.candidate_workspace
        base = (
            self._lease.physical_base_revision or self._lease.base_revision if physical else self._lease.base_revision
        )
        git, guard = _git_command_and_config_guard()
        async with AsyncExitStack() as stack:
            trees = []
            for patch in (before_patch, after_patch):
                patch_file = await stack.enter_async_context(_patch_temporary(
                    delegate, root, patch, prefix=".candidate-captured-patch-",
                ))
                index = await stack.enter_async_context(_patch_temporary(
                    delegate, root, "", prefix=".candidate-captured-index-",
                ))
                indexed_git = f"GIT_INDEX_FILE={shlex.quote(index)} {git}"
                command = (
                    f"cd -- {shlex.quote(root)} && {guard}"
                    f"{indexed_git} read-tree {shlex.quote(base)}"
                )
                if patch.strip():
                    command += (
                        f" && {indexed_git} apply --cached --binary --whitespace=nowarn -- "
                        f"{shlex.quote(patch_file)}"
                    )
                command += f" && {indexed_git} write-tree"
                trees.append(_complete(
                    await delegate.exec_cmd(command), "completed candidate tree capture",
                ).strip())
            result = await delegate.exec_cmd(
                f"cd -- {shlex.quote(root)} && {guard}{git} --no-pager diff "
                "--no-ext-diff --no-textconv --binary --full-index --no-color --unified=3 "
                f"--src-prefix=a/ --dst-prefix=b/ {shlex.quote(trees[0])} {shlex.quote(trees[1])} --"
            )
            return await _complete_patch(
                result, "completed candidate increment", delegate, root, trees[0], None,
                captured_blobs=True,
            )

    async def cleanup(self) -> None:
        if self._delivery_pending:
            raise RuntimeError(f"worktree changes await complete delivery; retained at {self.workspace}")
        await self._lease.cleanup()
        if self._source_owner is not None:
            await self._source_owner.cleanup()

    async def abort(self) -> None:
        # Stop processes and files before releasing any workspace. A failed
        # export continues to retain the physical checkout for recovery.
        await self._delegate.abort()
        await self.cleanup()
