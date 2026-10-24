import asyncio
import os

import pytest

from opencollab.adapters.env import LocalEnvironment
from opencollab.bootstrap import build_session, container
from opencollab.domain.agent import Agent
from tests.support.session_characterization_test_support import FakeLLMClient


@pytest.mark.parametrize("factory", [build_session, container.build_session_runtime])
@pytest.mark.parametrize("supplied", [False, True])
@pytest.mark.parametrize("failure_stage", ["missing_key", "trace"])
def test_runtime_construction_releases_only_its_default_workspace(
    tmp_path, monkeypatch, factory, supplied, failure_stage,
):
    monkeypatch.chdir(tmp_path)
    for name in tuple(os.environ):
        if name.startswith("OPENCOLLAB_") or name in {"OPENAI_API_KEY", "DASHSCOPE_API_KEY"}:
            monkeypatch.delenv(name)
    allocated = []

    class ObservedEnvironment(LocalEnvironment):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            allocated.append((self, self._workspace_fd))

    monkeypatch.setattr(container, "LocalEnvironment", ObservedEnvironment)
    environment = LocalEnvironment(str(tmp_path)) if supplied else None
    agent = Agent(
        name="reader", system_prompt="Read.", model="fixture", provider="openai",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    options = {}
    if failure_stage == "trace":
        failure_type, message = OSError, "trace write failed"

        def fail_trace(*args, **kwargs):
            raise OSError(message)

        monkeypatch.setattr(container, "_trace_history_compaction", fail_trace)
        options["llm"] = FakeLLMClient()
    else:
        failure_type, message = ValueError, "no API key configured"
    try:
        with pytest.raises(failure_type, match=message):
            factory(agent=agent, env=environment, **options)
        if supplied:
            assert allocated == []
            asyncio.run(environment.write_file("caller.txt", "usable"))
            assert asyncio.run(environment.read_file("caller.txt")) == "usable"
        else:
            assert len(allocated) == 1
            owned, descriptor = allocated[0]
            assert owned._workspace_fd is None
            with pytest.raises(OSError):
                os.fstat(descriptor)
    finally:
        for owned, _ in allocated:
            asyncio.run(owned.cleanup())
        if environment is not None:
            asyncio.run(environment.cleanup())
