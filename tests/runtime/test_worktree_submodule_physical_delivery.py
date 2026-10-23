from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from opencollab.adapters.worktree_pool import WorktreePool
from tests.runtime.test_worktree_submodule_delivery import _remove_retained
from tests.workflows.test_candidate_physical_submodules import _SCRIPT, _index, _source
from tests.workflows.test_workflow_candidate_workspace import _git


def _commit(repository, *args):
    return _git(
        repository, "-c", "user.name=Test User", "-c", "user.email=test@example.invalid",
        "commit", *args,
    )


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize(
    "setting", ["dirty", "all", "diff", "tracked", "assume-unchanged", "skip-worktree", "converted"],
)
async def test_default_pool_rejects_source_submodule_bytes_missing_from_candidate(tmp_path, nested, setting):
    source, module_path = _source(tmp_path, nested=nested, converted=setting == "converted")
    module = source / module_path
    original = _SCRIPT.replace(b"42", b"84")
    if setting == "converted":
        original = _SCRIPT.replace(b"\n", b"\r\n")
    elif setting == "tracked":
        _git(source, "config", "--file", ".gitmodules", "submodule.vendor/dependency.ignore", "dirty")
        _git(source, "add", ".gitmodules")
        _commit(source, "-qm", "Configure source module status")
    elif setting in {"assume-unchanged", "skip-worktree"}:
        _git(module, "update-index", f"--{setting}", "helper.sh")
    else:
        repositories = [(source, "vendor/dependency")]
        if nested:
            repositories.append((source / "vendor/dependency", "vendor/leaf"))
        for repository, name in repositories:
            if setting == "diff":
                _git(repository, "config", "diff.ignoreSubmodules", "all")
            else:
                _git(repository, "config", f"submodule.{name}.ignore", setting)
    (module / "helper.sh").write_bytes(original)
    if setting == "converted":
        _git(module, "add", "helper.sh")
    assert _git(source, "--no-optional-locks", "status", "--porcelain") == ""
    source_index, module_index = _index(source), _index(module)
    worktrees = _git(source, "worktree", "list", "--porcelain")
    pool = WorktreePool(str(source), use_worktrees=True)
    try:
        with pytest.raises(RuntimeError, match="source submodule physical contents cannot be reproduced"):
            await pool.acquire("coder")
        assert pool._envs == []
        assert (module / "helper.sh").read_bytes() == original
        assert _index(source) == source_index
        assert _index(module) == module_index
        assert _git(source, "worktree", "list", "--porcelain") == worktrees
        assert _git(source, "for-each-ref", "--format=%(refname)", "refs/heads/opencollab-") == ""
    finally:
        await pool.release()


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
async def test_default_pool_replays_parent_patch_with_clean_ignored_source_submodules(tmp_path, nested):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    _git(source, "config", "submodule.vendor/dependency.ignore", "dirty")
    if nested:
        _git(source / "vendor/dependency", "config", "submodule.vendor/leaf.ignore", "all")
    source_index, module_index = _index(source), _index(source / module_path)
    pool = WorktreePool(str(source), use_worktrees=True)
    candidate = await pool.acquire("coder")
    folder = Path(candidate.workspace)
    try:
        assert (folder / module_path / "helper.sh").read_bytes() == _SCRIPT
        await candidate.write_file(
            "app.py",
            f'import subprocess\nsubprocess.run(["./{module_path}/helper.sh"], check=True)\n',
        )
        tested = await candidate.exec_cmd(shlex.join((sys.executable, "-B", "app.py")))
        assert (tested.returncode, tested.stdout) == (0, "42\n")
        patch = await candidate.get_diff()
        subprocess.run(
            ["git", "-C", str(source), "apply", "-"], input=patch,
            text=True, capture_output=True, check=True,
        )
        replayed = subprocess.run(
            [sys.executable, "-B", "app.py"], cwd=source,
            text=True, capture_output=True, check=True,
        )
        assert replayed.stdout == tested.stdout
        assert candidate.recovery_location is None
        assert _index(source) == source_index
        assert _index(source / module_path) == module_index
    finally:
        await pool.release()
    assert not folder.exists()


