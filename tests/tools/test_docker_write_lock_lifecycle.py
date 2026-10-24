from __future__ import annotations

import asyncio

from opencollab.adapters import _env_docker as docker_module
from opencollab.application.tool_execution import ToolRuntime
from tests.support.docker_native_edits_support import (
    LocalDockerTransport,
    docker_environment,
    edit_call,
)


def test_attached_container_edits_work_across_repeated_event_loops(tmp_path):
    target = tmp_path / "module.py"
    container_id = "a" * 64

    async def edit_round():
        target.write_text("alpha = 1\nbeta = 1\n")
        transport = LocalDockerTransport(tmp_path)
        environment = docker_environment(tmp_path, transport, container_id=container_id)

        async def edit(index):
            tool, params = edit_call("str_replace", str(target), index)
            return await tool.execute_with_runtime(params, ToolRuntime(environment, None, None))

        try:
            outputs = await asyncio.gather(edit(0), edit(1))
            assert all(not output.startswith("Error") for output in outputs)
            assert target.read_text() == "alpha = 2\nbeta = 2\n"
        finally:
            await environment.cleanup()

    for _ in range(2):
        asyncio.run(edit_round())
    assert docker_module._WRITE_LOCKS == {}
    assert docker_module._WRITE_LOCK_OWNERS == {}
