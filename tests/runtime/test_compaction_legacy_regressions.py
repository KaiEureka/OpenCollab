"""Legacy context repairs retain the current pressure-driven history policy."""

from __future__ import annotations

from types import SimpleNamespace

from opencollab.bootstrap.container import _build_default_shaper


def test_small_window_retains_the_latest_location_and_evidence_pair():
    pipeline = _build_default_shaper(SimpleNamespace(context_window=lambda: 35_500), lambda _: "summary")
    clear, snip, compact = pipeline._shapers[1:]
    assert clear.keep_recent == 2
    assert snip.keep_recent_groups == compact.keep_recent_groups == 2

def test_large_window_keeps_default_history_and_does_not_clear_early():
    pipeline = _build_default_shaper(SimpleNamespace(context_window=lambda: 1_048_576), lambda _: "summary")
    clear, snip, compact = pipeline._shapers[1:]
    assert clear.keep_recent == 5
    assert snip.keep_recent_groups == compact.keep_recent_groups == 4
    messages = [{"role": "user", "content": "task"}]
    for index in range(20):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "tool_calls": [{"id": str(index), "function": {"name": "file_read", "arguments": "{}"}}],
                },
                {"role": "tool", "tool_call_id": str(index), "content": "source code" * 30},
            ]
        )
    assert pipeline.shape(messages) == messages
