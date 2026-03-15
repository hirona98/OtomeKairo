"""SQLite の明示的な write_memory unit_of_work。"""

from __future__ import annotations

from dataclasses import dataclass

from otomekairo.infra.sqlite_store_legacy_runtime import _now_ms
from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite.write_memory_execution_store import SqliteWriteMemoryExecutionStore
from otomekairo.schema.runtime_types import MemoryJobRecord
from otomekairo.usecase.run_write_memory_job import run_write_memory_job


# Block: write_memory unit_of_work アダプタ
@dataclass(frozen=True, slots=True)
class SqliteWriteMemoryUnitOfWork:
    backend: SqliteBackend

    def complete_write_memory_job(self, *, memory_job: MemoryJobRecord) -> str:
        now_ms = _now_ms()
        execution_store = SqliteWriteMemoryExecutionStore()
        with self.backend._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return run_write_memory_job(
                connection=connection,
                store=execution_store,
                memory_job=memory_job,
                now_ms=now_ms,
            )
