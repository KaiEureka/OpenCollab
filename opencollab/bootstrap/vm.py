from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import partial

from opencollab.adapters._env_vm import VMEnvironment
from opencollab.adapters.vm_config import VMConnection, VMResource
from opencollab.adapters.vm_isolation import VMIsolationPool
from opencollab.application.ports import WorktreePoolPort


def vm_isolation_provider(
    resources: Sequence[VMResource], *, lease_database: str,
    scope: str = "group", wait_timeout: float | None = 0,
) -> Callable[..., WorktreePoolPort]:
    if scope not in {"group", "session"}:
        raise ValueError("VM isolation scope must be group or session")
    return partial(VMIsolationPool, resources=tuple(resources), lease_database=lease_database,
                   scope=scope, wait_timeout=wait_timeout)


__all__ = ["VMConnection", "VMEnvironment", "VMResource", "vm_isolation_provider"]
