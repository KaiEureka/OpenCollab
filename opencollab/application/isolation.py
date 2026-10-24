from __future__ import annotations

from typing import Any


def isolation_name(value: Any, *, default: str | None = None) -> str | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return "worktree" if value else "shared"
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("isolation must be a strategy name, boolean, or None")
    if any(not (character.isalnum() or character in "-_") for character in value):
        raise ValueError("isolation strategy names may contain letters, numbers, '-' and '_'")
    return value
