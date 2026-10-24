"""Container file commands stay inside the workspace and ignore setup stdout."""

from __future__ import annotations

import uuid

import pytest

from opencollab.adapters._env_docker import DockerEnvironment
from opencollab.adapters.tools.fs import FileReadTool
from opencollab.application.tool_execution import ToolRuntime
from tests.support.docker_native_edits_support import LocalDockerTransport


def _environment(workspace, transport, *, command_prefix=None):
    env = DockerEnvironment(
        workspace=str(workspace),
        container_id=uuid.uuid4().hex * 2,
        exec_workdir=str(workspace),
        command_prefix=command_prefix,
    )
    transport.attach(env)
    return env


@pytest.mark.asyncio
async def test_prefix_stdout_is_not_treated_as_file_bytes(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    target = workspace / "note.txt"
    target.write_text("hello\n")
    transport = LocalDockerTransport(workspace)
    env = _environment(workspace, transport, command_prefix="echo PROFILE-OUT")

    assert await env.read_file("note.txt") == "hello\n"
    await env.write_file("note.txt", "updated\n")

    assert target.read_text() == "updated\n"
    assert await env.read_file(str(target)) == "updated\n"
    window = await env.read_text_range("note.txt", offset=1, limit=5, max_chars=100)
    assert window.lines == ["updated"]


@pytest.mark.asyncio
async def test_directory_symlink_cannot_read_or_write_outside_the_workspace(tmp_path):
    workspace = tmp_path / "repo"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("TOP-SECRET-BODY")
    (workspace / "alias").symlink_to(outside, target_is_directory=True)
    (workspace / "link").symlink_to(secret)
    transport = LocalDockerTransport(workspace)
    env = _environment(workspace, transport)
    runtime = ToolRuntime(env, None, None)

    read = await FileReadTool().execute_with_runtime({"path": "alias/secret.txt"}, runtime)
    assert "TOP-SECRET-BODY" not in read
    assert "escapes workspace" in read
    link = await FileReadTool().execute_with_runtime({"path": "link"}, runtime)
    assert "TOP-SECRET-BODY" not in link
    assert "escapes workspace" in link

    with pytest.raises(PermissionError, match="escapes workspace"):
        await env.write_file("alias/pwned.txt", "owned\n")
    assert not (outside / "pwned.txt").exists()
    assert secret.read_text() == "TOP-SECRET-BODY"
