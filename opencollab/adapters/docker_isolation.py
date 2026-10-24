from __future__ import annotations

import asyncio
import posixpath
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from opencollab.adapters._docker_snapshot import _import_snapshot
from opencollab.adapters._team_candidate_environment import _TeamCandidateEnvironment
from opencollab.adapters.env import DockerEnvironment, DockerWorkspaceEnvironment, Environment
from opencollab.adapters.worktree_pool import WorktreePool
from opencollab.application.async_timeout import await_owned_operation
from opencollab.application.exception_notes import add_exception_note
from opencollab.domain.docker import DockerOptions
from opencollab.domain.identity import validate_role_identity


@dataclass
class _DockerOwner:
    container: DockerEnvironment
    source_pools: list[WorktreePool]
    pool: WorktreePool | None = None
    view: DockerWorkspaceEnvironment | None = None

    async def cleanup(self) -> None:
        if self.pool is not None:
            await self.pool.release()
        for source_pool in tuple(self.source_pools):
            await source_pool.release()
            self.source_pools.remove(source_pool)
        if self.view is not None:
            await self.view.cleanup()
        await self.container.cleanup()


class DockerIsolationPool:
    """Own execution containers until their worktree delivery and cleanup finish."""

    def __init__(
        self,
        workspace: str,
        *,
        base_environment: Environment | None = None,
        scope: str = "group",
        options: DockerOptions | None = None,
        owned_save_dir: str | None = None,
        enable_adoption: bool = False,
        owned_output_paths: Sequence[str] = (),
    ) -> None:
        if scope not in {"group", "session"}:
            raise ValueError("Docker isolation scope must be group or session")
        self._workspace = workspace
        self._base_environment = base_environment
        self._scope = scope
        self._options = options or DockerOptions()
        for mount in self._options.mounts:
            for reserved in ("/workspace", "/tmp", "/var/tmp"):
                if posixpath.commonpath((mount.target, reserved)) in {mount.target, reserved}:
                    raise ValueError("Docker isolation mounts must be outside workspace and temporary directories")
        self._owned_save_dir = owned_save_dir
        self._owned_output_paths = tuple(owned_output_paths)
        self._owners: list[_DockerOwner] = []
        self._leases: dict[Environment, _DockerOwner] = {}
        self._group: _DockerOwner | None = None
        self._lock = asyncio.Lock()
        self._delivery = WorktreePool(
            workspace, use_worktrees=False, base_environment=base_environment,
            enable_adoption=enable_adoption,
        )

    async def _create_owner(self, role: str, source: Environment | None) -> _DockerOwner:
        source_pool = WorktreePool(
            self._workspace, use_worktrees=True, base_environment=self._base_environment,
            enable_adoption=True, owned_save_dir=self._owned_save_dir,
            owned_output_paths=self._owned_output_paths,
        )
        owner = _DockerOwner(DockerEnvironment(options=self._options), [source_pool])
        self._owners.append(owner)
        candidate = await source_pool.acquire(role, source=source)
        workspace = await _import_snapshot(candidate, owner.container)
        container_id = await owner.container.setup()
        owner.view = DockerWorkspaceEnvironment(container_id=container_id, repo_root=workspace)
        await owner.view.setup()
        owner.pool = WorktreePool(
            workspace, use_worktrees=True, base_environment=owner.view,
            enable_adoption=True,
        )
        await source_pool.release()
        owner.source_pools.remove(source_pool)
        return owner

    async def _import_source(self, owner: _DockerOwner, role: str, source: Environment) -> Environment:
        source_pool = WorktreePool(
            source.workspace, use_worktrees=True, base_environment=source, enable_adoption=True,
        )
        owner.source_pools.append(source_pool)
        candidate = await source_pool.acquire(role)
        container_id = await owner.container.setup()
        destination = DockerEnvironment(
            container_id=container_id, workspace=f"/tmp/opencollab-import-{uuid.uuid4().hex}", exec_workdir="/",
        )
        workspace = await _import_snapshot(candidate, destination)
        await source_pool.release()
        owner.source_pools.remove(source_pool)
        return DockerWorkspaceEnvironment(container_id=container_id, repo_root=workspace)

    async def acquire(self, role: str, *, source: Environment | None = None) -> Environment:
        role = validate_role_identity(role)
        async with self._lock:
            owner = self._group if self._scope == "group" else None
            created = owner is None
            previous_owners = len(self._owners)
            try:
                if owner is None:
                    owner = await self._create_owner(role, source)
                assert owner.pool is not None
                review_source = None
                if source is not None and not created:
                    review_source = source if self._leases.get(source) is owner else await self._import_source(
                        owner, role, source,
                    )
                environment = await owner.pool.acquire(role, source=review_source)
                delegate = environment._delegate if isinstance(environment, _TeamCandidateEnvironment) else environment
                delegate._execution_owner = owner.container
            except BaseException as failure:
                if created:
                    pending = owner if owner is not None else (
                        self._owners[-1] if len(self._owners) > previous_owners else None
                    )
                    if pending is not None:
                        try:
                            await await_owned_operation(pending.cleanup(), propagate_cancellation=False)
                        except BaseException as cleanup_error:
                            add_exception_note(failure, f"Docker isolation cleanup retained for retry: {cleanup_error}")
                        else:
                            self._owners.remove(pending)
                raise
            self._leases[environment] = owner
            if self._scope == "group":
                self._group = owner
            return environment

    async def adopt_captured(self, patch: str, physical_paths: Sequence[str]) -> None:
        await self._delivery.adopt_captured(patch, physical_paths)

    async def adopt(self, patch: str) -> None:
        await self._delivery.adopt(patch)

    async def release_env(self, env: Environment) -> None:
        async with self._lock:
            owner = self._leases.get(env)
            if owner is None:
                return
            assert owner.pool is not None
            await await_owned_operation(owner.pool.release_env(env), propagate_cancellation=True)
            if owner is not self._group and not any(
                value is owner and environment is not env for environment, value in self._leases.items()
            ):
                await await_owned_operation(owner.cleanup(), propagate_cancellation=True)
                self._owners.remove(owner)
            del self._leases[env]

    async def release(self) -> None:
        await await_owned_operation(self._release_owned(), propagate_cancellation=True)

    async def _release_owned(self) -> None:
        async with self._lock:
            errors: list[BaseException] = []
            for owner in reversed(tuple(self._owners)):
                try:
                    await owner.cleanup()
                except BaseException as exc:
                    errors.append(exc)
                else:
                    self._owners.remove(owner)
                    self._leases = {env: value for env, value in self._leases.items() if value is not owner}
                    if self._group is owner:
                        self._group = None
            if errors:
                raise OSError("Docker isolation cleanup failed; resources retained for retry") from errors[0]
            await self._delivery.release()

    async def cleanup(self) -> None:
        await self.release()
