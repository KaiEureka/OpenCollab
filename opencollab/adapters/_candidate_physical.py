"""Capture physical candidate contents alongside their ordinary Git patch."""

from __future__ import annotations

import posixpath
import shlex
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from opencollab.adapters._git_patch_bytes import _complete, _complete_patch, _patch_temporary
from opencollab.application.async_timeout import await_owned_operation
from opencollab.patches import patch_paths

_RAW_ATTRIBUTES = "-text -eol -filter -ident -working-tree-encoding"


def _git_prefix(workspace: str, strict: bool = False) -> str:
    environment = "GIT_NO_REPLACE_OBJECTS=1 "
    if strict:
        environment += "GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_EXTERNAL_DIFF= "
    command = f"{environment}git -C {shlex.quote(workspace)}"
    if strict:
        command += (
            f" -c safe.directory={shlex.quote(workspace)}"
            " -c core.attributesFile=/dev/null -c diff.ignoreSubmodules=none"
        )
    return command


async def _has_worktree_conversions(environment: Any, workspace: str, *, strict: bool = False) -> bool:
    git = _git_prefix(workspace, strict)
    files = _complete(await environment.exec_cmd(
        f"{git} ls-files --cached --others --exclude-standard -z",
    ), "candidate attribute files")
    if any(posixpath.basename(path) == ".gitattributes" for path in files.split("\0") if path):
        return True
    if files:
        async with _patch_temporary(
            environment, workspace, files, prefix=".candidate-attribute-probe-",
        ) as paths_file:
            result = await environment.exec_cmd(
                f"{git} check-attr --all -z --stdin < {shlex.quote(paths_file)}",
            )
            if result.stdout_truncated or result.stderr_truncated:
                return True
            values = _complete(result, "candidate effective attributes").split("\0")
            for offset in range(0, len(values) - 2, 3):
                _path, attribute, value = values[offset:offset + 3]
                if attribute in {"text", "eol", "filter", "ident", "working-tree-encoding"} and value not in {
                    "unspecified", "unset", "false",
                }:
                    return True
    autocrlf = await environment.exec_cmd(f"{git} config --get core.autocrlf")
    _complete(autocrlf, "candidate line-ending configuration", allowed=(0, 1))
    if autocrlf.returncode == 0 and autocrlf.stdout.strip().lower() not in ("false", "0", "off", ""):
        return True
    attributes = _complete(await environment.exec_cmd(
        f"{git} rev-parse --git-path info/attributes",
    ), "candidate local attributes").removesuffix("\n")
    paths = [attributes if posixpath.isabs(attributes) else posixpath.join(workspace, attributes)]
    variables = ["GIT_ATTR_SYSTEM"]
    if not strict:
        explicit = await environment.exec_cmd(f"{git} config --path --get core.attributesFile")
        _complete(explicit, "candidate global attributes", allowed=(0, 1))
        if explicit.returncode == 0:
            path = explicit.stdout.removesuffix("\n")
            if path:
                paths.append(path if posixpath.isabs(path) else posixpath.join(workspace, path))
        else:
            variables.append("GIT_ATTR_GLOBAL")
    for variable in variables:
        result = await environment.exec_cmd(f"{git} var {variable}")
        if result.returncode != 0 or result.stdout_truncated or result.stderr_truncated:
            return True
        paths.append(result.stdout.removesuffix("\n"))
    result = await environment.exec_cmd(" || ".join(f"test -s {shlex.quote(path)}" for path in paths))
    _complete(result, "candidate attribute storage inspection", allowed=(0, 1))
    return result.returncode == 0


def _attribute_pattern(path: str) -> str:
    literal = "/" + "".join("\\" + char if char in "\\*?[" else char for char in path)
    encoded = literal.encode("utf-8")
    return '"' + "".join(
        chr(value) if 32 <= value < 127 and value not in (34, 92) else f"\\{value:03o}"
        for value in encoded
    ) + '"'


