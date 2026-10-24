from __future__ import annotations

import math
import posixpath
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DockerMount:
    source: str
    target: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source or any(c in self.source for c in "\0\n\r,"):
            raise ValueError("Docker mount source must be a non-empty path without control characters or commas")
        if (
            not isinstance(self.target, str) or not self.target.startswith("/")
            or self.target == "/" or posixpath.normpath(self.target) != self.target
            or any(c in self.target for c in "\0\n\r,")
        ):
            raise ValueError("Docker mount target must be a normalized absolute non-root path")


@dataclass(frozen=True)
class DockerOptions:
    image: str = "python:3.11"
    network: str = "none"
    cpus: float | None = None
    memory: str | None = None
    pids_limit: int | None = None
    mounts: tuple[DockerMount, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.network, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.network)
            or self.network == "host"
        ):
            raise ValueError("Docker network must be none, bridge, or a named network")
        if self.cpus is not None and (
            isinstance(self.cpus, bool) or not isinstance(self.cpus, (int, float))
            or not math.isfinite(self.cpus) or self.cpus <= 0
        ):
            raise ValueError("Docker cpus must be a positive finite number")
        if self.memory is not None and (
            not isinstance(self.memory, str) or not re.fullmatch(r"[1-9][0-9]*[bkmgBKMG]?", self.memory)
        ):
            raise ValueError("Docker memory must be a positive byte count with an optional b, k, m, or g suffix")
        if self.pids_limit is not None and (
            isinstance(self.pids_limit, bool) or not isinstance(self.pids_limit, int) or self.pids_limit <= 0
        ):
            raise ValueError("Docker pids_limit must be a positive integer")
        if not isinstance(self.mounts, tuple) or any(not isinstance(mount, DockerMount) for mount in self.mounts):
            raise TypeError("Docker mounts must be a tuple of DockerMount values")
        targets = [mount.target for mount in self.mounts]
        if len(set(targets)) != len(targets):
            raise ValueError("Docker mount targets must be distinct")
