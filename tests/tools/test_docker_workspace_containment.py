"""Container file commands stay inside the workspace and ignore setup stdout."""

from __future__ import annotations

import uuid
from pathlib import Path

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


@pytest.mark.asyncio
async def test_owned_temporary_file_is_created_outside_the_workspace(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "alias").symlink_to(outside, target_is_directory=True)
    transport = LocalDockerTransport(workspace)
    env = _environment(workspace, transport, command_prefix="echo PROFILE-OUT")
    path: str | None = None
    stray = Path("/tmp/oc-not-owned.txt")
    try:
        path = await env.write_temp_file("payload\n", prefix="oc-temp-", suffix=".txt")
        assert path.startswith("/tmp/oc-temp-")
        assert not Path(path).is_relative_to(workspace)
        assert Path(path).read_text(encoding="utf-8") == "payload\n"

        await env.write_file("note.txt", "kept\n")
        assert (workspace / "note.txt").read_text(encoding="utf-8") == "kept\n"
        assert await env.read_file("note.txt") == "kept\n"

        with pytest.raises(PermissionError, match="escapes workspace"):
            await env.write_file("/tmp/oc-not-owned.txt", "nope\n")
        assert not stray.exists()
        with pytest.raises(PermissionError, match="escapes workspace"):
            await env.write_file("alias/pwned.txt", "owned\n")
        assert not (outside / "pwned.txt").exists()
    finally:
        if path is not None and path in env._temporary_files:
            await env.remove_file(path)
        if path is not None:
            Path(path).unlink(missing_ok=True)
        stray.unlink(missing_ok=True)
