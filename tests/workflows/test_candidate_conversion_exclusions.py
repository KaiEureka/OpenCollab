from __future__ import annotations

import json
import subprocess
import sys

import pytest

from opencollab import OpenCollab
from opencollab.adapters._env_local import LocalEnvironment
from opencollab.adapters.candidate_workspace import EnvCandidateWorkspace
from opencollab.adapters.llm.client import LLMClient
from opencollab.adapters.llm.types import LLMResponse, Usage
from tests.workflows.test_candidate_physical_submodules import _index, _source
from tests.workflows.test_workflow_candidate_workspace import _git, _repository


@pytest.mark.parametrize("staged", [False, True])
async def test_source_conversion_probe_uses_the_same_literal_exclusions_as_the_tree(tmp_path, staged):
    source = _repository(tmp_path)
    (source / ".gitattributes").write_text("*.diff text eol=lf\n")
    _git(source, "add", ".gitattributes")
    _git(source, "commit", "-qm", "Prepare diff attributes")
    environment = LocalEnvironment(str(source))
    workspace = EnvCandidateWorkspace(environment)
    excluded = ("evidence[run]",)
    try:
        before = await workspace.physical_source_revision(exclude_paths=excluded)
        assert before is None
        evidence = source / excluded[0]
        evidence.mkdir()
        (evidence / "candidate.diff").write_text("owned evidence\n")
        if staged:
            _git(source, "add", str(evidence))
        index = (source / ".git/index").read_bytes()
        assert await workspace.physical_source_revision(exclude_paths=excluded) == before
        assert await workspace.source_diff(exclude_paths=excluded) == ""
        assert (source / ".git/index").read_bytes() == index

        caller = source / "caller.diff"
        caller.write_bytes(b"caller data\n")
        _git(source, "add", "caller.diff")
        _git(source, "commit", "-qm", "Prepare a real source file")
        original = await workspace.physical_source_revision(exclude_paths=excluded)
        assert original is not None
        caller.write_bytes(b"caller data\r\n")
        _git(source, "add", "caller.diff")
        index = (source / ".git/index").read_bytes()
        assert await workspace.source_diff(exclude_paths=excluded) == ""
        assert await workspace.physical_source_revision(exclude_paths=excluded) != original
        assert caller.read_bytes() == b"caller data\r\n"
        assert (source / ".git/index").read_bytes() == index
    finally:
        await environment.cleanup()


def _record_modules(source, nested):
    if nested:
        middle = source / "vendor/dependency"
        _git(middle, "add", "vendor/leaf")
        _git(middle, "-c", "user.name=Test User", "-c", "user.email=test@example.invalid",
             "-c", "commit.gpgsign=false", "commit", "-qm", "Update nested module")
    _git(source, "add", "vendor/dependency")
    _git(source, "commit", "-qm", "Update source module")


