from __future__ import annotations

import asyncio
import os
import posixpath
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Any

from opencollab.adapters._candidate_physical import _initialized_submodule_paths
from opencollab.adapters._env_docker import DockerEnvironment
from opencollab.adapters._env_local import LocalEnvironment
from opencollab.adapters._git_patch_bytes import _complete, _patch_temporary
from opencollab.adapters._team_candidate_environment import _TeamCandidateEnvironment
from opencollab.application.async_timeout import await_owned_operation


async def _copy_to_host(environment: Any, source: str, target: Path, *, directory: bool = False) -> None:
    delegate = environment._delegate if isinstance(environment, _TeamCandidateEnvironment) else environment
    if getattr(delegate, "local_filesystem", False):
        if directory:
            operation = asyncio.to_thread(shutil.copytree, source, target, symlinks=True, dirs_exist_ok=True)
        else:
            operation = asyncio.to_thread(shutil.copyfile, source, target)
        await await_owned_operation(operation, propagate_cancellation=True)
        return
    if not isinstance(delegate, DockerEnvironment):
        raise TypeError("Docker isolation requires a local or Docker source environment")
    await delegate.setup()
    source_path = source.rstrip("/") + "/." if directory else source
    result = await delegate._docker("cp", "--", f"{delegate._container_id}:{source_path}", str(target))
    if result.returncode or result.stdout_dropped_bytes or result.stderr_dropped_bytes:
        raise OSError("Docker source workspace copy failed")


def _remove_marker(path: Path) -> None:
    marker = path / ".git"
    if marker.is_dir() and not marker.is_symlink():
        shutil.rmtree(marker)
    elif os.path.lexists(marker):
        marker.unlink()


async def _import_snapshot(source: Any, target: DockerEnvironment) -> str:
    probe = await source.exec_cmd(f"git -C {shlex.quote(source.workspace)} rev-parse --show-toplevel")
    _complete(probe, "Docker source repository probe", allowed=(0, 128))
    repository = probe.returncode == 0
    root = probe.stdout.removesuffix("\n") if repository else source.workspace
    prefix = posixpath.relpath(source.workspace, root) if repository else "."
    modules = await _initialized_submodule_paths(source, root, strict=True) if repository else ()
    directory = Path(tempfile.mkdtemp(prefix="opencollab-docker-import-"))
    try:
        contents = directory / "workspace"
        contents.mkdir()
        await _copy_to_host(source, root, contents, directory=True)
        if repository:
            for relative in (".", *modules):
                source_root = posixpath.normpath(posixpath.join(root, relative))
                local_root = contents.joinpath(*relative.split("/"))
                if not local_root.is_dir() or local_root.is_symlink():
                    raise RuntimeError("Docker source repository must be a physical directory")
                await await_owned_operation(asyncio.to_thread(_remove_marker, local_root), propagate_cancellation=True)
                object_format = _complete(await source.exec_cmd(
                    f"git -C {shlex.quote(source_root)} rev-parse --show-object-format",
                ), "Docker source object format").strip()
                bundle = directory / "repository.bundle"
                async with _patch_temporary(source, root, "", prefix=".docker-import-", suffix=".bundle") as remote:
                    _complete(await source.exec_cmd(
                        f"git -C {shlex.quote(source_root)} bundle create {shlex.quote(remote)} --tags HEAD",
                        timeout=900,
                    ), "Docker source Git bundle")
                    await _copy_to_host(source, remote, bundle)
                local = LocalEnvironment(str(local_root))
                try:
                    git = (
                        f"GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null "
                        f"git -c core.hooksPath=/dev/null -C {shlex.quote(str(local_root))}"
                    )
                    _complete(await local.exec_cmd(
                        f"{git} init --quiet --object-format={shlex.quote(object_format)}", timeout=900,
                    ), "Docker private repository initialization")
                    shallow = _complete(await source.exec_cmd(
                        f"git -C {shlex.quote(source_root)} rev-parse --is-shallow-repository",
                    ), "Docker source shallow history").strip()
                    if shallow == "true":
                        shallow_path = _complete(await source.exec_cmd(
                            f"git -C {shlex.quote(source_root)} rev-parse --path-format=absolute --git-path shallow",
                        ), "Docker source shallow boundary").removesuffix("\n")
                        await _copy_to_host(source, shallow_path, local_root / ".git" / "shallow")
                    command = (
                        f"{git} fetch --quiet --tags {shlex.quote(str(bundle))} HEAD && "
                        f"{git} reset --mixed --quiet FETCH_HEAD"
                    )
                    _complete(await local.exec_cmd(command, timeout=900), "Docker private repository metadata")
                finally:
                    await local.cleanup()
                bundle.unlink()
        else:
            await await_owned_operation(asyncio.to_thread(_remove_marker, contents), propagate_cancellation=True)
            local = LocalEnvironment(str(contents))
            try:
                git = (
                    "GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null "
                    f"git -c core.hooksPath=/dev/null -C {shlex.quote(str(contents))}"
                )
                _complete(await local.exec_cmd(
                    f"{git} init --quiet && {git} add --force --all && "
                    f"{git} -c user.name='OpenCollab Agent' -c user.email=agent@opencollab.invalid "
                    "commit --quiet --allow-empty --no-verify -m 'Initial workspace'",
                    timeout=900,
                ), "Docker directory snapshot")
            finally:
                await local.cleanup()
        container = await target.setup()
        _complete(await target.exec_cmd(f"mkdir -p -- {shlex.quote(target.workspace)}"), "Docker workspace directory")
        copied = await target._docker("cp", "--", str(contents) + "/.", f"{container}:{target.workspace}", timeout=900)
        if copied.returncode or copied.stdout_dropped_bytes or copied.stderr_dropped_bytes:
            raise OSError("Docker isolated workspace import failed")
        identity = _complete(await target.exec_cmd("id -u; id -g"), "Docker workspace user").splitlines()
        if len(identity) != 2 or any(not item.isdecimal() for item in identity):
            raise RuntimeError("Docker workspace user identity is incomplete")
        # Docker's archive transport can retain host IDs. Normalize the private
        # copy before Git checks ownership, including for images with USER set.
        ownership = await target._docker(
            "exec", "--user", "0", "--", container, "chown", "-Rh", ":".join(identity), "--", target.workspace,
            timeout=900,
        )
        if ownership.returncode or ownership.stdout_dropped_bytes or ownership.stderr_dropped_bytes:
            raise OSError("Docker isolated workspace ownership could not be assigned")
        return posixpath.normpath(posixpath.join(target.workspace, prefix))
    finally:
        await await_owned_operation(
            asyncio.to_thread(shutil.rmtree, directory), propagate_cancellation=True,
        )
