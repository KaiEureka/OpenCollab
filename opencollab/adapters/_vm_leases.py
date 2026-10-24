from __future__ import annotations

import asyncio
import math
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from pathlib import Path

from opencollab.domain.vm_resources import VMResource


class VMCapacityError(RuntimeError):
    pass


class _VMLeases:
    def __init__(self, database: str, resources: Sequence[VMResource], wait_timeout: float | None) -> None:
        if not isinstance(database, str) or not database.strip() or database == ":memory:":
            raise ValueError("VM lease_database must name a shared SQLite file")
        if wait_timeout is not None and (
            isinstance(wait_timeout, bool) or not isinstance(wait_timeout, (int, float))
            or not math.isfinite(wait_timeout) or wait_timeout < 0
        ):
            raise ValueError("VM wait_timeout must be finite and nonnegative or None")
        self.database = str(Path(database).expanduser().absolute())
        self.resources = tuple(resources)
        self.wait_timeout = wait_timeout
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS vm_leases "
                "(resource_id TEXT PRIMARY KEY, owner TEXT NOT NULL, workspace TEXT NOT NULL)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.database, timeout=0.1)) as connection, connection:
            yield connection

    def _try_acquire(self, owner: str) -> VMResource | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for resource in self.resources:
                claimed = connection.execute(
                    "INSERT OR IGNORE INTO vm_leases (resource_id, owner, workspace) VALUES (?, ?, ?)",
                    (resource.resource_id, owner, f"{resource.workspace_root}/{owner}"),
                )
                if claimed.rowcount:
                    return resource
        return None

    async def acquire(self, owner: str, closing: asyncio.Event | None = None) -> VMResource:
        deadline = None if self.wait_timeout is None else asyncio.get_running_loop().time() + self.wait_timeout
        while True:
            if closing is not None and closing.is_set():
                raise RuntimeError("VM isolation pool closed while waiting for capacity")
            # The transaction is short and synchronous, so cancellation cannot
            # abandon a successful claim in a worker thread.
            try:
                resource = self._try_acquire(owner)
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc):
                    raise
                resource = None
            if resource is not None:
                return resource
            remaining = None if deadline is None else deadline - asyncio.get_running_loop().time()
            if remaining is not None and remaining <= 0:
                raise VMCapacityError("VM capacity exhausted; release a lease or configure cancellable waiting")
            await asyncio.sleep(0.05 if remaining is None else min(0.05, remaining))

    def release(self, resource: VMResource, owner: str) -> None:
        with self._connect() as connection:
            removed = connection.execute(
                "DELETE FROM vm_leases WHERE resource_id = ? AND owner = ?", (resource.resource_id, owner),
            )
            if removed.rowcount != 1:
                raise RuntimeError(f"VM lease ownership changed for {resource.resource_id}")
