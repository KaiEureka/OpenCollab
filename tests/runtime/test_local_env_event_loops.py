from __future__ import annotations

import asyncio
import threading

from opencollab.adapters import _env_local as local_module
from opencollab.environments import local_environment


def test_caller_owned_environment_supports_file_operations_in_repeated_loops(tmp_path):
    environment = local_environment(tmp_path)
    (tmp_path / "data.txt").write_text("retained\nsecond line\n")

    async def file_operations(round_number):
        reads = await asyncio.gather(*(environment.read_file("data.txt") for _ in range(8)))
        assert reads == ["retained\nsecond line\n"] * 8
        windows = await asyncio.gather(*(
            environment.read_text_range("data.txt", offset=2, limit=1, max_chars=100)
            for _ in range(8)
        ))
        assert all(window.lines == ["second line"] for window in windows)
        await asyncio.gather(*(
            environment.write_file(f"result-{index}.txt", f"round {round_number}\n") for index in range(8)
        ))
        assert [
            (tmp_path / f"result-{index}.txt").read_text() for index in range(8)
        ] == [f"round {round_number}\n"] * 8

    try:
        for round_number in range(2):
            asyncio.run(file_operations(round_number))
    finally:
        asyncio.run(environment.cleanup())


async def test_local_file_workers_keep_the_existing_concurrency_limit(tmp_path, monkeypatch):
    environment = local_environment(tmp_path)
    (tmp_path / "data.txt").write_text("retained\n")
    real_read = local_module.read_regular_bytes
    four_started = threading.Event()
    release = threading.Event()
    counter_lock = threading.Lock()
    active = maximum = 0

    def held_read(*args, **kwargs):
        nonlocal active, maximum
        with counter_lock:
            active += 1
            maximum = max(maximum, active)
            if active == 4:
                four_started.set()
        try:
            assert release.wait(timeout=2)
            return real_read(*args, **kwargs)
        finally:
            with counter_lock:
                active -= 1

    monkeypatch.setattr(local_module, "read_regular_bytes", held_read)
    reads = asyncio.gather(*(environment.read_file("data.txt") for _ in range(8)))
    try:
        assert await asyncio.to_thread(four_started.wait, 1)
        await asyncio.sleep(0.03)
        assert maximum == 4
    finally:
        release.set()
        try:
            assert await reads == ["retained\n"] * 8
        finally:
            await environment.cleanup()
    assert active == 0
