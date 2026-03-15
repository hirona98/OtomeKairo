"""SQLite の write_memory 実行状態読み込み処理。"""

from __future__ import annotations

import sqlite3
from typing import Any

from otomekairo.infra.sqlite_store_memory_helpers import (
    _action_entries_for_write_memory_plan,
    _browse_fact_entries_for_write_memory_plan,
    _fetch_events_for_ids,
    _recent_dialogue_context_for_write_memory_plan,
    _write_memory_plan_event_entries,
    _write_memory_plan_long_mood_entry,
    _write_memory_plan_preference_entries,
)
from otomekairo.infra.sqlite_store_snapshots import _decoded_object_json
from otomekairo.schema.runtime_types import MemoryJobRecord
from otomekairo.usecase.run_write_memory_job import WriteMemoryJobExecutionState
from otomekairo.usecase.write_memory_plan import validate_write_memory_event_snapshots


# Block: 実行状態読み込み
def load_write_memory_job_execution_state(
    *,
    connection: sqlite3.Connection,
    memory_job: MemoryJobRecord,
    validated_payload: dict[str, Any],
) -> WriteMemoryJobExecutionState:
    del memory_job
    source_event_ids = list(validated_payload["source_event_ids"])
    event_rows = _fetch_events_for_ids(
        connection=connection,
        event_ids=source_event_ids,
    )
    event_entries = _write_memory_plan_event_entries(event_rows)
    validate_write_memory_event_snapshots(
        payload=validated_payload,
        event_entries=event_entries,
    )
    cycle_id = str(validated_payload["cycle_id"])
    action_entries = _action_entries_for_write_memory_plan(
        connection=connection,
        cycle_id=cycle_id,
    )
    browse_fact_entries = _browse_fact_entries_for_write_memory_plan(
        connection=connection,
        cycle_id=cycle_id,
    )
    self_state_row = connection.execute(
        """
        SELECT current_emotion_json
        FROM self_state
        WHERE row_id = 1
        """
    ).fetchone()
    if self_state_row is None:
        raise RuntimeError("self_state row is missing")
    return WriteMemoryJobExecutionState(
        validated_payload=dict(validated_payload),
        source_event_ids=source_event_ids,
        cycle_id=cycle_id,
        event_rows=event_rows,
        event_entries=event_entries,
        action_entries=action_entries,
        browse_fact_entries=browse_fact_entries,
        current_emotion=_decoded_object_json(self_state_row["current_emotion_json"]),
        existing_long_mood_state=_write_memory_plan_long_mood_entry(
            connection=connection,
        ),
        existing_preference_entries=_write_memory_plan_preference_entries(
            connection=connection,
        ),
        recent_dialogue_context=_recent_dialogue_context_for_write_memory_plan(
            connection=connection,
            before_created_at=min(int(event_row["created_at"]) for event_row in event_rows),
        ),
    )
