from __future__ import annotations

import math
import posixpath
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class VMConnection:
    host: str
    user: str
    port: int = 22
    identity_file: str | None = None
    known_hosts_file: str | None = None
    python: str = "python3"
    connect_timeout: float = 10.0

    def __post_init__(self) -> None:
        for name, value in (("host", self.host), ("user", self.user)):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@%-]*", value):
                raise ValueError(f"VM {name} must be an SSH hostname or account name")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("VM SSH port must be between 1 and 65535")
        for name, value in (("identity_file", self.identity_file), ("known_hosts_file", self.known_hosts_file)):
            if value is not None and (not isinstance(value, str) or not value or any(c in value for c in "\0\n\r")):
                raise ValueError(f"VM {name} must be a path without control characters")
        if not isinstance(self.python, str) or not self.python or any(c in self.python for c in "\0\n\r"):
            raise ValueError("VM python must name the guest Python executable")
        if (
            isinstance(self.connect_timeout, bool) or not isinstance(self.connect_timeout, (int, float))
            or not math.isfinite(self.connect_timeout) or self.connect_timeout <= 0
        ):
            raise ValueError("VM connect_timeout must be finite and positive")


def validate_vm_path(value: str) -> str:
    if (
        not isinstance(value, str) or not value.startswith("/") or value == "/"
        or posixpath.normpath(value) != value or any(c in value for c in "\0\n\r")
    ):
        raise ValueError("VM paths must be normalized absolute non-root paths")
    return value
