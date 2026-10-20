"""Capture physical candidate contents alongside their ordinary Git patch."""

from __future__ import annotations

import posixpath
import shlex
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from opencollab.adapters._env_worktree_submodules import _source_available_submodule_paths
from opencollab.adapters._git_patch_bytes import _complete, _complete_patch, _patch_temporary
from opencollab.adapters.git_patch import _submodule_patch_guard
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


async def _has_worktree_conversions(
    environment: Any, workspace: str, *, strict: bool = False, exclude_paths: Sequence[str] = (),
    exclude_untracked_paths: Sequence[str] = (),
) -> bool:
    git = _git_prefix(workspace, strict)
    ignored = "--exclude-per-directory=.gitignore" if strict else "--exclude-standard"

    def pathspec(paths: Sequence[str]) -> str:
        return " -- . " + " ".join(shlex.quote(f":(exclude,literal){path}") for path in paths) if paths else ""

    async with _patch_temporary(
        environment, workspace, "", prefix=".candidate-attribute-probe-", repository=False,
    ) as paths_file:
        result = await environment.exec_cmd(
            f"{git} ls-files --cached -z{pathspec(exclude_paths)} > {shlex.quote(paths_file)} && "
            f"{git} ls-files --others {ignored} -z{pathspec((*exclude_paths, *exclude_untracked_paths))} "
            f">> {shlex.quote(paths_file)} && "
            f"{git} check-attr --all -z --stdin < {shlex.quote(paths_file)} && printf '\\0\\0' && "
            f"({git} config --null --get-regexp '^(core\\.autocrlf|extensions\\.worktreeconfig)$' "
            '|| [ "$?" -eq 1 ])',
        )
        if result.stdout_truncated or result.stderr_truncated:
            return True
        attributes, configuration = _complete(result, "candidate conversion settings").rsplit("\0\0", 1)
        values = attributes.split("\0")
        for offset in range(0, len(values) - 2, 3):
            _path, attribute, value = values[offset:offset + 3]
            if attribute in {"text", "eol", "filter", "ident", "working-tree-encoding"} and value not in {
                "unspecified", "unset", "false",
            }:
                return True
        for record in configuration.split("\0"):
            if not record:
                continue
            _key, separator, value = record.partition("\n")
            if not separator:
                value = "true"
            if value.lower() not in ("false", "0", "off", "no", ""):
                return True
        symlinks = _complete(await environment.exec_cmd(
            f"{git} config --type=bool --get core.symlinks",
        ), "candidate symlink configuration", allowed=(0, 1)).strip()
        if symlinks != "false":
            return False
        result = await environment.exec_cmd(
            f"{git} -c core.quotePath=true ls-files --stage{pathspec(exclude_paths)} > {shlex.quote(paths_file)} && "
            f"(grep -q '^120000 ' {shlex.quote(paths_file)} && printf true || [ \"$?\" -eq 1 ])",
        )
        return _complete(result, "candidate symlink index") == "true"


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
    objects = _complete(await environment.exec_cmd(
        f"{real_git} rev-parse --git-path objects",
    ), "physical candidate objects").removesuffix("\n")
    if not posixpath.isabs(objects):
        objects = posixpath.join(workspace, objects)
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
            isolated = (
                "GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_PARAMETERS= GIT_CONFIG_COUNT=0 "
                "GIT_ATTR_NOSYSTEM=1 GIT_EXTERNAL_DIFF= GIT_NO_REPLACE_OBJECTS=1 "
                f"GIT_DIR={quoted} GIT_COMMON_DIR={quoted} GIT_INDEX_FILE={quoted}/index "
            )
            _complete(await environment.exec_cmd(
                f"{isolated} GIT_OBJECT_DIRECTORY={quoted}/objects "
                f"git init --bare --quiet --template= {format_option} {quoted} && "
                f"mkdir -p -- {quoted}/info && cat -- {shlex.quote(attribute_file)} > {quoted}/info/attributes",
            ), "physical candidate metadata setup")
            command = (
                f"{isolated} GIT_OBJECT_DIRECTORY={shlex.quote(objects)} "
                f"git -C {shlex.quote(workspace)} --git-dir={quoted} --work-tree={shlex.quote(workspace)} "
                f"-c safe.directory= -c safe.directory={shlex.quote(workspace)} "
                "-c core.bare=false -c core.filemode=true -c core.autocrlf=false -c core.attributesFile=/dev/null"
            )
            yield command, directory
        finally:
            _complete(await await_owned_operation(environment.exec_cmd(
                f"rm -rf -- {quoted}", timeout=120,
            ), propagate_cancellation=True), "physical candidate metadata cleanup")


