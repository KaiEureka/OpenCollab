from __future__ import annotations

import json
import subprocess
import sys

import pytest

from opencollab import OpenCollab
from opencollab.adapters.llm.types import LLMResponse, Usage
from opencollab.adapters.tools.adopt import AdoptTool
from tests.support.worktree_delivery_support import git


class AdoptingLLM:
    def __init__(self, sha):
        self.sha = sha
        self.observation = None

    async def complete(self, messages, **kwargs):
        observations = [message["content"] for message in messages if message["role"] == "tool"]
        if observations:
            self.observation = observations[-1]
            return LLMResponse(content=self.observation, usage=Usage(4, 2), finish_reason="stop")
        return LLMResponse(tool_calls=[{
            "id": "adopt", "type": "function", "function": {
                "name": "adopt", "arguments": json.dumps({"sha": self.sha}),
            },
        }], usage=Usage(4, 2), finish_reason="tool_calls")


@pytest.mark.parametrize("object_format,length", [
    ("sha1", None), ("sha256", None), ("sha1", 10), ("sha256", 40), ("sha256", 48),
])
async def test_public_workflow_adopts_full_and_abbreviated_commit_ids(tmp_path, object_format, length):
    git(tmp_path, "init", "-q", f"--object-format={object_format}")
    git(tmp_path, "config", "user.email", "tests@example.com")
    git(tmp_path, "config", "user.name", "OpenCollab Tests")
    (tmp_path / "answer.py").write_text("print(1)\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "Initial answer")
    start = git(tmp_path, "rev-parse", "HEAD").strip()
    (tmp_path / "answer.py").write_text("print(42)\n", encoding="utf-8")
    git(tmp_path, "commit", "-qam", "Complete answer")
    target = git(tmp_path, "rev-parse", "HEAD").strip()
    git(tmp_path, "checkout", "-q", "--detach", start)
    llm = AdoptingLLM(target if length is None else target[:length])

    async def flow(context, _inputs):
        return await context.agent_run(
            "Adopt the teammate's commit.", budget=5_000,
            tools=[AdoptTool(require_process_isolation=False)],
            system_prompt="Call adopt and return its result.",
        )

    result = await OpenCollab(tmp_path, model="offline").workflow(
        flow, llm=llm, budget=10_000, trace=False, limit_mode="explicit",
    )

    assert result.ok, result.error
    assert result.output.status == "completed"
    assert llm.observation.startswith(f"Checked out {target}"), llm.observation
    assert "+print(42)" in llm.observation
    assert git(tmp_path, "rev-parse", "HEAD").strip() == target
    executed = subprocess.run(
        [sys.executable, "answer.py"], cwd=tmp_path, check=True, capture_output=True, text=True,
    )
    assert executed.stdout == "42\n"
