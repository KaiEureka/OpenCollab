from __future__ import annotations

from collections.abc import Callable
from functools import partial

from opencollab.adapters.docker_isolation import DockerIsolationPool
from opencollab.application.ports import WorktreePoolPort
from opencollab.domain.docker import DockerMount, DockerOptions


def docker_isolation_provider(
    options: DockerOptions | None = None, *, scope: str = "group",
) -> Callable[..., WorktreePoolPort]:
    if scope not in {"group", "session"}:
        raise ValueError("Docker isolation scope must be group or session")
    return partial(DockerIsolationPool, options=options, scope=scope)


__all__ = ["DockerMount", "DockerOptions", "docker_isolation_provider"]