@asynccontextmanager
async def _physical_git(
    environment: Any, workspace: str, paths: Sequence[str] | None = None, *, strict: bool = False,
    metadata: tuple[str, str] | None = None,
) -> AsyncIterator[tuple[str, str]]:
    if metadata is not None:
        yield metadata
        return
    real_git = _git_prefix(workspace, strict)
    locations = []
    for name in ("objects", "config", "config.worktree", "info/attributes"):
        value = _complete(await environment.exec_cmd(
            f"{real_git} rev-parse --git-path {name}",
        ), "physical candidate Git location").removesuffix("\n")
        locations.append(value if posixpath.isabs(value) else posixpath.join(workspace, value))
    objects, config, worktree_config, source_attributes = locations
    object_format = _complete(await environment.exec_cmd(
        f"{real_git} config --local --get extensions.objectFormat",
    ), "physical candidate object format", allowed=(0, 1)).strip() or "sha1"
    format_option = "" if object_format == "sha1" else f" --object-format={shlex.quote(object_format)}"
    override = (
        f"* {_RAW_ATTRIBUTES}\n" if paths is None else
        "".join(f"{_attribute_pattern(path)} {_RAW_ATTRIBUTES}\n" for path in paths)
    )
    async with _patch_temporary(
        environment, workspace, override, prefix=".candidate-physical-",
    ) as attribute_file:
        directory = attribute_file + ".git"
        quoted = shlex.quote(directory)
        try:
            _complete(await environment.exec_cmd(
                f"{_git_prefix(workspace, strict)} init --bare --quiet --template= "
                f"{format_option} {quoted} && mkdir -p -- {quoted}/info && "
                f"{{ if [ -f {shlex.quote(source_attributes)} ]; then "
                f"cat -- {shlex.quote(source_attributes)}; fi; printf '\\n'; "
                f"cat -- {shlex.quote(attribute_file)}; }} > {quoted}/info/attributes",
            ), "physical candidate metadata setup")
            command = (
                f"GIT_OBJECT_DIRECTORY={shlex.quote(objects)} {real_git} "
                f"--git-dir={quoted} --work-tree={shlex.quote(workspace)} "
                f"-c include.path={shlex.quote(config)} "
                f"-c include.path={shlex.quote(worktree_config)} "
                f"-c safe.directory= -c safe.directory={shlex.quote(workspace)} "
                "-c core.bare=false -c core.filemode=true"
            )
            if paths is None:
                command += " -c core.autocrlf=false -c core.attributesFile=/dev/null"
            if strict:
                command += " -c core.attributesFile=/dev/null -c diff.ignoreSubmodules=none"
            yield command, directory
        finally:
            _complete(await await_owned_operation(environment.exec_cmd(
                f"rm -rf -- {quoted}", timeout=120,
            ), propagate_cancellation=True), "physical candidate metadata cleanup")


async def _physical_tree(
    environment: Any, workspace: str, base_revision: str,
    *, exclude_paths: Sequence[str] = (), strict: bool = False,
    metadata: tuple[str, str] | None = None,
) -> str:
    real_git = _git_prefix(workspace, strict)
    staged = _complete(await environment.exec_cmd(
        f"{real_git} ls-files --stage -z",
    ), "physical candidate indexed files")
    ignored = "--exclude-per-directory=.gitignore" if strict else "--exclude-standard"
    untracked = _complete(await environment.exec_cmd(
        f"{real_git} ls-files --others {ignored} -z",
    ), "physical candidate untracked files")
    excluded = tuple(path.rstrip("/") + "/" for path in exclude_paths)
    remaining = "".join(
        path + "\0" for path in untracked.split("\0")
        if path and not any(path == prefix[:-1] or path.startswith(prefix) for prefix in excluded)
    )
    async with _patch_temporary(environment, workspace, staged, prefix=".candidate-physical-index-") as staged_file:
        async with _patch_temporary(
            environment, workspace, remaining, prefix=".candidate-physical-paths-",
        ) as paths_file:
            async with _physical_git(environment, workspace, strict=strict, metadata=metadata) as (git, _directory):
                _complete(await environment.exec_cmd(
                    f"{git} read-tree {shlex.quote(base_revision)} && "
                    f"{git} update-index -z --index-info < {shlex.quote(staged_file)} && "
                    f"{git} add -u && "
                    f"if [ -s {shlex.quote(paths_file)} ]; then "
                    f"{git} --literal-pathspecs add --force --pathspec-from-file={shlex.quote(paths_file)} "
                    "--pathspec-file-nul; fi",
                    timeout=900,
                ), "physical candidate contents capture")
                return _complete(await environment.exec_cmd(
                    f"{git} write-tree",
                ), "physical candidate tree").strip()


