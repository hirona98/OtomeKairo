"""SQLite の write_memory 実行アダプタ。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from otomekairo.infra.sqlite.memory_job_impl import (
    enqueue_embedding_sync_jobs,
    ensure_claimed_memory_job,
    mark_memory_job_completed,
)
from otomekairo.infra.sqlite.write_memory_context_impl import (
    apply_context_updates,
    apply_event_about_time,
    apply_event_affect_updates,
    apply_event_entities,
)
from otomekairo.infra.sqlite.write_memory_load_impl import load_write_memory_job_execution_state
from otomekairo.infra.sqlite.write_memory_preference_impl import apply_preference_updates
from otomekairo.infra.sqlite.write_memory_state_impl import (
    apply_state_about_time,
    apply_state_entities,
    apply_state_updates,
    sync_current_emotion_from_long_mood_state,
)


# Block: write_memory 実行アダプタ
class SqliteWriteMemoryExecutionStore:
    # Block: claim 済み job 確認
    def ensure_claimed_memory_job_in_transaction(
        self,
        *,
        connection: sqlite3.Connection,
        job_id: str,
    ) -> None:
        ensure_claimed_memory_job(
            connection=connection,
            job_id=job_id,
        )

    # Block: 実行状態読み込み
    def load_write_memory_job_execution_state(
        self,
        *,
        connection: sqlite3.Connection,
        memory_job,
        validated_payload,
    ):
        return load_write_memory_job_execution_state(
            connection=connection,
            memory_job=memory_job,
            validated_payload=validated_payload,
        )

    # Block: 計画適用
    def apply_write_memory_plan_in_transaction(
        self,
        *,
        connection: sqlite3.Connection,
        memory_write_plan: dict[str, Any],
        created_at: int,
    ) -> dict[str, list[dict[str, Any]]]:
        memory_state_targets, state_embedding_targets, state_id_by_ref = apply_state_updates(
            connection=connection,
            state_updates=list(memory_write_plan["state_updates"]),
            created_at=created_at,
        )
        sync_current_emotion_from_long_mood_state(
            connection=connection,
            state_updates=list(memory_write_plan["state_updates"]),
            state_id_by_ref=state_id_by_ref,
            created_at=created_at,
        )
        apply_preference_updates(
            connection=connection,
            preference_updates=list(memory_write_plan["preference_updates"]),
            created_at=created_at,
        )
        event_affect_targets = apply_event_affect_updates(
            connection=connection,
            event_affect_updates=list(memory_write_plan["event_affect"]),
            created_at=created_at,
        )
        apply_event_about_time(
            connection=connection,
            event_annotations=list(memory_write_plan["event_annotations"]),
            created_at=created_at,
        )
        apply_event_entities(
            connection=connection,
            event_annotations=list(memory_write_plan["event_annotations"]),
            created_at=created_at,
        )
        apply_context_updates(
            connection=connection,
            context_updates=dict(memory_write_plan["context_updates"]),
            state_id_by_ref=state_id_by_ref,
            created_at=created_at,
        )
        apply_state_about_time(
            connection=connection,
            state_updates=list(memory_write_plan["state_updates"]),
            state_id_by_ref=state_id_by_ref,
            created_at=created_at,
        )
        apply_state_entities(
            connection=connection,
            state_updates=list(memory_write_plan["state_updates"]),
            state_id_by_ref=state_id_by_ref,
            created_at=created_at,
        )
        return {
            "memory_state_targets": memory_state_targets,
            "embedding_targets": [
                *state_embedding_targets,
                *event_affect_targets,
            ],
        }

    # Block: 後続 job enqueue
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

    # Block: claim 済み job 完了
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


# Block: 設定文字列取得
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
