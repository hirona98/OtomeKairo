"""Explicit write_memory transaction boundary."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any, Protocol

from otomekairo.schema.runtime_types import MemoryJobRecord

if TYPE_CHECKING:
    from otomekairo.usecase.run_write_memory_job import WriteMemoryJobExecutionState


# Block: Write memory execution contract
class WriteMemoryExecutionStore(Protocol):
    def ensure_claimed_memory_job_in_transaction(
        self,
        *,
        connection: sqlite3.Connection,
        job_id: str,
    ) -> None:
        ...

    def load_write_memory_job_execution_state(
        self,
        *,
        connection: sqlite3.Connection,
        memory_job: MemoryJobRecord,
        validated_payload: dict[str, Any],
    ) -> WriteMemoryJobExecutionState:
        ...

    def apply_write_memory_plan_in_transaction(
        self,
        *,
        connection: sqlite3.Connection,
        memory_write_plan: dict[str, Any],
        created_at: int,
    ) -> dict[str, list[dict[str, Any]]]:
        ...

    def enqueue_write_memory_followup_jobs_in_transaction(
        self,
        *,
        connection: sqlite3.Connection,
        cycle_id: str,
        event_rows: list[sqlite3.Row],
        source_event_ids: list[str],
        embedding_targets: list[dict[str, Any]],
        created_at: int,
    ) -> None:
        ...

    def mark_memory_job_completed_in_transaction(
        self,
        *,
        connection: sqlite3.Connection,
        job_id: str,
        completed_at: int,
    ) -> None:
        ...


# Block: Write memory unit of work contract
class WriteMemoryUnitOfWork(Protocol):
    def complete_write_memory_job(self, *, memory_job: MemoryJobRecord) -> str:
        ...
