"""Shared path-jail helper for filesystem tools.

Every filesystem tool resolves a user-supplied path through the sandbox's
``check_path`` before touching the environment, so no path can escape the
workspace (ref: ``adapters/safety.py``). This module is the single copy of that
guard; ``fs.py`` and ``apply_patch.py`` delegate here instead of repeating the
``policy and policy.check_path(...)`` dance.
"""

from __future__ import annotations

import os
import posixpath

from opencollab.application.tool_execution import ToolRuntime


def checked_path(runtime: ToolRuntime, path: str) -> str:
    """Jail ``path`` to the workspace via the runtime's safety policy.

    Returns the resolved absolute path, or ``path`` unchanged when no policy is
    wired (yolo / tests). Raises ``PermissionError`` if the path escapes the
    workspace — callers that prefer a soft error catch it and return a string.
    """
    policy = runtime.safety_policy
    checked = policy.check_path(path) if policy else path
    environment = runtime.environment
    if policy and environment is not None and not getattr(environment, "local_filesystem", False):
        host_root = os.path.realpath(environment.workspace)
        if os.path.isabs(checked) and os.path.commonpath((host_root, checked)) == host_root:
            checked = posixpath.join(environment.workspace, os.path.relpath(checked, host_root))
    return checked
