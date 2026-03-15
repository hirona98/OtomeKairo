"""SQLite runtime query implementations."""

from __future__ import annotations

import json
from typing import Any

from otomekairo.infra.sqlite.backend import (
    RETRIEVAL_STABLE_PREFERENCE_BUCKET_LIMIT,
    STABLE_PREFERENCE_BUCKET_LIMIT,
    SqliteBackend,
)
from otomekairo.infra.sqlite_store_legacy_runtime import _merge_runtime_settings, _now_ms
from otomekairo.infra.sqlite_store_memory_helpers import (
    _fetch_event_about_time_for_memory_snapshot,
    _fetch_event_entities_for_memory_snapshot,
    _fetch_event_links_for_memory_snapshot,
    _fetch_event_threads_for_memory_snapshot,
    _fetch_state_about_time_for_memory_snapshot,
    _fetch_state_entities_for_memory_snapshot,
    _fetch_state_links_for_memory_snapshot,
)
from otomekairo.infra.sqlite_store_settings_editor import (
    _decode_camera_connection_rows,
    _decode_settings_editor_state_row,
    _decode_settings_preset_rows,
    _fetch_editor_preset_rows,
    _read_active_retrieval_profile,
)
from otomekairo.infra.sqlite_store_snapshots import (
    _build_memory_snapshot_rows,
    _build_task_snapshot_rows,
    _memory_snapshot_entry,
    _preference_snapshot_entry,
    _read_stable_preference_projection_rows,
)
from otomekairo.infra.sqlite_store_runtime_view import (
    _public_body_state_summary,
    _public_drive_state_summary,
    _public_emotion_summary,
    _public_primary_focus,
    _public_world_state_summary,
)
from otomekairo.infra.sqlite_store_vectors import (
    _merge_ranked_event_rows,
    _merge_ranked_memory_rows,
    _search_vec_similarity_hits,
)
from otomekairo.schema.runtime_types import CognitionStateSnapshot
from otomekairo.schema.settings import build_settings_editor_system_keys


# Block: Health read
def read_health() -> dict[str, Any]:
    return {"status": "ok", "server_time": _now_ms()}


# Block: Status read
def read_status(backend: SqliteBackend) -> dict[str, Any]:
    now_ms = _now_ms()
    with backend._connect() as connection:
        runtime_row = connection.execute(
            """
            SELECT owner_token
            FROM runtime_leases
            WHERE lease_name = ?
              AND expires_at >= ?
            """,
            ("primary_runtime", now_ms),
        ).fetchone()
        commit_row = connection.execute(
            """
            SELECT commit_id, cycle_id
            FROM commit_records
            ORDER BY commit_id DESC
            LIMIT 1
            """
        ).fetchone()
        self_row = connection.execute(
            """
            SELECT current_emotion_json
            FROM self_state
            WHERE row_id = 1
            """
        ).fetchone()
        attention_row = connection.execute(
            """
            SELECT primary_focus_json
            FROM attention_state
            WHERE row_id = 1
            """
        ).fetchone()
        body_row = connection.execute(
            """
            SELECT posture_json, sensor_availability_json, load_json
            FROM body_state
            WHERE row_id = 1
            """
        ).fetchone()
        world_row = connection.execute(
            """
            SELECT situation_summary, external_waits_json
            FROM world_state
            WHERE row_id = 1
            """
        ).fetchone()
        drive_row = connection.execute(
            """
            SELECT priority_effects_json
            FROM drive_state
            WHERE row_id = 1
            """
        ).fetchone()
        task_counts_row = connection.execute(
            """
            SELECT
                SUM(CASE WHEN task_status = 'active' THEN 1 ELSE 0 END) AS active_count,
                SUM(CASE WHEN task_status = 'waiting_external' THEN 1 ELSE 0 END) AS waiting_count
            FROM task_state
            """
        ).fetchone()
    if (
        self_row is None
        or attention_row is None
        or body_row is None
        or world_row is None
        or drive_row is None
    ):
        raise RuntimeError("singleton state rows are missing")
    runtime_payload: dict[str, Any] = {"is_running": runtime_row is not None}
    if commit_row is not None:
        runtime_payload["last_cycle_id"] = commit_row["cycle_id"]
        runtime_payload["last_commit_id"] = commit_row["commit_id"]
    current_emotion_json = json.loads(self_row["current_emotion_json"])
    primary_focus_json = json.loads(attention_row["primary_focus_json"])
    posture_json = json.loads(body_row["posture_json"])
    sensor_availability_json = json.loads(body_row["sensor_availability_json"])
    load_json = json.loads(body_row["load_json"])
    external_waits_json = json.loads(world_row["external_waits_json"])
    priority_effects_json = json.loads(drive_row["priority_effects_json"])
    active_count = int(task_counts_row["active_count"] or 0)
    waiting_count = int(task_counts_row["waiting_count"] or 0)
    self_state_payload: dict[str, Any] = {
        "current_emotion": _public_emotion_summary(current_emotion_json),
    }
    return {
        "server_time": now_ms,
        "runtime": runtime_payload,
        "self_state": self_state_payload,
        "attention_state": {"primary_focus": _public_primary_focus(primary_focus_json)},
        "body_state": _public_body_state_summary(
            posture_json=posture_json,
            sensor_availability_json=sensor_availability_json,
            load_json=load_json,
        ),
        "world_state": _public_world_state_summary(
            situation_summary=str(world_row["situation_summary"]),
            external_waits_json=external_waits_json,
        ),
        "drive_state": _public_drive_state_summary(
            priority_effects_json=priority_effects_json,
        ),
        "task_state": {
            "active_task_count": active_count,
            "waiting_task_count": waiting_count,
        },
    }


