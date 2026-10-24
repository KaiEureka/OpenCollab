from __future__ import annotations

import os
from pathlib import Path

import pytest

from opencollab.adapters import _vm_remote


@pytest.fixture
def temporary_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    identity = {"device": workspace.stat().st_dev, "inode": workspace.stat().st_ino}
    monkeypatch.setattr(_vm_remote, "_identity", lambda path: identity)

    async def perform(operation, action, arguments):
        directory = state / operation
        directory.mkdir(exist_ok=True)
        return await _vm_remote._perform({"workspace": str(workspace), "identity": identity,
                                          "action": action, "args": arguments}, directory)

    return state, perform


async def test_mutable_temporary_pins_directory_and_cleans_replaced_files_without_following_links(
    tmp_path, temporary_workspace,
):
    state, perform = temporary_workspace
    outside = tmp_path / "outside"
    outside.write_text("keep")
    path = Path(await perform("create", "mutable_temporary", {"name": "git.index", "content": "initial"}))
    assert path.parent.stat().st_mode & 0o777 == 0o700
    inode = path.stat().st_ino
    replacement = path.with_name("replacement")
    replacement.write_text("Git rewrites its index")
    os.replace(replacement, path)
    assert path.stat().st_ino != inode
    (path.parent / "external-link").symlink_to(outside)
    await perform("remove", "remove_mutable_temporary", {"operation_id": "create"})
    assert not (state / "create" / "temporary").exists()
    assert outside.read_text() == "keep"


async def test_mutable_temporary_retains_replaced_directory_until_original_is_restored(temporary_workspace):
    state, perform = temporary_workspace
    path = Path(await perform("create", "mutable_temporary", {"name": "git.index", "content": "initial"}))
    saved = path.parent.with_name("saved")
    path.parent.rename(saved)
    path.parent.mkdir()
    (path.parent / "replacement").write_text("retain")
    with pytest.raises(OSError, match="directory was replaced"):
        await perform("remove", "remove_mutable_temporary", {"operation_id": "create"})
    assert (path.parent / "replacement").read_text() == "retain"
    (path.parent / "replacement").unlink()
    path.parent.rmdir()
    saved.rename(path.parent)
    await perform("retry", "remove_mutable_temporary", {"operation_id": "create"})
    assert not (state / "create" / "temporary").exists()
