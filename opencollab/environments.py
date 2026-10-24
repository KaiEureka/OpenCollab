"""Public execution-environment contract and thin composition helpers."""

from opencollab.application.ports import EnvironmentPort as Environment
from opencollab.application.ports import WorktreePoolPort as EnvironmentPool
from opencollab.bootstrap.docker import DockerMount, DockerOptions, docker_isolation_provider
from opencollab.bootstrap.programmatic import (
    attach_container,
    build_repo_map_via_env,
    docker_environment,
    local_environment,
    worktree_environment,
)
from opencollab.bootstrap.vm import VMConnection, VMEnvironment, VMResource, vm_isolation_provider

__all__ = [
    "Environment",
    "EnvironmentPool",
    "DockerMount",
    "DockerOptions",
    "VMConnection",
    "VMEnvironment",
    "VMResource",
    "attach_container",
    "build_repo_map_via_env",
    "docker_environment",
    "docker_isolation_provider",
    "local_environment",
    "worktree_environment",
    "vm_isolation_provider",
]
