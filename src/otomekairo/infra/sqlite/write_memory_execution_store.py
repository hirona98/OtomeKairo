"""SQLite write_memory execution adapter."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite.memory_job_impl import (
    enqueue_embedding_sync_jobs,
    ensure_claimed_memory_job,
    mark_memory_job_completed,
)


# Block: Write memory execution adapter
@dataclass(frozen=True, slots=True)
class SqliteWriteMemoryExecutionStore:
    backend: SqliteBackend

    def ensure_claimed_memory_job_in_transaction(
        self,
        *,
        connection: sqlite3.Connection,
        job_id: str,
    ) -> None:
        ensure_claimed_memory_job(connection=connection, job_id=job_id)

    def load_write_memory_job_execution_state(
        self,
        *,
        connection: sqlite3.Connection,
        memory_job,
        validated_payload,
    ):
        return self.backend.load_write_memory_job_execution_state(
            connection=connection,
            memory_job=memory_job,
            validated_payload=validated_payload,
        )

    def apply_write_memory_plan_in_transaction(
        self,
        *,
        connection: sqlite3.Connection,
        memory_write_plan,
        created_at: int,
    ):
        return self.backend.apply_write_memory_plan_in_transaction(
            connection=connection,
            memory_write_plan=memory_write_plan,
            created_at=created_at,
        )

    def enqueue_write_memory_followup_jobs_in_transaction(
        self,
        *,
        connection: sqlite3.Connection,
        cycle_id: str,
        event_rows: list[sqlite3.Row],
        source_event_ids: list[str],
        embedding_targets: list[dict[str, object]],
        created_at: int,
    ) -> None:
        event_embedding_targets = [
            {
                "entity_type": "event",
                "entity_id": str(event_row["event_id"]),
                "source_updated_at": int(event_row["source_updated_at"]),
                "current_searchable": bool(event_row["searchable"]),
            }
            for event_row in event_rows
        ]
        enqueue_embedding_sync_jobs(
            connection=connection,
            cycle_id=cycle_id,
            source_event_ids=source_event_ids,
            targets=[*event_embedding_targets, *embedding_targets],
            embedding_model=_require_runtime_setting_string(
                connection=connection,
                key="llm.embedding_model",
            ),
            created_at=created_at,
        )

    def mark_memory_job_completed_in_transaction(
        self,
        *,
        connection: sqlite3.Connection,
        job_id: str,
        completed_at: int,
    ) -> None:
        mark_memory_job_completed(
            connection=connection,
            job_id=job_id,
            completed_at=completed_at,
        )


# Block: Runtime setting string read
def _require_runtime_setting_string(
    *,
    connection: sqlite3.Connection,
    key: str,
) -> str:
    runtime_settings_row = connection.execute(
        """
        SELECT values_json
        FROM runtime_settings
        WHERE row_id = 1
        """
    ).fetchone()
    if runtime_settings_row is None:
        raise RuntimeError("runtime_settings row is missing")
    runtime_values = json.loads(runtime_settings_row["values_json"])
    value = runtime_values.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{key} must be non-empty string")
    return value