def _diff_module(tmp_path, nested):
    source, module_path = _source(tmp_path, nested=nested, converted=False)
    module = source / module_path
    (module / ".gitattributes").write_text("*.diff text eol=lf\n")
    _git(module, "add", ".gitattributes")
    _git(module, "-c", "user.name=Test User", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm", "Prepare module diff attributes")
    _record_modules(source, nested)
    return source, module


@pytest.mark.parametrize("nested", [False, True])
async def test_module_conversion_probe_uses_module_relative_exclusions(tmp_path, nested):
    source, module = _diff_module(tmp_path, nested)
    module_path = module.relative_to(source).as_posix()
    environment = LocalEnvironment(str(source))
    workspace = EnvCandidateWorkspace(environment)
    excluded = (module_path + "/evidence[run]",)
    try:
        assert await workspace.physical_source_revision(exclude_paths=excluded) is None
        evidence = source / excluded[0]
        evidence.mkdir()
        (evidence / "candidate.diff").write_text("owned module evidence\n")
        index_path = module / _git(module, "rev-parse", "--git-path", "index").removesuffix("\n")
        module_index = index_path.read_bytes()
        source_index = (source / ".git/index").read_bytes()
        assert await workspace.physical_source_revision(exclude_paths=excluded) is None
        (module / "caller.diff").write_text("caller data\n")
        assert await workspace.physical_source_revision(exclude_paths=excluded) is not None
        assert index_path.read_bytes() == module_index
        assert (source / ".git/index").read_bytes() == source_index
    finally:
        await environment.cleanup()


@pytest.mark.parametrize("location", ["root", "module", "nested", "module-root"])
async def test_owned_output_probes_preserve_tracked_files_in_the_same_directory(tmp_path, location):
    if location == "root":
        source = _repository(tmp_path)
        module = source
        (source / ".gitattributes").write_text("*.diff text eol=lf\n")
        _git(source, "add", ".gitattributes")
        _git(source, "commit", "-qm", "Prepare output attributes")
    else:
        source, module = _diff_module(tmp_path, location == "nested")
    outputs = module if location == "module-root" else module / "outputs[run]"
    outputs.mkdir(exist_ok=True)
    environment = LocalEnvironment(str(source))
    workspace = EnvCandidateWorkspace(environment, owned_save_dir=str(outputs))
    try:
        assert await workspace.physical_source_revision() is None
        current = outputs / "current.diff"
        current.write_text("owned output\n")
        source_index, module_index = _index(source), _index(module)
        assert await workspace.physical_source_revision() is None
        assert _index(source) == source_index
        assert _index(module) == module_index

        tracked = outputs / "tracked.diff"
        tracked.write_bytes(b"tracked source\n")
        _git(module, "add", str(tracked))
        _git(module, "-c", "user.name=Test User", "-c", "user.email=test@example.invalid",
             "-c", "commit.gpgsign=false", "commit", "-qm", "Prepare tracked output source")
        if module != source:
            _record_modules(source, location == "nested")
        before = await workspace.physical_source_revision()
        assert before is not None
        tracked.write_bytes(b"tracked source\r\n")
        _git(module, "add", str(tracked))
        source_index, module_index = _index(source), _index(module)
        assert await workspace.physical_source_revision() != before
        assert tracked.read_bytes() == b"tracked source\r\n"
        assert current.read_text() == "owned output\n"
        assert _index(source) == source_index
        assert _index(module) == module_index
    finally:
        await environment.cleanup()


@pytest.mark.parametrize("converted_evidence", [False, True])
async def test_duo_adopts_after_creating_evidence_with_conversion_attributes(tmp_path, monkeypatch, converted_evidence):
    monkeypatch.setenv("OPENCOLLAB_API_USAGE_LOG", "")
    source = _repository(tmp_path)
    (source / ".gitattributes").write_text("*.diff text eol=lf\n" if converted_evidence else "*.patch text eol=lf\n")
    _git(source, "add", ".gitattributes")
    _git(source, "commit", "-qm", "Prepare evidence attributes")
    index = (source / ".git/index").read_bytes()
    calls = 0

    async def complete(llm, messages, **kwargs):
        nonlocal calls
        calls += 1
        if calls in (1, 3):
            name = "file_write"
            arguments = {"path": "app.py", "mode": "create", "content": "print(2)\n" if calls == 1 else "print(3)\n"}
        elif calls in (2, 4):
            return LLMResponse(content="finished", usage=Usage(1, 1), finish_reason="stop")
        else:
            name = "structured_output"
            arguments = {
                "winner": "B", "requirements_complete": True,
                "requirements": [{"requirement": "app.py prints 3", "a_coverage": "not_covered",
                                  "b_coverage": "covered", "a_evidence": ["app.py prints 2"],
                                  "b_evidence": ["app.py prints 3"]}],
                "rationale": "B contains the requested program",
            }
        return LLMResponse(tool_calls=[{"id": f"call-{calls}", "type": "function",
                                       "function": {"name": name, "arguments": json.dumps(arguments)}}],
                           usage=Usage(1, 1), finish_reason="tool_calls")

    monkeypatch.setattr(LLMClient, "complete", complete)
    client = OpenCollab(source, model="test-model", provider="openai", api_key="test-key")  # pragma: allowlist secret
    result = await client.workflow(
        "duo", {"goal": "app.py prints 3", "candidate_evidence_dir": str(source / "evidence")},
        budget=100_000, trace=False,
    )
    assert result.status == "completed", result.reason
    assert result.output["adopted"] == "B"
    executed = subprocess.run([sys.executable, "-B", "app.py"], cwd=source, capture_output=True, check=True)
    assert executed.stdout == b"3\n"
    assert len(list(source.glob("evidence/**/candidate.diff"))) == 2
    assert (source / ".git/index").read_bytes() == index
    assert calls == 5
