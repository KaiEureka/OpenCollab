"""Create, track, and clean up per-agent worktree environments."""

from __future__ import annotations

import asyncio
import logging
import os
import posixpath
import shlex
import uuid
from collections.abc import Sequence
from contextlib import nullcontext
from contextvars import ContextVar

from opencollab.adapters._candidate_physical import _physical_paths
from opencollab.adapters._git_patch_bytes import _complete
from opencollab.adapters._team_candidate_environment import _TeamCandidateEnvironment
from opencollab.adapters.candidate_workspace import EnvCandidateWorkspace, _apply_increment
from opencollab.adapters.env import (
    ContainerWorktreeEnvironment,
    DockerEnvironment,
    DockerWorkspaceEnvironment,
    Environment,
    LocalEnvironment,
    WorktreeEnvironment,
)
from opencollab.application.async_timeout import await_owned_operation
from opencollab.application.exception_notes import add_exception_note
from opencollab.domain.identity import role_storage_slug, validate_role_identity

logger = logging.getLogger(__name__)

# Where per-agent checkouts go inside a container. Deliberately not under the
# repository root: an archive of that root is what a harness reads the run's
# result out of, and a worktree nested in it would arrive there as a directory
# full of files no agent wrote.
CONTAINER_WORKTREE_ROOT = "/tmp/opencollab-worktrees"

async def _finish_cleanup(operation):
    return await await_owned_operation(operation, propagate_cancellation=True)

