"""Explicit ownership for local environments created by tests."""

from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack

import pytest

from opencollab.adapters.env import LocalEnvironment


@pytest.fixture
async def local_environment_factory() -> AsyncIterator[Callable[[str], LocalEnvironment]]:
    async with AsyncExitStack() as resources:
        def create(workspace: str = ".") -> LocalEnvironment:
            environment = LocalEnvironment(workspace)
            resources.push_async_callback(environment.cleanup)
            return environment

        yield create