async def _physical_tree(
    environment: Any, workspace: str, base_revision: str,
    *, exclude_paths: Sequence[str] = (), exclude_tree_paths: Sequence[str] = (), strict: bool = False,
    metadata: tuple[str, str] | None = None,
) -> str:
    real_git = _git_prefix(workspace, strict)
    ignored = "--exclude-per-directory=.gitignore" if strict else "--exclude-standard"
    exclusions = ""
    if exclude_paths:
        exclusions = " -- . " + " ".join(
            shlex.quote(f":(exclude,literal){path}") for path in exclude_paths
        )
    async with _patch_temporary(
        environment, workspace, "", prefix=".candidate-physical-index-", repository=False,
    ) as staged_file:
        async with _patch_temporary(
            environment, workspace, "", prefix=".candidate-physical-paths-", repository=False,
        ) as paths_file:
            _complete(await environment.exec_cmd(
                f"{real_git} ls-files --stage -z > {shlex.quote(staged_file)} && "
                f"{real_git} ls-files --others {ignored} -z{exclusions} > {shlex.quote(paths_file)}",
                timeout=900,
            ), "physical candidate file enumeration")
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
                if exclude_tree_paths:
                    _complete(await environment.exec_cmd(
                        f"{git} --literal-pathspecs rm --quiet -r --cached --ignore-unmatch -- "
                        f"{shlex.join(exclude_tree_paths)}", timeout=900,
                    ), "physical source path exclusion")
                return _complete(await environment.exec_cmd(
                    f"{git} write-tree",
                ), "physical candidate tree").strip()


async def _physical_submodule_trees(
    environment: Any, workspace: str, *, strict: bool = False, source_prefix: str = "",
    include_paths: Sequence[str] = (), exclude_paths: Sequence[str] = (),
    exclude_tree_paths: Sequence[str] = (),
) -> tuple[tuple[str, str], ...]:
    async def git_in(directory: str, *arguments: str) -> Any:
        return await environment.exec_cmd(
            f"{_git_prefix(directory, strict)} {shlex.join(arguments)}", timeout=900,
        )

    async def git_marker_exists(directory: str) -> bool:
        marker = shlex.quote(posixpath.join(directory, ".git"))
        result = await environment.exec_cmd(f"test -e {marker} || test -L {marker}", timeout=900)
        _complete(result, "physical source submodule metadata inspection", allowed=(0, 1))
        return result.returncode == 0

    modules = await _source_available_submodule_paths(
        workspace, git_in=git_in, source_prefix=source_prefix,
        local_filesystem=bool(getattr(environment, "local_filesystem", False)),
        git_marker_exists=git_marker_exists,
    )
    trees = []
    for path in modules:
        if any(path == excluded or path.startswith(excluded + "/") for excluded in exclude_tree_paths):
            continue
        module = posixpath.join(workspace, path)
        module_exclusions = tuple(excluded[len(path) + 1:] for excluded in exclude_tree_paths
                                  if excluded.startswith(path + "/"))
        module_untracked_exclusions = tuple(
            "." if path == excluded or path.startswith(excluded + "/") else excluded[len(path) + 1:]
            for excluded in exclude_paths
            if path == excluded or path.startswith(excluded + "/") or excluded.startswith(path + "/")
        )
        if path not in include_paths and not await _has_worktree_conversions(
            environment, module, strict=strict, exclude_paths=module_exclusions,
            exclude_untracked_paths=module_untracked_exclusions,
        ):
            continue
        revision = _complete(await git_in(module, "rev-parse", "--verify", "HEAD"),
                             "physical source submodule revision").strip()
        tree = await _physical_tree(
            environment, module, revision, strict=strict,
            exclude_paths=module_untracked_exclusions,
            exclude_tree_paths=module_exclusions,
        )
        trees.append((path, tree))
    return tuple(sorted(trees))


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
        _complete(await environment.exec_cmd(
            _submodule_patch_guard(
                _git_prefix(workspace, strict), base_revision,
                indexed_git_command=owned[0], reject_ignored_untracked=False,
            ), timeout=900,
        ), "physical candidate submodule inspection")
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
    git = _git_prefix(workspace, strict)
    attributes = "text eol filter ident working-tree-encoding"

    async def inspect(command: str) -> tuple[str, ...]:
        output = _complete(await environment.exec_cmd(
            f"{command} && printf '\\0\\0' && ({git} config --get core.autocrlf || [ \"$?\" -eq 1 ])",
        ), "candidate conversion attributes")
        values, autocrlf = output.rsplit("\0\0", 1)
        selected = set()
        records = values.split("\0")
        for offset in range(0, len(records) - 2, 3):
            path, _attribute, value = records[offset:offset + 3]
            if value not in ("unspecified", "unset", "false"):
                selected.add(path)
        if autocrlf.strip().lower() not in ("false", "0", "off", "no", ""):
            selected.update(paths)
        return tuple(path for path in paths if path in selected)

    command = f"{git} check-attr -z {attributes} -- {shlex.join(paths)}"
    if len(command.encode("utf-8", errors="surrogateescape")) <= 32_768:
        return await inspect(command)
    async with _patch_temporary(
        environment, workspace, "\0".join(paths) + "\0",
        prefix=".candidate-physical-attributes-", repository=False,
    ) as paths_file:
        return await inspect(f"{git} check-attr -z --stdin {attributes} < {shlex.quote(paths_file)}")
