"""Run a generated Workflow against an offline HTTP model."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from opencollab import OpenCollab

SOURCE = '''from opencollab.workflows import workflow

@workflow
async def main(ctx, args):
    answer = await ctx.agent(args["question"], tools=[])
    return {"answer": answer, "total": sum(range(args["count"]))}
'''


async def run(workspace: Path) -> dict:
    observed = {}
    parent_step = 0

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            nonlocal parent_step
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            messages = body["messages"]
            is_parent = messages[0].get("content") == "Offline workflow author"
            tool_call = None
            if is_parent:
                parent_step += 1
                if parent_step == 1:
                    name, args = "file_write", {"path": "generated.py", "mode": "create", "content": SOURCE}
                elif parent_step == 2:
                    name, args = "manage_workflow", {"action": "launch", "source": "generated.py",
                                                    "python": sys.executable,
                                                    "args": {"question": "Give a local answer", "count": 6}}
                elif parent_step == 3:
                    launched = json.loads([m for m in messages if m["role"] == "tool"][-1]["content"])
                    name, args = "manage_workflow", {"action": "wait", "id": launched["id"]}
                else:
                    observed.update(json.loads([m for m in messages if m["role"] == "tool"][-1]["content"]))
                if parent_step <= 3:
                    tool_call = {"id": f"call-{parent_step}", "type": "function",
                                 "function": {"name": name, "arguments": json.dumps(args)}}
            message = {"role": "assistant", "content": "Collected child result" if is_parent else "Local answer"}
            if tool_call is not None:
                message.update(content=None, tool_calls=[tool_call])
            payload = json.dumps({
                "id": "offline", "object": "chat.completion", "created": 0, "model": "gpt-4o",
                "choices": [{"index": 0, "message": message,
                             "finish_reason": "tool_calls" if tool_call else "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    workspace.mkdir(parents=True, exist_ok=True)
    team = workspace / "team.yaml"
    team.write_text("entry: author\nroles:\n  author:\n    prompt: Offline workflow author\n"
                    "    tools: [file_write, bash, manage_workflow]\n", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = await OpenCollab(
            workspace, model="gpt-4o", provider="openai",
            api_key="offline-model",  # pragma: allowlist secret
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            config={"thinking": False, "budget": 10_000},
        ).team(
            "Write and execute your own Workflow", config=team, trace=False,
            artifacts=workspace / "parent-run", use_worktrees=False, allow_unisolated_shell=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    result.raise_for_status()
    if observed.get("status") != "completed":
        raise RuntimeError(f"child failed: {observed}")
    return {"parent_output": result.output, "total_tokens": result.tokens, "child": observed,
            "workspace": str(workspace)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=None)
    options = parser.parse_args()
    directory = options.workspace or Path(tempfile.mkdtemp(prefix="opencollab-authored-"))
    print(json.dumps(asyncio.run(run(directory.resolve())), indent=2))