class WorktreePool:
    """Lend isolated environments and track them for cleanup.

    With ``use_worktrees=False``, return ``LocalEnvironment(workspace)``.
    ``base_environment`` selects host worktrees by default or worktrees inside
    an attached container. Container worktrees avoid host mounts so bounded
    container archives remain usable for patch extraction.
    """

    def __init__(
        self,
        workspace: str,
        *,
        use_worktrees: bool,
        base_environment: Environment | None = None,
        owned_save_dir: str | None = None,
        enable_adoption: bool = False,
        owned_output_paths: Sequence[str] = (),
    ):
        self._workspace = workspace
        self._use_worktrees = use_worktrees
        self._base_environment = base_environment
        self._owned_save_dir = owned_save_dir
        self._enable_adoption = enable_adoption
        self._owned_output_paths = tuple(os.path.realpath(path) for path in owned_output_paths)
        self._delivery_lock = asyncio.Lock()
        self._owned_delivery_environment: LocalEnvironment | None = None
        self._envs: list[Environment] = []
        self._source_dependencies: dict[Environment, Environment] = {}
        self._lifecycle = asyncio.Condition()
        self._release_lock = asyncio.Lock()
        self._release_active = False
        self._generation = 0
        self._inflight_acquires = 0
        self._physical_delivery_paths: ContextVar[tuple[str, ...] | None] = ContextVar(
            "physical_delivery_paths", default=None,
        )

    async def acquire(self, role: str, *, source: Environment | None = None) -> Environment:
        """Lend an environment, optionally snapshotting a completed teammate's files."""
        role = validate_role_identity(role)
        base = self._base_environment if source is None else source
        if not self._use_worktrees:
            # Without isolation every agent works where the run works, which is
            # the supplied environment when there is one and the host workspace
            # otherwise. The shared environment is not tracked for cleanup here:
            # it belongs to whoever handed it in.
            if base is not None:
                return base
            env = LocalEnvironment(self._workspace)
            generation = await self._begin_acquire()
            return await self._finish_acquire(env, generation)

        branch = f"opencollab-{role_storage_slug(role)}-{uuid.uuid4().hex[:8]}"
        generation = await self._begin_acquire()
        env = None
        source_owner = None
        try:
            async with (self._delivery_lock if self._enable_adoption else nullcontext()):
                if self._enable_adoption or source is not None:
                    candidate_source = self._delivery_environment() if source is None else source
                    if source is not None:
                        if getattr(source, "revoked", False):
                            raise RuntimeError("review source environment is unavailable")
                        if getattr(source, "local_filesystem", False):
                            source_owner = LocalEnvironment(source.workspace)
                            candidate_source = source_owner
                        else:
                            candidate_source = (
                                source._delegate if isinstance(source, _TeamCandidateEnvironment) else source
                            )
                            if isinstance(candidate_source, DockerEnvironment):
                                container_id = candidate_source.container_reference
                                if container_id is None:
                                    raise RuntimeError("review source container identity is unavailable")
                                source_owner = DockerWorkspaceEnvironment(
                                    container_id=container_id, repo_root=candidate_source.workspace,
                                    command_prefix=candidate_source.command_prefix,
                                    timeout_returncode=candidate_source._timeout_returncode,
                                )
                                candidate_source = source_owner
                    probe = await candidate_source.exec_cmd(
                        f"git -C {shlex.quote(candidate_source.workspace)} rev-parse --show-toplevel",
                    )
                    if probe.returncode == 0:
                        root = _complete(probe, "team source repository").removesuffix("\n")
                        excluded = ()
                        if isinstance(candidate_source, LocalEnvironment):
                            excluded = tuple(
                                os.path.relpath(path, root).replace(os.sep, "/")
                                for path in self._owned_output_paths
                                if os.path.commonpath((path, root)) == root and path != root
                            )
                        def remember_lease(lease) -> None:
                            nonlocal env
                            env = _TeamCandidateEnvironment(
                                lease, candidate_source.workspace, source_owner=source_owner,
                            )
                            if source is not None:
                                self._source_dependencies[env] = source

                        await EnvCandidateWorkspace(
                            candidate_source, owned_save_dir=self._owned_save_dir,
                        )._acquire(role, strict_capture=True, lease_observer=remember_lease,
                                   strict_untracked_exclusions=excluded)
                if env is None:
                    if source_owner is not None:
                        await source_owner.cleanup()
                        source_owner = None
                    env = self._build_worktree(branch, base)
                    if source is not None:
                        self._source_dependencies[env] = source
                    await env.setup()
        except BaseException as original:
            try:
                if env is not None:
                    await _finish_cleanup(env.cleanup())
                    self._source_dependencies.pop(env, None)
                elif source_owner is not None:
                    await _finish_cleanup(source_owner.cleanup())
            except BaseException as cleanup_exc:
                async with self._lifecycle:
                    self._envs.append(env if env is not None else source_owner)
                logger.warning("partial worktree cleanup failed", exc_info=True)
                add_exception_note(
                    original,
                    "partial worktree retained for cleanup retry: "
                    f"{type(cleanup_exc).__name__}: {cleanup_exc}",
                )
            await _finish_cleanup(self._end_acquire())
            raise original
        return await self._finish_acquire(env, generation)

    def _delivery_environment(self) -> Environment:
        base = self._base_environment
        if base is not None and not getattr(base, "local_filesystem", False):
            return base
        if self._owned_delivery_environment is None:
            # Preserve the pool's existing local_filesystem delegation for
            # third-party local ports as well as native LocalEnvironment.
            self._owned_delivery_environment = LocalEnvironment(
                base.workspace if base is not None else self._workspace,
            )
        return self._owned_delivery_environment

    async def adopt_captured(self, patch: str, physical_paths: Sequence[str]) -> None:
        token = self._physical_delivery_paths.set(tuple(physical_paths))
        try:
            await self.adopt(patch)
        finally:
            self._physical_delivery_paths.reset(token)

    async def adopt(self, patch: str) -> None:
        if not self._enable_adoption:
            raise PermissionError("this team has not declared adopt_agent")
        await self._begin_acquire()
        try:
            async with self._delivery_lock:
                source = self._delivery_environment()
                probe = await source.exec_cmd(
                    f"git -C {shlex.quote(source.workspace)} rev-parse --show-toplevel",
                )
                repository = probe.returncode == 0
                root = (
                    _complete(probe, "team delivery repository").removesuffix("\n")
                    if repository else source.workspace
                )
                physical_paths = self._physical_delivery_paths.get()
                await _apply_increment(
                    source, root, patch, repository=repository, strict=True,
                    physical_paths=tuple(dict.fromkeys((
                        *physical_paths, *await _physical_paths(source, root, patch, strict=True),
                    ))) if physical_paths is not None else (),
                )
        finally:
            await _finish_cleanup(self._end_acquire())

    async def _begin_acquire(self) -> int:
        async with self._lifecycle:
            while self._release_active:
                await self._lifecycle.wait()
            self._inflight_acquires += 1
            return self._generation

    async def _end_acquire(self) -> None:
        async with self._lifecycle:
            self._inflight_acquires -= 1
            self._lifecycle.notify_all()

    async def _finish_acquire(self, env: Environment, generation: int) -> Environment:
        async with self._lifecycle:
            invalidated = generation != self._generation
            if not invalidated:
                self._envs.append(env)
                self._inflight_acquires -= 1
                self._lifecycle.notify_all()
        if invalidated:
            try:
                await _finish_cleanup(env.cleanup())
                self._source_dependencies.pop(env, None)
            except BaseException as cleanup_exc:
                async with self._lifecycle:
                    self._envs.append(env)
                raise RuntimeError(
                    "pool release invalidated an in-flight acquire; cleanup retained for retry"
                ) from cleanup_exc
            finally:
                await _finish_cleanup(self._end_acquire())
            raise RuntimeError("pool release invalidated an in-flight acquire")
        return env

    def _build_worktree(self, branch: str, base: Environment | None) -> Environment:
        """An isolated view of wherever the run's repository actually is."""
        output_options = (
            {"owned_output_paths": self._owned_output_paths}
            if self._enable_adoption and self._owned_output_paths else {}
        )
        if isinstance(base, DockerEnvironment) and base.container_reference is not None:
            worktree_root = CONTAINER_WORKTREE_ROOT
            if posixpath.commonpath((base.workspace, worktree_root)) == base.workspace:
                worktree_root = "/var/tmp/opencollab-worktrees"
            return ContainerWorktreeEnvironment(
                container_id=base.container_reference,
                repository_root=base.workspace,
                worktree_root=worktree_root,
                branch_name=branch,
                command_prefix=base.command_prefix,
            )
        if base is None or getattr(base, "local_filesystem", False):
            return WorktreeEnvironment(
                base.workspace if base is not None else self._workspace, branch_name=branch,
                owned_save_dir=self._owned_save_dir,
                **output_options,
            )
        raise TypeError(
            "worktree isolation is not available for this environment: "
            f"{type(base).__name__}"
        )

    async def release(self) -> None:
        """Tear down every environment this pool has handed out.

        One failing teardown must not abort the others, so each is isolated;
        the failure is logged rather than swallowed so it is diagnosable.
        """
        await _finish_cleanup(self._release_owned())

    async def _release_owned(self) -> None:
        async with self._release_lock:
            async with self._lifecycle:
                self._release_active = True
                self._generation += 1
                while self._inflight_acquires:
                    await self._lifecycle.wait()
            try:
                await self._cleanup_owned()
            finally:
                async with self._lifecycle:
                    self._release_active = False
                    self._lifecycle.notify_all()

    async def _cleanup_owned(self) -> None:
        failures: list[str] = []
        for env in reversed(tuple(self._envs)):
            if any(owner is env for owner in self._source_dependencies.values()):
                failures.append(f"{env.workspace}: dependent review workspace cleanup is pending")
                continue
            try:
                await _finish_cleanup(env.cleanup())
            except BaseException as exc:
                failures.append(f"{env.workspace}: {type(exc).__name__}: {exc}")
                logger.warning(
                    "environment cleanup failed for %s",
                    env.workspace,
                    exc_info=True,
                )
            else:
                self._envs.remove(env)
                self._source_dependencies.pop(env, None)
        if failures:
            raise OSError(
                "environment pool cleanup failed; retry state retained: "
                + "; ".join(failures)
            )
        if self._owned_delivery_environment is not None:
            await self._owned_delivery_environment.cleanup()
            self._owned_delivery_environment = None

    async def release_env(self, env: Environment) -> None:
        """Release one failed spawn's environment without touching siblings."""
        async with self._release_lock:
            if env not in self._envs:
                return
            if any(owner is env for owner in self._source_dependencies.values()):
                raise RuntimeError("dependent review workspace must be released before its source")
            try:
                await _finish_cleanup(env.cleanup())
            except BaseException:
                logger.warning(
                    "environment cleanup failed for %s", env.workspace, exc_info=True
                )
                raise
            self._envs.remove(env)
            self._source_dependencies.pop(env, None)

    async def cleanup(self) -> None:
        """Compatibility alias for older callers."""
        await self.release()