def _owned_source(tmp_path, nested, setting):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    module = source / module_path
    save = module / "runs/current"
    save.mkdir(parents=True)
    (save / "tracked.txt").write_text("tracked\n")
    _git(module, "add", "runs/current/tracked.txt")
    _commit(module, "-qm", "Prepare tracked run input")
    if nested:
        middle = source / "vendor/dependency"
        _git(middle, "add", "vendor/leaf")
        _commit(middle, "-qm", "Record run input dependency")
        _git(middle, "config", "submodule.vendor/leaf.ignore", setting)
    _git(source, "add", "vendor/dependency")
    _commit(source, "-qm", "Record run input module")
    _git(source, "config", "submodule.vendor/dependency.ignore", setting)
    (save / "events.jsonl").write_text("started\n")
    return source, module_path, save


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("setting", ["dirty", "all"])
async def test_default_pool_preserves_owned_output_inside_source_submodule(tmp_path, nested, setting):
    source, module_path, save = _owned_source(tmp_path, nested, setting)
    module = source / module_path
    source_index, module_index = _index(source), _index(module)
    worktrees = _git(source, "worktree", "list", "--porcelain")
    pool = WorktreePool(str(source), use_worktrees=True, owned_save_dir=str(save))
    folder = None
    try:
        candidate = await pool.acquire("coder")
        folder = Path(candidate.workspace)
        assert not (folder / module_path / "runs/current/events.jsonl").exists()
        assert (folder / module_path / "runs/current/tracked.txt").read_text() == "tracked\n"
        await candidate.write_file(
            "app.py", "from pathlib import Path\nimport subprocess\n"
            f"Path({str(save / 'events.jsonl')!r}).write_text('finished\\n')\n"
            f'subprocess.run(["./{module_path}/helper.sh"], check=True)\n',
        )
        tested = await candidate.exec_cmd(shlex.join((sys.executable, "-B", "app.py")))
        assert (tested.returncode, tested.stdout) == (0, "42\n")
        assert (save / "events.jsonl").read_text() == "finished\n"
        patch = await candidate.get_diff()
        assert [line for line in patch.splitlines() if line.startswith("diff --git")] == [
            "diff --git a/app.py b/app.py",
        ]
        subprocess.run(
            ["git", "-C", str(source), "apply", "-"], input=patch,
            text=True, capture_output=True, check=True,
        )
        replayed = subprocess.run(
            [sys.executable, "-B", "app.py"], cwd=source,
            text=True, capture_output=True, check=True,
        )
        assert replayed.stdout == tested.stdout
        assert candidate.recovery_location is None
        assert _index(source) == source_index
        assert _index(module) == module_index
    finally:
        await pool.release()
    assert folder is not None and not folder.exists()
    assert (save / "events.jsonl").read_text() == "finished\n"
    assert (save / "tracked.txt").read_text() == "tracked\n"
    assert _git(source, "worktree", "list", "--porcelain") == worktrees
    assert _git(source, "for-each-ref", "--format=%(refname)", "refs/heads/opencollab-") == ""


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("mutation", ["tracked", "outside-owned"])
async def test_default_pool_keeps_owned_submodule_exclusions_limited_to_untracked_output(
    tmp_path, nested, mutation,
):
    source, module_path, save = _owned_source(tmp_path, nested, "dirty")
    changed = save / "tracked.txt" if mutation == "tracked" else save.parent / "current-other.txt"
    changed.write_text("source change\n")
    source_index, module_index = _index(source), _index(source / module_path)
    worktrees = _git(source, "worktree", "list", "--porcelain")
    pool = WorktreePool(str(source), use_worktrees=True, owned_save_dir=str(save))
    try:
        with pytest.raises(RuntimeError, match="source submodule physical contents cannot be reproduced"):
            await pool.acquire("coder")
        assert pool._envs == []
        assert changed.read_text() == "source change\n"
        assert (save / "events.jsonl").read_text() == "started\n"
        assert _index(source) == source_index
        assert _index(source / module_path) == module_index
        assert _git(source, "worktree", "list", "--porcelain") == worktrees
        assert _git(source, "for-each-ref", "--format=%(refname)", "refs/heads/opencollab-") == ""
    finally:
        await pool.release()


