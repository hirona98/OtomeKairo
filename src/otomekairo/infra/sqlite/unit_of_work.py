"""SQLite-backed explicit write_memory unit of work."""

from __future__ import annotations

from dataclasses import dataclass

from otomekairo.infra.sqlite_store_legacy_runtime import _now_ms
from otomekairo.infra.sqlite_state_store import SqliteStateStore
from otomekairo.schema.runtime_types import MemoryJobRecord
from otomekairo.usecase.run_write_memory_job import run_write_memory_job


# Block: Write memory unit of work adapter
@dataclass(frozen=True, slots=True)
class SqliteWriteMemoryUnitOfWork:
    backend: SqliteStateStore

    def complete_write_memory_job(self, *, memory_job: MemoryJobRecord) -> str:
        now_ms = _now_ms()
        with self.backend._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return run_write_memory_job(
                connection=connection,
                store=self.backend,
                memory_job=memory_job,
                now_ms=now_ms,
            )