# Block: Effective settings read
def read_effective_settings(
    backend: SqliteBackend,
    default_settings: dict[str, Any],
) -> dict[str, Any]:
    with backend._connect() as connection:
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
    return _merge_runtime_settings(default_settings, runtime_values)


# Block: Cognition state read
def read_cognition_state(
    backend: SqliteBackend,
    default_settings: dict[str, Any],
    *,
    observation_hint_text: str | None = None,
) -> CognitionStateSnapshot:
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
    with backend._connect() as connection:
        retrieval_profile = _read_active_retrieval_profile(connection=connection)
        similarity_limit = int(retrieval_profile["semantic_top_k"])
        similar_episodes_limit = effective_settings.get("memory.similar_episodes_limit")
        if isinstance(similar_episodes_limit, int) and not isinstance(similar_episodes_limit, bool):
            similarity_limit = min(similarity_limit, similar_episodes_limit)
        recent_event_fetch_limit = max(8, int(retrieval_profile["recent_window_limit"]))
        memory_fetch_limit = max(16, similarity_limit * 2)
        recent_event_rows = connection.execute(
            """
            SELECT
                events.event_id,
                events.source,
                events.kind,
                events.observation_summary,
                events.action_summary,
                events.result_summary,
                events.created_at
            FROM events
            WHERE events.searchable = 1
            ORDER BY events.created_at DESC
            LIMIT ?
            """,
            (recent_event_fetch_limit,),
        ).fetchall()
        memory_rows = connection.execute(
            """
            SELECT
                memory_state_id,
                memory_kind,
                body_text,
                payload_json,
                confidence,
                importance,
                memory_strength,
                created_at,
                updated_at,
                last_confirmed_at
            FROM memory_states
            WHERE searchable = 1
              AND memory_kind IN ('summary', 'fact', 'relation', 'long_mood_state', 'reflection_note')
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (memory_fetch_limit,),
        ).fetchall()
        affect_rows = connection.execute(
            """
            SELECT
                event_affects.event_affect_id,
                event_affects.event_id,
                event_affects.moment_affect_text,
                event_affects.moment_affect_labels_json,
                event_affects.vad_json,
                event_affects.confidence,
                event_affects.created_at,
                events.source,
                events.kind,
                events.observation_summary,
                events.action_summary,
                events.result_summary
            FROM event_affects
            INNER JOIN events
                    ON events.event_id = event_affects.event_id
            WHERE events.searchable = 1
            ORDER BY event_affects.created_at DESC
            LIMIT 6
            """
        ).fetchall()
        stable_preference_rows = _read_stable_preference_projection_rows(
            connection=connection,
            bucket_limit=STABLE_PREFERENCE_BUCKET_LIMIT,
        )
        retrieval_preference_rows = _read_stable_preference_projection_rows(
            connection=connection,
            bucket_limit=RETRIEVAL_STABLE_PREFERENCE_BUCKET_LIMIT,
        )
        stable_long_mood_row = connection.execute(
            """
            SELECT
                memory_state_id,
                memory_kind,
                body_text,
                payload_json,
                confidence,
                importance,
                memory_strength,
                created_at,
                updated_at,
                last_confirmed_at
            FROM memory_states
            WHERE memory_kind = 'long_mood_state'
            ORDER BY updated_at DESC, created_at DESC, memory_state_id DESC
            LIMIT 1
            """
        ).fetchone()
        if observation_hint_text is not None and observation_hint_text.strip():
            similarity_hits = _search_vec_similarity_hits(
                connection=connection,
                query_text=observation_hint_text.strip(),
                embedding_model=embedding_model,
                limit=similarity_limit,
            )
            recent_event_rows = _merge_ranked_event_rows(
                connection=connection,
                ranked_hits=similarity_hits,
                fallback_rows=recent_event_rows,
            )
            memory_rows = _merge_ranked_memory_rows(
                connection=connection,
                ranked_hits=similarity_hits,
                fallback_rows=memory_rows,
            )
        recent_event_ids = [str(row["event_id"]) for row in recent_event_rows]
        memory_state_ids = [str(row["memory_state_id"]) for row in memory_rows]
        event_link_rows = _fetch_event_links_for_memory_snapshot(
            connection=connection,
            event_ids=recent_event_ids,
        )
        event_thread_rows = _fetch_event_threads_for_memory_snapshot(
            connection=connection,
            event_ids=recent_event_ids,
        )
        event_about_time_rows = _fetch_event_about_time_for_memory_snapshot(
            connection=connection,
            event_ids=recent_event_ids,
        )
        event_entity_rows = _fetch_event_entities_for_memory_snapshot(
            connection=connection,
            event_ids=recent_event_ids,
        )
        state_link_rows = _fetch_state_links_for_memory_snapshot(
            connection=connection,
            memory_state_ids=memory_state_ids,
        )
        state_about_time_rows = _fetch_state_about_time_for_memory_snapshot(
            connection=connection,
            memory_state_ids=memory_state_ids,
        )
        state_entity_rows = _fetch_state_entities_for_memory_snapshot(
            connection=connection,
            memory_state_ids=memory_state_ids,
        )
    return CognitionStateSnapshot(
        self_state={
            "personality": json.loads(self_row["personality_json"]),
            "current_emotion": json.loads(self_row["current_emotion_json"]),
            "long_term_goals": json.loads(self_row["long_term_goals_json"]),
            "relationship_overview": json.loads(self_row["relationship_overview_json"]),
            "invariants": json.loads(self_row["invariants_json"]),
            "personality_updated_at": int(self_row["personality_updated_at"]),
            "updated_at": int(self_row["updated_at"]),
        },
        attention_state={
            "primary_focus": json.loads(attention_row["primary_focus_json"]),
            "secondary_focuses": json.loads(attention_row["secondary_focuses_json"]),
            "suppressed_items": json.loads(attention_row["suppressed_items_json"]),
            "revisit_queue": json.loads(attention_row["revisit_queue_json"]),
            "updated_at": int(attention_row["updated_at"]),
        },
        body_state={
            "posture": json.loads(body_row["posture_json"]),
            "mobility": json.loads(body_row["mobility_json"]),
            "sensor_availability": json.loads(body_row["sensor_availability_json"]),
            "output_locks": json.loads(body_row["output_locks_json"]),
            "load": json.loads(body_row["load_json"]),
            "updated_at": int(body_row["updated_at"]),
        },
        world_state={
            "location": json.loads(world_row["location_json"]),
            "situation_summary": str(world_row["situation_summary"]),
            "surroundings": json.loads(world_row["surroundings_json"]),
            "affordances": json.loads(world_row["affordances_json"]),
            "constraints": json.loads(world_row["constraints_json"]),
            "attention_targets": json.loads(world_row["attention_targets_json"]),
            "external_waits": json.loads(world_row["external_waits_json"]),
            "updated_at": int(world_row["updated_at"]),
        },
        drive_state={
            "drive_levels": json.loads(drive_row["drive_levels_json"]),
            "priority_effects": json.loads(drive_row["priority_effects_json"]),
            "updated_at": int(drive_row["updated_at"]),
        },
        task_snapshot=_build_task_snapshot_rows(
            active_task_rows=active_task_rows,
            waiting_task_rows=waiting_task_rows,
        ),
        memory_snapshot=_build_memory_snapshot_rows(
            recent_event_rows=recent_event_rows,
            memory_rows=memory_rows,
            affect_rows=affect_rows,
            stable_preference_rows=retrieval_preference_rows,
            event_link_rows=event_link_rows,
            event_thread_rows=event_thread_rows,
            event_about_time_rows=event_about_time_rows,
            event_entity_rows=event_entity_rows,
            state_link_rows=state_link_rows,
            state_about_time_rows=state_about_time_rows,
            state_entity_rows=state_entity_rows,
        ),
        stable_preference_items=[
            _preference_snapshot_entry(row)
            for row in stable_preference_rows
        ],
        stable_long_mood_item=(
            _memory_snapshot_entry(stable_long_mood_row)
            if stable_long_mood_row is not None
            else None
        ),
        retrieval_profile=retrieval_profile,
        effective_settings=effective_settings,
    )


# Block: Runtime work state read
def read_runtime_work_state(backend: SqliteBackend) -> dict[str, bool]:
    with backend._connect() as connection:
        row = connection.execute(
            """
            SELECT
                CASE
                    WHEN EXISTS(
                        SELECT 1
                        FROM settings_change_sets
                        WHERE status = 'queued'
                    )
                    OR EXISTS(
                        SELECT 1
                        FROM settings_overrides
                        WHERE status = 'queued'
                    )
                    OR EXISTS(
                        SELECT 1
                        FROM pending_inputs
                        WHERE status = 'queued'
                    )
                    OR EXISTS(
                        SELECT 1
                        FROM task_state
                        WHERE task_kind = 'browse'
                          AND task_status = 'waiting_external'
                    )
                    THEN 1
                    ELSE 0
                END AS has_short_cycle_work,
                EXISTS(
                    SELECT 1
                    FROM memory_jobs
                    WHERE status = 'queued'
                ) AS has_memory_job
            """
        ).fetchone()
    if row is None:
        return {
            "has_short_cycle_work": False,
            "has_memory_job": False,
        }
    return {
        "has_short_cycle_work": bool(row["has_short_cycle_work"]),
        "has_memory_job": bool(row["has_memory_job"]),
    }


# Block: Settings editor read
def read_settings_editor(backend: SqliteBackend) -> dict[str, Any]:
    with backend._connect() as connection:
        editor_row = connection.execute(
            """
            SELECT
                active_character_preset_id,
                active_behavior_preset_id,
                active_conversation_preset_id,
                active_memory_preset_id,
                active_motion_preset_id,
                system_values_json,
                revision,
                updated_at
            FROM settings_editor_state
            WHERE row_id = 1
            """
        ).fetchone()
        character_rows = _fetch_editor_preset_rows(
            connection=connection,
            table_name="character_presets",
        )
        behavior_rows = _fetch_editor_preset_rows(
            connection=connection,
            table_name="behavior_presets",
        )
        conversation_rows = _fetch_editor_preset_rows(
            connection=connection,
            table_name="conversation_presets",
        )
        memory_rows = _fetch_editor_preset_rows(
            connection=connection,
            table_name="memory_presets",
        )
        motion_rows = _fetch_editor_preset_rows(
            connection=connection,
            table_name="motion_presets",
        )
        camera_connection_rows = connection.execute(
            """
            SELECT
                camera_connection_id,
                is_enabled,
                display_name,
                host,
                username,
                password,
                sort_order,
                created_at,
                updated_at
            FROM camera_connections
            ORDER BY sort_order ASC, updated_at DESC
            """
        ).fetchall()
    if editor_row is None:
        raise RuntimeError("settings_editor_state row is missing")
    editor_state = _decode_settings_editor_state_row(editor_row)
    return {
        "editor_state": {
            "revision": editor_state["revision"],
            "active_character_preset_id": editor_state["active_character_preset_id"],
            "active_behavior_preset_id": editor_state["active_behavior_preset_id"],
            "active_conversation_preset_id": editor_state["active_conversation_preset_id"],
            "active_memory_preset_id": editor_state["active_memory_preset_id"],
            "active_motion_preset_id": editor_state["active_motion_preset_id"],
            "system_values": dict(editor_state["system_values"]),
        },
        "character_presets": _decode_settings_preset_rows(character_rows),
        "behavior_presets": _decode_settings_preset_rows(behavior_rows),
        "conversation_presets": _decode_settings_preset_rows(conversation_rows),
        "memory_presets": _decode_settings_preset_rows(memory_rows),
        "motion_presets": _decode_settings_preset_rows(motion_rows),
        "camera_connections": _decode_camera_connection_rows(camera_connection_rows),
        "constraints": {
            "editable_system_keys": list(build_settings_editor_system_keys()),
        },
    }


# Block: Enabled camera connections read
def read_enabled_camera_connections(backend: SqliteBackend) -> list[dict[str, Any]]:
    with backend._connect() as connection:
        camera_connection_rows = connection.execute(
            """
            SELECT
                camera_connection_id,
                is_enabled,
                display_name,
                host,
                username,
                password,
                sort_order,
                created_at,
                updated_at
            FROM camera_connections
            WHERE is_enabled = 1
            ORDER BY sort_order ASC, updated_at DESC
            """
        ).fetchall()
    return _decode_camera_connection_rows(camera_connection_rows)
