from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from opencollab.adapters import worktree_pool
from opencollab.adapters.worktree_pool import WorktreePool
from opencollab.environments import attach_container
from tests.runtime.test_worktree_submodule_delivery import _remove_retained
from tests.support.worktree_delivery_support import CONTAINER_ID
from tests.support.worktree_delivery_support import worktree_transport as worktree_transport
from tests.workflows.test_candidate_physical_submodules import _SCRIPT, _index, _source
from tests.workflows.test_workflow_candidate_workspace import _git


async def _candidate(source, tmp_path, monkeypatch):
    monkeypatch.setattr(worktree_pool, "CONTAINER_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    base = attach_container(container_id=CONTAINER_ID, workspace=str(source))
    pool = WorktreePool(str(source), use_worktrees=True, base_environment=base)
    return base, pool, await pool.acquire("coder")


def _replay(source, patch):
    subprocess.run(
        ["git", "-C", str(source), "apply", "--binary", "-"], input=patch,
        text=True, capture_output=True, check=True,
    )
    return subprocess.run(
        [sys.executable, "-B", "app.py"], cwd=source,
        text=True, capture_output=True, check=True,
    ).stdout


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("setting", [None, "assume-unchanged", "skip-worktree"], ids=["visible", "assume", "skip"])
async def test_container_keeps_unpatchable_submodule_bytes(
    worktree_transport, tmp_path, monkeypatch, nested, setting,
):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    source_indexes = _index(source), _index(source / module_path)
    base, pool, candidate = await _candidate(source, tmp_path, monkeypatch)
    folder = Path(candidate.workspace)
    module = folder / module_path
    try:
        if setting is not None:
            _git(module, "update-index", f"--{setting}", "helper.sh")
        (module / "helper.sh").write_bytes(_SCRIPT.replace(b"42", b"84"))
        if setting is not None:
            assert _git(module, "--no-optional-locks", "status", "--porcelain") == ""
        await candidate.write_file(
            "app.py", f'import subprocess\nsubprocess.run(["./{module_path}/helper.sh"], check=True)\n',
        )
        tested = await candidate.exec_cmd(shlex.join((sys.executable, "-B", "app.py")))
        assert (tested.returncode, tested.stdout) == (0, "84\n")
        _git(module, "status", "--porcelain")
        candidate_indexes = _index(folder), _index(module)
        try:
            patch = await candidate.get_diff()
        except RuntimeError as failure:
            assert "submodule" in str(failure) and "changes" in str(failure)
        else:
            assert _replay(source, patch) == tested.stdout
            pytest.fail("undelivered submodule bytes must retain the executed container workspace")
        assert candidate.recovery_location == f"{CONTAINER_ID}:{folder}"
        with pytest.raises(OSError, match="retained"):
            await pool.release()
        assert folder.is_dir()
        assert (module / "helper.sh").read_bytes() == _SCRIPT.replace(b"42", b"84")
        assert (folder / "app.py").is_file()
        assert not (source / "app.py").exists()
        assert (_index(folder), _index(module)) == candidate_indexes
        assert (_index(source), _index(source / module_path)) == source_indexes
        assert (source / module_path / "helper.sh").read_bytes() == _SCRIPT
    finally:
        if candidate.recovery_location is not None:
            _remove_retained(source, candidate)
        else:
            await pool.release()
        await base.cleanup()
    assert not folder.exists()


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("setting", [None, "assume-unchanged", "skip-worktree"], ids=["plain", "assume", "skip"])
async def test_container_replays_parent_patch_with_unchanged_submodule_bytes(
    worktree_transport, tmp_path, monkeypatch, nested, setting,
):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    source_indexes = _index(source), _index(source / module_path)
    base, pool, candidate = await _candidate(source, tmp_path, monkeypatch)
    folder = Path(candidate.workspace)
    metadata = Path(_git(folder, "rev-parse", "--absolute-git-dir").strip())
    module = folder / module_path
    try:
        if setting is not None:
            _git(module, "update-index", f"--{setting}", "helper.sh")
        assert await candidate.get_diff() == ""
        await candidate.write_file(
            "app.py", f'import subprocess\nsubprocess.run(["./{module_path}/helper.sh"], check=True)\n',
        )
        tested = await candidate.exec_cmd(shlex.join((sys.executable, "-B", "app.py")))
        assert (tested.returncode, tested.stdout) == (0, "42\n")
        candidate_indexes = _index(folder), _index(module)
        patch = await candidate.get_diff()
        assert candidate.recovery_location is None
        assert _replay(source, patch) == tested.stdout
        assert (_index(folder), _index(module)) == candidate_indexes
        assert (_index(source), _index(source / module_path)) == source_indexes
    finally:
        if candidate.recovery_location is not None:
            _remove_retained(source, candidate)
        else:
            await pool.release()
        await base.cleanup()
    assert not folder.exists()
    assert not metadata.exists()
    assert _git(source, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert _git(source, "for-each-ref", "--format=%(refname)", "refs/heads/opencollab-") == ""


def _commit_module(source, module_path, nested, message):
    module = source / module_path
    _git(module, "add", ".")
    _git(module, "commit", "-qm", message)
    if nested:
        middle = source / "vendor/dependency"
        _git(middle, "add", "vendor/leaf")
        _git(middle, "commit", "-qm", message)
    _git(source, "add", "vendor/dependency")
    _git(source, "commit", "-qm", message)


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("source_state", ["old", "current", "current-attributes"])
@pytest.mark.parametrize("hidden", [False, True], ids=["clean", "assume"])
async def test_container_compares_converted_submodules_after_a_new_base(
    worktree_transport, tmp_path, monkeypatch, nested, source_state, hidden,
):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    module = source / module_path
    (module / ".gitattributes").write_text("*.sh text eol=crlf\n")
    _commit_module(source, module_path, nested, "准备子模块换行配置")
    _git(module, "checkout-index", "--all", "--force")
    old = _git(source, "rev-parse", "HEAD").strip()
    updated = _SCRIPT.replace(b"42", b"84").replace(b"\n", b"\r\n")
    (module / "helper.sh").write_bytes(updated)
    _commit_module(source, module_path, nested, "更新子模块程序")
    new = _git(source, "rev-parse", "HEAD").strip()
    _git(source, "reset", "--hard", old)
    _git(source, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive")
    base, pool, candidate = await _candidate(source, tmp_path, monkeypatch)
    folder = Path(candidate.workspace)
    target = folder / module_path
    try:
        assert await candidate.get_diff() == ""
        _git(folder, "checkout", "-q", new)
        _git(folder, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive")
        assert (target / "helper.sh").read_bytes() == updated
        if source_state != "old":
            _git(source, "checkout", "-q", new)
            _git(source, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive")
        if source_state == "current-attributes":
            attributes = Path(_git(module, "rev-parse", "--absolute-git-dir").strip()) / "info/attributes"
            attributes.write_text("*.sh text eol=lf\n")
            _git(module, "add", "helper.sh")
            assert _git(module, "--no-optional-locks", "status", "--porcelain") == ""
            assert (module / "helper.sh").read_bytes() == updated
        if hidden:
            _git(target, "update-index", "--assume-unchanged", "helper.sh")
            (target / "helper.sh").write_bytes(updated.replace(b"84", b"168"))
        assert _git(target, "--no-optional-locks", "status", "--porcelain") == ""
        await candidate.write_file(
            "app.py", "from pathlib import Path\nimport subprocess\n"
            f"subprocess.run(['sh'], input=Path({(module_path + '/helper.sh')!r}).read_text(), "
            "text=True, check=True)\n",
        )
        tested = await candidate.exec_cmd(shlex.join((sys.executable, "-B", "app.py")))
        assert (tested.returncode, tested.stdout) == (0, "168\n" if hidden else "84\n")
        candidate_indexes = _index(folder), _index(target)
        source_indexes = _index(source), _index(module)
        if hidden:
            try:
                patch = await candidate.get_diff()
            except RuntimeError as failure:
                assert "submodule physical changes" in str(failure)
            else:
                _git(source, "checkout", "-q", new)
                _git(source, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive")
                assert _replay(source, patch) == tested.stdout
                pytest.fail("a new diff base must preserve hidden converted submodule changes")
            with pytest.raises(OSError, match="retained"):
                await pool.release()
            assert (target / "helper.sh").read_bytes() == updated.replace(b"84", b"168")
            assert (_index(source), _index(module)) == source_indexes
        else:
            patch = await candidate.get_diff()
            assert candidate.recovery_location is None
            assert (_index(source), _index(module)) == source_indexes
            _git(source, "checkout", "-q", new)
            _git(source, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive")
            assert _replay(source, patch) == tested.stdout
        assert candidate.diff_base == new
        assert candidate.own_commit_count == 0
        assert (_index(folder), _index(target)) == candidate_indexes
    finally:
        if candidate.recovery_location is not None:
            _remove_retained(source, candidate)
        else:
            await pool.release()
        await base.cleanup()
    assert not folder.exists()


@pytest.mark.parametrize(
    ("nested", "setting", "source_attributes"),
    [(False, "attributes", False), (True, "attributes", False),
     (False, "autocrlf", False), (False, "attributes", True)],
    ids=["direct", "nested", "autocrlf", "source-metadata"],
)
async def test_container_compares_actual_source_bytes_instead_of_conversion_metadata(
    worktree_transport, tmp_path, monkeypatch, nested, setting, source_attributes,
):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    module = source / module_path
    if source_attributes:
        attributes = Path(_git(module, "rev-parse", "--absolute-git-dir").strip()) / "info/attributes"
        attributes.write_text("*.sh text eol=crlf\n")
    source_indexes = _index(source), _index(module)
    base, pool, candidate = await _candidate(source, tmp_path, monkeypatch)
    folder = Path(candidate.workspace)
    target = folder / module_path
    try:
        assert await candidate.get_diff() == ""
        if setting == "attributes":
            attributes = Path(_git(target, "rev-parse", "--absolute-git-dir").strip()) / "info/attributes"
            attributes.write_text("*.sh text eol=crlf\n")
        else:
            _git(target, "config", "core.autocrlf", "true")
        (target / "helper.sh").write_bytes(_SCRIPT.replace(b"\n", b"\r\n"))
        _git(target, "add", "helper.sh")
        assert _git(target, "--no-optional-locks", "status", "--porcelain") == ""
        await candidate.write_file(
            "app.py", "from pathlib import Path\n"
            f"print(Path({(module_path + '/helper.sh')!r}).read_bytes().count(b'\\r\\n'))\n",
        )
        tested = await candidate.exec_cmd(shlex.join((sys.executable, "-B", "app.py")))
        assert (tested.returncode, tested.stdout) == (0, "2\n")
        candidate_indexes = _index(folder), _index(target)
        try:
            patch = await candidate.get_diff()
        except RuntimeError as failure:
            assert "submodule physical changes" in str(failure)
        else:
            assert _replay(source, patch) == tested.stdout
            pytest.fail("conversion metadata must retain different physical submodule bytes")
        with pytest.raises(OSError, match="retained"):
            await pool.release()
        assert (target / "helper.sh").read_bytes() == _SCRIPT.replace(b"\n", b"\r\n")
        assert (_index(source), _index(module)) == source_indexes
        assert (_index(folder), _index(target)) == candidate_indexes
    finally:
        if candidate.recovery_location is not None:
            _remove_retained(source, candidate)
        else:
            await pool.release()
        await base.cleanup()
    assert not folder.exists()
