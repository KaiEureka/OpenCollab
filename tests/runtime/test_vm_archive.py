from __future__ import annotations

import io
import os
import stat
import sys
import tarfile
from pathlib import Path

import pytest

from opencollab.adapters._vm_archive import pack, publish, unpack


def test_archive_preserves_binary_names_symlinks_modes_and_empty_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir(mode=0o775)
    name = "a space\nand\tunicode-中文-" + (os.fsdecode(b"\xff") if sys.platform == "linux" else "legal")
    data = source / name
    data.write_bytes(b"\0\xff" * 1000000)
    data.chmod(0o775)
    (source / "empty").touch()
    (source / "link").symlink_to(name)
    (source / "subdirectory").mkdir()
    archive = tmp_path / "archive.tar"
    with archive.open("wb") as output:
        pack(source, output, directory=True)
    previous = os.umask(0o077)
    try:
        payload = unpack(archive, tmp_path / "staging", directory=True)
        target = tmp_path / "target"
        target.mkdir()
        original_inode = target.stat().st_ino
        publish(payload, target, directory=True)
    finally:
        os.umask(previous)
    assert (target / name).read_bytes() == data.read_bytes()
    assert stat.S_IMODE((target / name).stat().st_mode) == 0o775
    assert (target / "empty").read_bytes() == b""
    assert os.readlink(target / "link") == name
    assert target.stat().st_ino == original_inode


def _archive(path: Path, members):
    with tarfile.open(path, "w") as archive:
        for name, kind, mode in members:
            member = tarfile.TarInfo(name)
            member.mode = mode
            member.type = kind
            if kind == tarfile.SYMTYPE:
                member.linkname = "/tmp"
            archive.addfile(member, io.BytesIO())


@pytest.mark.parametrize("members", [
    [("../escape", tarfile.REGTYPE, 0o644)],
    [("/tmp/escape", tarfile.REGTYPE, 0o644)],
    [("payload", tarfile.DIRTYPE, 0o755), ("payload/../escape", tarfile.REGTYPE, 0o644)],
    [("payload", tarfile.DIRTYPE, 0o755), ("payload/link", tarfile.SYMTYPE, 0o777),
     ("payload/link/escape", tarfile.REGTYPE, 0o644)],
    [("payload", tarfile.DIRTYPE, 0o755), ("payload/file", tarfile.LNKTYPE, 0o644)],
])
def test_archive_rejects_paths_links_and_special_members(tmp_path, members):
    archive = tmp_path / "archive.tar"
    _archive(archive, members)
    with pytest.raises((ValueError, OSError)):
        unpack(archive, tmp_path / "staging", directory=True)
    assert not (tmp_path / "escape").exists()


def test_archive_strips_setuid_and_setgid_without_losing_group_write(tmp_path):
    archive = tmp_path / "archive.tar"
    _archive(archive, [("payload", tarfile.REGTYPE, 0o6775)])
    previous = os.umask(0o077)
    try:
        payload = unpack(archive, tmp_path / "staging", directory=False)
    finally:
        os.umask(previous)
    assert stat.S_IMODE(payload.stat().st_mode) == 0o775


def test_directory_publication_rejects_existing_content(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (target / "existing").write_text("keep")
    with pytest.raises(FileExistsError, match="empty"):
        publish(source, target, directory=True)
    assert (target / "existing").read_text() == "keep"


def test_publication_accepts_only_its_owned_staging_inside_empty_destination(tmp_path):
    target = tmp_path / "destination"
    payload = target / ".oc-vm-import-owned" / "contents" / "payload"
    payload.mkdir(parents=True)
    (payload / "file").write_text("copied")
    publish(payload, target, directory=True)
    assert (target / "file").read_text() == "copied"
    assert (target / ".oc-vm-import-owned").is_dir()
