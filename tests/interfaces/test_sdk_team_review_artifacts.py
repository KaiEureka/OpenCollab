"""Review verdicts retain their answer boundary when a reviewer writes files."""

from __future__ import annotations

import json
import subprocess

import pytest

from opencollab import OpenCollab
from opencollab.adapters.llm.types import LLMResponse, Usage
from opencollab.application.scheduler import Scheduler
from opencollab.bootstrap import container


@pytest.mark.parametrize("serialized", [False, True])
@pytest.mark.parametrize("write_report", [False, True])
@pytest.mark.parametrize("answer,passed", [
    ("The implementation matches the task.\nVERDICT: PASS", True),
    ("VERDICT: PASS\nVERDICT: FAIL", False),
    ("VERDICT: PASS\nThis is a quoted example.", False),
])
async def test_review_uses_original_answer_and_preserves_worktree_artifacts(
    tmp_path, monkeypatch, serialized, write_report, answer, passed,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "answer.py").write_text("answer = 0\n")
    for args in (
        ("init", "-q"), ("add", "answer.py"),
        ("-c", "user.name=OpenCollab Tests", "-c", "user.email=tests@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm", "\u521d\u59cb\u5316\u6d4b\u8bd5\u4ed3\u5e93"),
    ):
        subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True)
    config = tmp_path / "team.yaml"
    config.write_text(
        """entry: lead
roles:
  lead:
    prompt: ROLE_LEAD
    tools: [spawn_with_review]
  coder:
    prompt: ROLE_CODER
    tools: [file_write]
  reviewer:
    prompt: ROLE_REVIEWER
    tools: [file_read, file_write]
topology:
  lead: [coder, reviewer]
""", encoding="utf-8",
    )
    calls = []
    schedulers = []
    original_init = Scheduler.__init__

    def capture_scheduler(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        schedulers.append(self)

    class OfflineModel:
        def __init__(self, **kwargs):
            self.step = 0

        def context_window(self):
            return 200_000

        async def close(self):
            pass

        async def complete(self, messages, **kwargs):
            self.step += 1
            role = next(
                role for role in ("lead", "coder", "reviewer")
                if f"ROLE_{role.upper()}" in str(messages[0]["content"])
            )
            calls.append((role, self.step))
            name = None
            if self.step == 1:
                if role == "lead":
                    name, arguments = "spawn_with_review", {
                        "task": "Set answer.py to answer = 42.", "max_iterations": 3 if passed else 1,
                    }
                elif role == "coder":
                    name, arguments = "file_write", {
                        "path": "answer.py", "mode": "create", "content": "answer = 42\n",
                    }
                else:
                    name, arguments = "file_read", {"path": "answer.py"}
            elif role == "reviewer" and self.step == 2 and write_report:
                name, arguments = "file_write", {
                    "path": "review.txt", "mode": "create", "content": "Reviewed the implementation.\n",
                }
            if name is not None:
                return LLMResponse(tool_calls=[{
                    "id": f"call-{self.step}", "type": "function", "function": {
                        "name": name, "arguments": json.dumps(arguments),
                    },
                }], usage=Usage(10, 5), finish_reason="tool_calls")
            if role == "lead":
                content = "\n".join(str(m["content"]) for m in messages if m["role"] == "tool")
            elif role == "coder":
                content = "Updated answer.py."
            else:
                assert any(
                    "+answer = 42" in str(m.get("content", ""))
                    for m in messages if m["role"] == "user"
                )
                assert any(
                    "answer = 0" in str(m.get("content", ""))
                    for m in messages if m["role"] == "tool"
                )
                content = answer
            return LLMResponse(content=content, usage=Usage(10, 5), finish_reason="stop")

    monkeypatch.setattr(container, "LLMClient", OfflineModel)
    monkeypatch.setattr(Scheduler, "__init__", capture_scheduler)
    result = await OpenCollab(workspace, model="offline", config={"max_output_tokens": 128}).team(
        "Set answer.py to answer = 42 and review it.", config=config,
        use_worktrees=True, serialize_turns=serialized, budget=100_000,
        max_steps=10, timeout=10, trace=False,
    )

    assert result.status == "completed", result.reason
    assert ("PASSED after 1 iteration" if passed else "FAILED after 1 iteration") in result.output
    assert [role for role, step in calls if step == 1] == ["lead", "coder", "reviewer"]
    coder, reviewer = [scb for scb in schedulers[0].table.entries.values() if scb.parent_aid == 0]
    assert reviewer.answer == answer
    assert "+answer = 42" in coder.result
    if write_report:
        assert "[Changes made in worktree]" in reviewer.result
        assert "+Reviewed the implementation." in reviewer.result
        assert reviewer.result.endswith("```")
    else:
        assert reviewer.result == answer
    if passed:
        assert "+answer = 42" in result.output
    else:
        assert answer in result.output
