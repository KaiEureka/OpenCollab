from __future__ import annotations

import os
import stat
import tarfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from opencollab.adapters.safe_anchored_files import (
    open_directory_anchor,
    open_parent_beneath,
    write_regular_file_atomic_at,
)
from opencollab.adapters.safe_paths import canonicalize_system_path


def _absolute_parent(path: str | Path, *, create: bool = False) -> tuple[int, str]:
    value = canonicalize_system_path(path)
    root = open_directory_anchor("/")
    try:
        return open_parent_beneath(root, "/", str(value).lstrip("/"), create=create)
    finally:
        os.close(root)


def _add(archive: tarfile.TarFile, parent: int, name: str, relative: str) -> None:
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    member = tarfile.TarInfo(relative)
    member.mode = stat.S_IMODE(info.st_mode) & 0o777
    member.mtime = info.st_mtime
    if stat.S_ISLNK(info.st_mode):
        member.type = tarfile.SYMTYPE
        member.linkname = os.readlink(name, dir_fd=parent)
        archive.addfile(member)
        return
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(name, flags, dir_fd=parent)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise OSError("archive source changed while opening it")
        if stat.S_ISDIR(opened.st_mode):
            member.type = tarfile.DIRTYPE
            archive.addfile(member)
            for child in sorted(os.listdir(fd)):
                _add(archive, fd, child, relative + "/" + child)
        elif stat.S_ISREG(opened.st_mode):
            member.size = opened.st_size
            with os.fdopen(fd, "rb") as handle:
                fd = -1
                archive.addfile(member, handle)
        else:
            raise OSError("VM transfers accept regular files, directories and symlinks")
    finally:
        if fd >= 0:
            os.close(fd)


def pack(source: str | Path, output: BinaryIO, *, directory: bool) -> None:
    parent, name = _absolute_parent(source)
    try:
        mode = os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode
        if stat.S_ISDIR(mode) != directory:
            raise ValueError("VM transfer directory flag must match its source")
        with tarfile.open(fileobj=output, mode="w|", format=tarfile.PAX_FORMAT) as archive:
            _add(archive, parent, name, "payload")
    finally:
        os.close(parent)


def unpack(archive_path: Path, staging: Path, *, directory: bool) -> Path:
    staging.mkdir(mode=0o700)
    anchor = open_directory_anchor(staging)
    seen = set()
    modes = []
    try:
        with tarfile.open(archive_path, mode="r|") as archive:
            for member in archive:
                parts = PurePosixPath(member.name).parts
                if (not parts or parts[0] != "payload" or any(p in {"", ".", ".."} for p in parts)
                        or member.name.startswith("/") or member.name in seen):
                    raise ValueError("VM archive contains an unsafe or repeated path")
                if len(parts) == 1 and member.isdir() != directory:
                    raise ValueError("VM archive root does not match transfer type")
                member.mode &= 0o777
                seen.add(member.name)
                parent, name = open_parent_beneath(anchor, staging, member.name, create=True)
                try:
                    if member.isdir():
                        os.mkdir(name, 0o700, dir_fd=parent)
                        modes.append((member.name, member.mode))
                    elif member.issym():
                        os.symlink(member.linkname, name, dir_fd=parent)
                    elif member.isfile():
                        handle = archive.extractfile(member)
                        if handle is None:
                            raise ValueError("VM archive file contents are missing")
                        with handle:
                            def write(output: BinaryIO) -> None:
                                remaining = member.size
                                while remaining:
                                    chunk = handle.read(min(65536, remaining))
                                    if not chunk:
                                        raise EOFError("VM archive file was truncated")
                                    output.write(chunk)
                                    remaining -= len(chunk)
                            write_regular_file_atomic_at(anchor, staging, member.name, write,
                                                         max_bytes=member.size, mode=member.mode, create_only=True)
                            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                            try:
                                os.fchmod(fd, member.mode)
                            finally:
                                os.close(fd)
                    else:
                        raise ValueError("VM archive contains an unsupported file type")
                finally:
                    os.close(parent)
        if "payload" not in seen:
            raise ValueError("VM archive payload is missing")
        for path, mode in reversed(modes):
            parent, name = open_parent_beneath(anchor, staging, path, create=False)
            try:
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    os.fchmod(fd, mode)
                finally:
                    os.close(fd)
            finally:
                os.close(parent)
    finally:
        os.close(anchor)
    return staging / "payload"


def publish(payload: Path, destination: str | Path, *, directory: bool) -> None:
    source_parent, source_name = _absolute_parent(payload)
    parent, name = _absolute_parent(destination, create=True)
    try:
        if directory:
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
            target = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            source_mode = stat.S_IMODE(os.stat(source_name, dir_fd=source_parent, follow_symlinks=False).st_mode)
            os.chmod(source_name, source_mode | 0o700, dir_fd=source_parent, follow_symlinks=False)
            source = os.open(source_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=source_parent)
            try:
                entries = set(os.listdir(target))
                owned_staging = payload.parent.parent
                staging_parent = os.stat(owned_staging.parent, follow_symlinks=False)
                target_info = os.fstat(target)
                if (staging_parent.st_dev, staging_parent.st_ino) == (target_info.st_dev, target_info.st_ino):
                    staged = os.stat(owned_staging.name, dir_fd=target, follow_symlinks=False)
                    expected = os.stat(owned_staging, follow_symlinks=False)
                    if (staged.st_dev, staged.st_ino) == (expected.st_dev, expected.st_ino):
                        entries.discard(owned_staging.name)
                if entries:
                    raise FileExistsError("VM directory transfer requires an empty destination")
                for child in os.listdir(source):
                    os.rename(child, child, src_dir_fd=source, dst_dir_fd=target)
                os.fchmod(target, source_mode)
                os.fsync(target)
            finally:
                os.close(source)
                os.close(target)
        else:
            try:
                existing = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing is not None and not stat.S_ISREG(existing.st_mode):
                raise OSError("VM transfer destination must be a regular file or absent")
            os.rename(source_name, name, src_dir_fd=source_parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(source_parent)
        os.close(parent)


__all__ = ["pack", "publish", "unpack"]
