"""SQLite の cognition 基底状態 query 実装。"""

from __future__ import annotations

import json
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite_store_legacy_runtime import _merge_runtime_settings


# Block: cognition 基底状態読み出し
def load_cognition_base_state(
    *,
    backend: SqliteBackend,
    default_settings: dict[str, object],
) -> dict[str, Any]:
    with backend._connect() as connection:
        self_row = connection.execute(
            """
            SELECT
                personality_json,
                current_emotion_json,
                long_term_goals_json,
                relationship_overview_json,
                invariants_json,
                personality_updated_at,
                updated_at
            FROM self_state
            WHERE row_id = 1
            """
        ).fetchone()
        attention_row = connection.execute(
            """
            SELECT
                primary_focus_json,
                secondary_focuses_json,
                suppressed_items_json,
                revisit_queue_json,
                updated_at
            FROM attention_state
            WHERE row_id = 1
            """
        ).fetchone()
        body_row = connection.execute(
            """
            SELECT
                posture_json,
                mobility_json,
                sensor_availability_json,
                output_locks_json,
                load_json,
                updated_at
            FROM body_state
            WHERE row_id = 1
            """
        ).fetchone()
        world_row = connection.execute(
            """
            SELECT
                location_json,
                situation_summary,
                surroundings_json,
                affordances_json,
                constraints_json,
                attention_targets_json,
                external_waits_json,
                updated_at
            FROM world_state
            WHERE row_id = 1
            """
        ).fetchone()
        drive_row = connection.execute(
            """
            SELECT
                drive_levels_json,
                priority_effects_json,
                updated_at
            FROM drive_state
            WHERE row_id = 1
            """
        ).fetchone()
        runtime_settings_row = connection.execute(
            """
            SELECT values_json
            FROM runtime_settings
            WHERE row_id = 1
            """
        ).fetchone()
        active_task_rows = connection.execute(
            """
            SELECT
                task_id,
                task_kind,
                task_status,
                goal_hint,
                completion_hint_json,
                resume_condition_json,
                interruptible,
                priority,
                created_at,
                updated_at,
                title,
                step_hints_json
            FROM task_state
            WHERE task_status = 'active'
            ORDER BY priority DESC, updated_at DESC
            LIMIT 3
            """
        ).fetchall()
        waiting_task_rows = connection.execute(
            """
            SELECT
                task_id,
                task_kind,
                task_status,
                goal_hint,
                completion_hint_json,
                resume_condition_json,
                interruptible,
                priority,
                created_at,
                updated_at,
                title,
                step_hints_json
            FROM task_state
            WHERE task_status = 'waiting_external'
            ORDER BY priority DESC, updated_at DESC
            LIMIT 3
            """
        ).fetchall()
    if (
        self_row is None
        or attention_row is None
        or body_row is None
        or world_row is None
        or drive_row is None
        or runtime_settings_row is None
    ):
        raise RuntimeError("singleton state rows are missing")
    effective_settings = _merge_runtime_settings(
        default_settings,
        json.loads(runtime_settings_row["values_json"]),
    )
    embedding_model = effective_settings.get("llm.embedding_model")
    if not isinstance(embedding_model, str) or not embedding_model:
        raise RuntimeError("llm.embedding_model must be non-empty string")
    return {
        "self_row": self_row,
        "attention_row": attention_row,
        "body_row": body_row,
        "world_row": world_row,
        "drive_row": drive_row,
        "active_task_rows": active_task_rows,
        "waiting_task_rows": waiting_task_rows,
        "effective_settings": effective_settings,
        "embedding_model": embedding_model,
    }