async def test_default_pool_excludes_owned_output_from_new_source_submodule_base(tmp_path):
    source, module_path, save = _owned_source(tmp_path, False, "dirty")
    module = source / module_path
    (module / ".gitattributes").write_text("*.sh text eol=crlf\n")
    _git(module, "add", ".gitattributes")
    _commit(module, "-qm", "Configure converted run dependency")
    _git(source, "add", module_path)
    _commit(source, "-qm", "Record converted run dependency")
    (module / "helper.sh").unlink()
    _git(module, "checkout-index", "--all", "--force")
    _git(module, "add", "helper.sh")
    pool = WorktreePool(str(source), use_worktrees=True, owned_save_dir=str(save))
    candidate = await pool.acquire("coder")
    folder = Path(candidate.workspace)
    try:
        updated = _SCRIPT.replace(b"42", b"84").replace(b"\n", b"\r\n")
        (module / "helper.sh").write_bytes(updated)
        _git(module, "add", "helper.sh")
        _commit(module, "-qm", "Update converted run dependency")
        _git(source, "add", module_path)
        _commit(source, "-qm", "Advance converted run dependency")
        parent = _git(source, "rev-parse", "HEAD").strip()
        _git(folder, "checkout", "-q", parent)
        _git(folder, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive")
        await candidate.write_file(
            "app.py", "from pathlib import Path\nimport subprocess\n"
            f"subprocess.run(['sh'], input=Path({(module_path + '/helper.sh')!r}).read_text(), "
            "text=True, check=True)\n",
        )
        tested = await candidate.exec_cmd(shlex.join((sys.executable, "-B", "app.py")))
        assert (tested.returncode, tested.stdout) == (0, "84\n")
        source_index, module_index = _index(source), _index(module)
        patch = await candidate.get_diff()
        assert candidate.diff_base == parent
        assert candidate.recovery_location is None
        subprocess.run(
            ["git", "-C", str(source), "apply", "-"], input=patch,
            text=True, capture_output=True, check=True,
        )
        replayed = subprocess.run(
            [sys.executable, "-B", "app.py"], cwd=source,
            text=True, capture_output=True, check=True,
        )
        assert replayed.stdout == tested.stdout
        assert (save / "events.jsonl").read_text() == "started\n"
        assert _index(source) == source_index
        assert _index(module) == module_index
    finally:
        if candidate.recovery_location is not None:
            _remove_retained(source, candidate)
        else:
            await pool.release()
    assert not folder.exists()


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("setting", ["assume-unchanged", "skip-worktree"])
async def test_default_worktree_retains_hidden_submodule_output(tmp_path, nested, setting):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    source_index, source_module_index = _index(source), _index(source / module_path)
    pool = WorktreePool(str(source), use_worktrees=True)
    candidate = await pool.acquire("coder")
    folder = Path(candidate.workspace)
    module = folder / module_path
    try:
        await candidate.write_file(
            "app.py",
            f'import subprocess\nsubprocess.run(["./{module_path}/helper.sh"], check=True)\n',
        )
        _git(module, "update-index", f"--{setting}", "helper.sh")
        (module / "helper.sh").write_bytes(_SCRIPT.replace(b"42", b"84"))
        assert _git(module, "--no-optional-locks", "status", "--porcelain") == ""
        command = shlex.join((sys.executable, "-B", "app.py"))
        tested = await candidate.exec_cmd(command)
        assert (tested.returncode, tested.stdout) == (0, "84\n")
        candidate_index, candidate_module_index = _index(folder), _index(module)
        try:
            patch = await candidate.get_diff()
        except RuntimeError as failure:
            assert "submodule physical changes" in str(failure)
            assert str(folder) in str(failure)
        else:
            subprocess.run(
                ["git", "-C", str(source), "apply", "-"], input=patch,
                text=True, capture_output=True, check=True,
            )
            replayed = subprocess.run(
                [sys.executable, "-B", "app.py"], cwd=source,
                text=True, capture_output=True, check=True,
            )
            assert replayed.stdout == tested.stdout
            pytest.fail("hidden submodule changes must retain the tested workspace")
        assert candidate.recovery_location is not None
        assert Path(candidate.recovery_location) == folder
        with pytest.raises(OSError, match="retained"):
            await pool.release()
        assert folder.exists()
        assert (module / "helper.sh").read_bytes() == _SCRIPT.replace(b"42", b"84")
        assert (folder / "app.py").is_file()
        assert _index(folder) == candidate_index
        assert _index(module) == candidate_module_index
        assert _index(source) == source_index
        assert _index(source / module_path) == source_module_index
        assert not (source / "app.py").exists()
    finally:
        if candidate.recovery_location is not None:
            (module / "helper.sh").write_bytes(_SCRIPT)
            await candidate.get_diff()
        await pool.release()
    assert not folder.exists()


@pytest.mark.parametrize(
    ("nested", "setting", "source_failure"),
    [(False, None, None), (True, None, None), (False, "assume-unchanged", None),
     (False, "skip-worktree", None), (False, None, "metadata"), (False, None, "object"),
     (False, "attributes", None)],
    ids=["direct-clean", "nested-clean", "assume-unchanged", "skip-worktree", "source-metadata", "source-object",
         "restored-attributes"],
)
async def test_default_worktree_compares_crlf_submodules_after_a_new_diff_base(
    tmp_path, nested, setting, source_failure,
):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    module = source / module_path

    def commit_module(message):
        _git(module, "add", ".")
        _commit(module, "-qm", message)
        if nested:
            middle = source / "vendor/dependency"
            _git(middle, "add", "vendor/leaf")
            _commit(middle, "-qm", message)
        _git(source, "add", "vendor/dependency")
        _commit(source, "-qm", message)

    (module / ".gitattributes").write_text("*.sh text eol=crlf\n")
    commit_module("Configure converted dependency")
    _git(module, "checkout-index", "--all", "--force")
    old = _git(source, "rev-parse", "HEAD").strip()
    updated = _SCRIPT.replace(b"42", b"84").replace(b"\n", b"\r\n")
    (module / "helper.sh").write_bytes(updated)
    commit_module("Update dependency output")
    new = _git(source, "rev-parse", "HEAD").strip()
    _git(source, "reset", "--hard", old)
    _git(source, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive")
    pool = WorktreePool(str(source), use_worktrees=True)
    candidate = await pool.acquire("coder")
    folder = Path(candidate.workspace)
    target = folder / module_path
    removed = None
    hidden = setting in {"assume-unchanged", "skip-worktree"}
    try:
        assert await candidate.get_diff() == ""
        _git(folder, "checkout", "-q", new)
        _git(folder, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive")
        assert (target / "helper.sh").read_bytes() == updated
        if hidden:
            _git(target, "update-index", f"--{setting}", "helper.sh")
            (target / "helper.sh").write_bytes(updated.replace(b"84", b"168"))
        elif setting == "attributes":
            attributes = Path(_git(target, "rev-parse", "--git-path", "info/attributes").strip())
            if not attributes.is_absolute():
                attributes = target / attributes
            attributes.write_text("*.sh text eol=lf\n")
            _git(target, "add", "helper.sh")
        assert _git(target, "--no-optional-locks", "status", "--porcelain") == ""
        await candidate.write_file(
            "app.py",
            "from pathlib import Path\nimport subprocess\n"
            f"subprocess.run(['sh'], input=Path({(module_path + '/helper.sh')!r}).read_text(), "
            "text=True, check=True)\n",
        )
        tested = await candidate.exec_cmd(shlex.join((sys.executable, "-B", "app.py")))
        assert (tested.returncode, tested.stdout) == (0, "168\n" if hidden else "84\n")
        index, module_index = _index(folder), _index(target)
        if source_failure == "metadata":
            removed = module / ".git"
        elif source_failure == "object":
            tree = _git(target, "rev-parse", "HEAD^{tree}").strip()
            objects = Path(_git(module, "rev-parse", "--git-path", "objects").strip())
            if not objects.is_absolute():
                objects = module / objects
            removed = objects / tree[:2] / tree[2:]
        if removed is not None:
            removed.rename(tmp_path / "source-backup")
        if hidden or source_failure is not None:
            match = "source submodule|submodule expected checkout" if source_failure else "submodule physical changes"
            with pytest.raises(RuntimeError, match=match):
                await candidate.get_diff()
            with pytest.raises(OSError, match="retained"):
                await pool.release()
            assert folder.is_dir()
            assert (target / "helper.sh").read_bytes() == (
                updated if source_failure else updated.replace(b"84", b"168")
            )
        else:
            patch = await candidate.get_diff()
            if setting == "attributes":
                attributes.unlink()
                assert await candidate.get_diff() == patch
            assert candidate.recovery_location is None
            _git(source, "checkout", "-q", new)
            _git(source, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive")
            subprocess.run(
                ["git", "-C", str(source), "apply", "-"], input=patch,
                text=True, capture_output=True, check=True,
            )
            replayed = subprocess.run(
                [sys.executable, "-B", "app.py"], cwd=source,
                text=True, capture_output=True, check=True,
            )
            assert replayed.stdout == tested.stdout
        assert candidate.diff_base == new
        assert _index(folder) == index
        assert _index(target) == module_index
    finally:
        if removed is not None:
            (tmp_path / "source-backup").rename(removed)
        if candidate.recovery_location is not None:
            try:
                _remove_retained(source, candidate)
            finally:
                if candidate._local_env is not None:
                    await candidate._local_env.cleanup()
        else:
            await pool.release()
    assert not folder.exists()


@pytest.mark.parametrize(
    ("nested", "setting", "source_attributes"),
    [(False, "attributes", False), (True, "attributes", False), (False, "autocrlf", False),
     (False, "attributes", True)],
    ids=["direct-attributes", "nested-attributes", "autocrlf", "source-attributes"],
)
async def test_candidate_conversion_metadata_cannot_define_delivered_submodule_bytes(
    tmp_path, nested, setting, source_attributes,
):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    source_index, source_module_index = _index(source), _index(source / module_path)
    if source_attributes:
        attributes = Path(_git(source / module_path, "rev-parse", "--git-path", "info/attributes").strip())
        if not attributes.is_absolute():
            attributes = source / module_path / attributes
        attributes.write_text("*.sh text eol=crlf\n")
    pool = WorktreePool(str(source), use_worktrees=True)
    candidate = await pool.acquire("coder")
    folder = Path(candidate.workspace)
    module = folder / module_path
    try:
        assert await candidate.get_diff() == ""
        if setting == "attributes":
            attributes = Path(_git(module, "rev-parse", "--git-path", "info/attributes").strip())
            if not attributes.is_absolute():
                attributes = module / attributes
            attributes.write_text("*.sh text eol=crlf\n")
        else:
            _git(module, "config", "core.autocrlf", "true")
        (module / "helper.sh").write_bytes(_SCRIPT.replace(b"\n", b"\r\n"))
        _git(module, "add", "helper.sh")
        assert _git(module, "--no-optional-locks", "status", "--porcelain") == ""
        await candidate.write_file(
            "app.py", "from pathlib import Path\n"
            f"print(Path({(module_path + '/helper.sh')!r}).read_bytes().count(b'\\r\\n'))\n",
        )
        tested = await candidate.exec_cmd(shlex.join((sys.executable, "-B", "app.py")))
        assert (tested.returncode, tested.stdout) == (0, "2\n")
        index, module_index = _index(folder), _index(module)
        try:
            patch = await candidate.get_diff()
        except RuntimeError as failure:
            assert "submodule physical changes" in str(failure)
        else:
            subprocess.run(
                ["git", "-C", str(source), "apply", "-"], input=patch,
                text=True, capture_output=True, check=True,
            )
            replayed = subprocess.run(
                [sys.executable, "-B", "app.py"], cwd=source,
                text=True, capture_output=True, check=True,
            )
            assert replayed.stdout == tested.stdout
            pytest.fail("candidate conversion metadata must retain its physical changes")
        assert candidate.recovery_location is not None
        with pytest.raises(OSError, match="retained"):
            await pool.release()
        assert (module / "helper.sh").read_bytes() == _SCRIPT.replace(b"\n", b"\r\n")
        assert _index(folder) == index
        assert _index(module) == module_index
        assert _index(source) == source_index
        assert _index(source / module_path) == source_module_index
    finally:
        if candidate.recovery_location is not None:
            try:
                _remove_retained(source, candidate)
            finally:
                if candidate._local_env is not None:
                    await candidate._local_env.cleanup()
        else:
            await pool.release()
    assert not folder.exists()


async def test_default_worktree_exports_clean_crlf_submodule_checkout(tmp_path):
    source, module_path = _source(tmp_path, converted=False)
    module = source / module_path
    (module / ".gitattributes").write_text("*.sh text eol=crlf\n")
    _git(module, "add", ".gitattributes")
    _commit(module, "-qm", "Configure CRLF checkout")
    _git(source, "add", module_path)
    _commit(source, "-qm", "Record converted module")
    (module / "helper.sh").unlink()
    _git(module, "checkout-index", "--all", "--force")
    assert (module / "helper.sh").read_bytes() == _SCRIPT.replace(b"\n", b"\r\n")
    _git(module, "add", "helper.sh")
    pool = WorktreePool(str(source), use_worktrees=True)
    candidate = await pool.acquire("coder")
    folder = Path(candidate.workspace)
    try:
        assert (folder / module_path / "helper.sh").read_bytes() == _SCRIPT.replace(b"\n", b"\r\n")
        assert await candidate.get_diff() == ""
        await candidate.write_file("app.py", "print(42)\n")
        assert "+print(42)" in await candidate.get_diff()
        assert candidate.recovery_location is None
    finally:
        if candidate.recovery_location is not None:
            _remove_retained(source, candidate)
        else:
            await pool.release()
    assert not folder.exists()


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("setting", ["assume-unchanged", "skip-worktree"])
async def test_default_worktree_exports_parent_changes_with_clean_hidden_submodules(tmp_path, nested, setting):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    source_index, source_module_index = _index(source), _index(source / module_path)
    pool = WorktreePool(str(source), use_worktrees=True)
    candidate = await pool.acquire("coder")
    folder = Path(candidate.workspace)
    try:
        _git(folder / module_path, "update-index", f"--{setting}", "helper.sh")
        assert await candidate.get_diff() == ""
        await candidate.write_file(
            "app.py",
            f'import subprocess\nsubprocess.run(["./{module_path}/helper.sh"], check=True)\n',
        )
        patch = await candidate.get_diff()
        assert candidate.recovery_location is None
        with pytest.raises(PermissionError, match="has not declared adopt_agent"):
            await pool.adopt(patch)
        subprocess.run(
            ["git", "-C", str(source), "apply", "-"], input=patch,
            text=True, capture_output=True, check=True,
        )
        replayed = subprocess.run(
            [sys.executable, "-B", "app.py"], cwd=source,
            text=True, capture_output=True, check=True,
        )
        assert replayed.stdout == "42\n"
        assert _index(source) == source_index
        assert _index(source / module_path) == source_module_index
    finally:
        await pool.release()
    assert not folder.exists()