async def _tree_patch(
    environment: Any, workspace: str, before: str, after: str,
    *, exclude_paths: Sequence[str] = (), strict: bool = False,
) -> str:
    exclusions = ""
    if exclude_paths:
        exclusions = " -- . " + " ".join(shlex.quote(f":(exclude,literal){path}") for path in exclude_paths)
    options = (
        "--no-pager diff --binary --full-index --no-ext-diff --no-textconv --no-color "
        "--unified=3 --src-prefix=a/ --dst-prefix=b/ "
        f"{shlex.quote(before)} {shlex.quote(after)}{exclusions}"
    )
    result = await environment.exec_cmd(f"{_git_prefix(workspace, strict)} {options}", timeout=900)
    return await _complete_patch(
        result, "captured candidate patch", environment, workspace, before, None,
        captured_blobs=True,
    )


async def _normalize_tree(
    environment: Any, workspace: str, tree: str, *, strict: bool = False,
    metadata: tuple[str, str] | None = None,
) -> str:
    async with _physical_git(environment, workspace, strict=strict, metadata=metadata) as (raw_git, directory):
        snapshot = directory + "/snapshot"
        _complete(await environment.exec_cmd(
            f"mkdir -- {shlex.quote(snapshot)} && {raw_git} read-tree {shlex.quote(tree)} && "
            f"{raw_git} checkout-index --all --prefix={shlex.quote(snapshot + '/')}",
            timeout=900,
        ), "physical candidate snapshot materialization")
        git = (
            f"GIT_INDEX_FILE={shlex.quote(directory + '/normalized-index')} "
            f"{_git_prefix(workspace, strict)} --work-tree={shlex.quote(snapshot)} -c core.filemode=true"
        )
        _complete(await environment.exec_cmd(
            f"{git} read-tree {shlex.quote(tree)} && {git} add -u", timeout=900,
        ), "candidate Git normalization")
        return _complete(await environment.exec_cmd(
            f"{git} write-tree",
        ), "normalized candidate tree").strip()


@dataclass(frozen=True, slots=True)
class _PhysicalCapture:
    tree: str
    normalized_tree: str
    patch: str


async def _capture_physical(
    environment: Any, workspace: str, base_revision: str,
    *, exclude_paths: Sequence[str] = (), strict: bool = False,
    metadata: tuple[str, str] | None = None,
) -> _PhysicalCapture:
    async with _physical_git(environment, workspace, strict=strict, metadata=metadata) as owned:
        tree = await _physical_tree(
            environment, workspace, base_revision, exclude_paths=exclude_paths,
            strict=strict, metadata=owned,
        )
        normalized = await _normalize_tree(environment, workspace, tree, strict=strict, metadata=owned)
        patch = await _tree_patch(environment, workspace, base_revision, normalized, strict=strict)
        return _PhysicalCapture(tree, normalized, patch)


async def _apply_physical(
    environment: Any, workspace: str, patch_file: str, paths: Sequence[str], *, strict: bool = False,
    metadata: tuple[str, str] | None = None,
) -> None:
    async with _physical_git(environment, workspace, paths, strict=strict, metadata=metadata) as (git, _directory):
        _complete(await environment.exec_cmd(
            f"{git} apply --binary --whitespace=nowarn -- {shlex.quote(patch_file)}", timeout=120,
        ), "physical candidate adoption")


async def _physical_paths(
    environment: Any, workspace: str, patch: str, *, strict: bool = False,
) -> tuple[str, ...]:
    paths = patch_paths(patch)
    if not paths:
        return ()
    async with _patch_temporary(
        environment, workspace, "\0".join(paths) + "\0", prefix=".candidate-physical-attributes-",
    ) as paths_file:
        git = _git_prefix(workspace, strict)
        output = _complete(await environment.exec_cmd(
            f"{git} check-attr -z --stdin text eol filter ident working-tree-encoding < {shlex.quote(paths_file)}",
        ), "candidate conversion attributes").split("\0")
        selected = set()
        for offset in range(0, len(output) - 2, 3):
            path, _attribute, value = output[offset:offset + 3]
            if value not in ("unspecified", "unset", "false"):
                selected.add(path)
        autocrlf = await environment.exec_cmd(f"{git} config --get core.autocrlf")
        if autocrlf.returncode == 0 and autocrlf.stdout.strip().lower() not in ("false", "0", "off"):
            selected.update(paths)
        return tuple(path for path in paths if path in selected)
