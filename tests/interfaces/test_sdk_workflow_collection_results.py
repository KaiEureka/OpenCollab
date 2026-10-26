import asyncio
import json

import pytest

from opencollab import OpenCollab


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["parallel", "pipeline"])
@pytest.mark.parametrize("capacity", [1, 2, 4])
async def test_collections_preserve_handled_business_errors(tmp_path, kind, capacity):
    inputs = [tmp_path / f"input-{index}.txt" for index in range(3)]
    for path, value in zip(inputs, ["42", "invalid", "0"]):
        path.write_text(value)
    caught = []

    async def parse(index):
        text = await asyncio.to_thread(inputs[index].read_text)
        try:
            value = int(text)
        except ValueError as exc:
            caught.append(exc)
            return exc
        await asyncio.to_thread((tmp_path / f"result-{index}.txt").write_text, str(value))
        return value

    async def flow(ctx, _inputs):
        if kind == "parallel":
            values = await ctx.parallel([lambda index=index: parse(index) for index in range(3)])
        else:
            async def stage(_previous, _item, index):
                return await parse(index)

            values = await ctx.pipeline(list(range(3)), stage)
        assert values[0] == 42 and values[1] is caught[0] and values[2] == 0
        summary = [str(value) if isinstance(value, Exception) else value for value in values]
        await asyncio.to_thread((tmp_path / "summary.json").write_text, json.dumps(summary))
        assert await ctx.parallel([lambda: asyncio.sleep(0, result="recovered")]) == ["recovered"]
        return summary

    artifacts = tmp_path / "artifacts"
    result = await OpenCollab(tmp_path, model="test-model").workflow(
        flow, concurrency=capacity, task_concurrency=capacity, timeout=10,
        trace=False, artifacts=artifacts,
    )
    assert result.status == "completed"
    assert result.output == [42, str(caught[0]), 0]
    assert json.loads((tmp_path / "summary.json").read_text()) == result.output
    assert (tmp_path / "result-0.txt").read_text() == "42"
    assert (tmp_path / "result-2.txt").read_text() == "0"
    assert json.loads((artifacts / "workflow.json").read_text())["status"] == "completed"
