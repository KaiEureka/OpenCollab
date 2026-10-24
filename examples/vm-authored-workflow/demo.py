"""Have a Team member author and run a Workflow across two existing Linux VMs."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from opencollab import OpenCollab

SOURCE = '''from opencollab.tools import builtin_tools

async def main(ctx, args):
    tools = builtin_tools("bash")
    first = await ctx.agent("Inspect the member VM", tools=tools)
    second = await ctx.agent("Inspect a separate VM", tools=tools, isolation="vm-session")
    return {"workers": [first, second], "spent_tokens": ctx.tokens_spent()}
'''


async def run(workspace: Path, profile: dict) -> dict:
    observed = {}

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            messages = body["messages"]
            replies = [item for item in messages if item["role"] == "tool"]
            author = messages[0].get("content", "").startswith("Offline VM workflow author")
            call = None
            if author:
                if not replies:
                    name, args = "file_write", {"path": "generated.py", "mode": "create", "content": SOURCE}
                elif len(replies) == 1:
                    name, args = "manage_workflow", {
                        "action": "launch", "source": "generated.py", "isolation": "worktree",
                    }
                elif len(replies) == 2:
                    name, args = "manage_workflow", {"action": "wait", "id": json.loads(replies[-1]["content"])["id"]}
                else:
                    observed.update(json.loads(replies[-1]["content"]))
                if len(replies) < 3:
                    call = {"id": f"author-{len(replies)}", "type": "function", "function": {
                        "name": name, "arguments": json.dumps(args),
                    }}
            elif not replies:
                call = {"id": "probe", "type": "function", "function": {"name": "bash", "arguments": json.dumps({
                    "command": "cat seed.txt && hostname && cat /proc/sys/kernel/random/boot_id && pwd && "
                               "printf worker > worker.txt",
                })}}
            content = "Collected child result" if author else replies[-1]["content"] if replies else None
            message = {"role": "assistant", "content": content}
            if call is not None:
                message.update(content=None, tool_calls=[call])
            payload = json.dumps({
                "id": "offline", "object": "chat.completion", "created": 0, "model": "gpt-4o",
                "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if call else "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "seed.txt").write_text("Source seed\n", encoding="utf-8")
    for args in (("init", "-q"), ("add", "seed.txt"), (
        "-c", "user.name=Example", "-c", "user.email=example@example.invalid", "commit", "-qm", "initial",
    )):
        subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True)
    team = workspace / "team.yaml"
    team.write_text("entry: author\nisolation: vm-group\nroles:\n  author:\n"
                    "    prompt: Offline VM workflow author\n"
                    "    tools: [file_write, bash, manage_workflow]\n", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = await OpenCollab(
            workspace, model="gpt-4o", provider="openai", api_key="offline-model",  # pragma: allowlist secret
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            config={"vm": profile, "budget": 10_000, "llm_max_retries": 0, "thinking": False},
        ).team("Write and execute your own Workflow", config=team, artifacts=workspace / "parent-run", trace=False)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    result.raise_for_status()
    if observed.get("status") != "completed":
        raise RuntimeError(f"child failed {observed}")
    return {"parent_output": result.output, "total_tokens": result.tokens,
            "child": observed, "workspace": str(workspace)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True,
                        help="JSON VM profile containing resources and lease_database")
    parser.add_argument("--workspace", type=Path)
    options = parser.parse_args()
    directory = options.workspace or Path(tempfile.mkdtemp(prefix="opencollab-vm-authored-"))
    print(json.dumps(asyncio.run(run(directory.resolve(), json.loads(options.config.read_text()))), indent=2))
