"""SQLite-backed state and control plane access."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import sqlite_vec

from otomekairo.schema.runtime_types import (
    ActionHistoryRecord,
    CognitionStateSnapshot,
    MemoryJobRecord,
    PendingInputRecord,
    PendingInputMutationRecord,
    SettingsChangeSetRecord,
    SettingsOverrideRecord,
    TaskStateRecord,
    TaskStateMutationRecord,
)
from otomekairo.schema.settings import (
    build_default_camera_connections,
    build_default_settings,
    build_default_settings_editor_state,
    build_default_settings_editor_presets,
    build_settings_editor_system_keys,
    decode_requested_value,
    normalize_settings_editor_document,
)
from otomekairo.infra.sqlite_store_errors import (
    StoreConflictError,
    StoreValidationError,
)
from otomekairo.infra.sqlite_store_legacy_runtime import (
    LEGACY_AIVIS_RUNTIME_KEYS,
    LEGACY_OPTIONAL_BASE_URL_DEFAULTS,
    LEGACY_OUTPUT_PRESET_SETTING_KEYS,
    LEGACY_SETTING_KEY_ALIASES,
    _active_legacy_output_payload_for_migration,
    _bounded_float,
    _build_legacy_output_preset_payload,
    _build_v8_system_values_from_v7_row,
    _insert_migrated_editor_presets,
    _json_text,
    _legacy_settings_editor_state_seed_v5,
    _legacy_settings_preset_seeds_from_defaults,
    _merge_runtime_settings,
    _merged_unique_strings,
    _migrate_behavior_payload_to_v8,
    _migrate_llm_payload_to_conversation_payload,
    _migrate_memory_payload_to_v8,
    _migrate_output_payload_to_character_payload,
    _normalize_legacy_optional_base_urls,
    _normalize_legacy_optional_base_url_value,
    _normalize_legacy_output_preset_payload,
    _normalize_runtime_settings_updated_at,
    _normalize_runtime_settings_values,
    _normalized_target_entity_ref_json,
    _now_ms,
    _opaque_id,
    _preference_target_key,
    _quoted_identifier,
    _repo_root,
    _runtime_settings_seed_timestamps,
    _string_list,
    _string_list_or_empty,
    _upsert_runtime_setting_value,
)
from otomekairo.usecase.observation_normalization import (
    normalize_observation_kind,
    normalize_observation_source,
)
from otomekairo.usecase.camera_observation_payload import build_camera_observation_payload
from otomekairo.usecase.retrieval_public_view import (
    build_public_retrieval_detail,
    build_public_retrieval_summary,
)
from otomekairo.infra.sqlite_store_settings_editor import (
    _active_preset_payload,
    _canonical_editor_state_for_compare,
    _decode_camera_connection_rows,
    _decode_settings_editor_state_row,
    _decode_settings_preset_rows,
    _fetch_editor_preset_rows,
    _insert_settings_change_set,
    _materialize_effective_settings_from_editor,
    _normalize_settings_editor_system_values,
    _persist_settings_editor_state,
    _read_active_retrieval_profile,
    _replace_camera_connections,
    _replace_editor_preset_rows,
)
from otomekairo.infra.sqlite_store_snapshots import (
    _build_memory_snapshot_rows,
    _build_task_snapshot_rows,
    _decoded_object_json,
    _decoded_string_array_json,
    _event_summary_text,
    _event_entity_entries_from_annotation,
    _memory_snapshot_entry,
    _memory_state_revision_json,
    _memory_state_revision_json_from_row,
    _memory_state_target,
    _normalized_entity_name,
    _preference_snapshot_entry,
    _read_stable_preference_projection_rows,
    _state_about_time_from_row,
    _state_entity_entries_from_row,
)
from otomekairo.infra.sqlite_store_memory_helpers import (
    _action_entries_for_write_memory_plan,
    _browse_fact_entries_for_write_memory_plan,
    _build_event_preview_text,
    _current_emotion_json_from_long_mood_payload,
    _event_snapshot_refs_for_write_memory_job,
    _fetch_event_about_time_for_memory_snapshot,
    _fetch_event_about_time_for_preview,
    _fetch_event_affect_for_preview,
    _fetch_event_entities_for_memory_snapshot,
    _fetch_event_links_for_memory_snapshot,
    _fetch_event_threads_for_memory_snapshot,
    _fetch_events_for_ids,
    _fetch_state_about_time_for_memory_snapshot,
    _fetch_state_entities_for_memory_snapshot,
    _fetch_state_links_for_memory_snapshot,
    _recent_dialogue_context_for_write_memory_plan,
    _write_memory_plan_event_entries,
    _write_memory_plan_long_mood_entry,
    _write_memory_plan_preference_entries,
)
from otomekairo.infra.sqlite_store_job_helpers import (
    _embedding_sync_job_idempotency_key,
    _memory_job_error_text,
    _normalize_embedding_scopes,
    _normalize_quarantine_targets,
    _normalize_tidy_target_refs,
    _quarantine_memory_job_idempotency_key,
    _refresh_preview_job_idempotency_key,
    _resolve_embedding_source_text,
    _resolve_memory_job_payload_ref,
    _tidy_memory_job_idempotency_key,
    _write_memory_job_idempotency_key,
)
from otomekairo.infra.sqlite_store_live_state_seeds import (
    _attention_primary_focus_seed,
    _body_state_load_seed,
    _body_state_mobility_seed,
    _body_state_output_locks_seed,
    _body_state_posture_seed,
    _body_state_sensor_availability_seed,
    _drive_state_drive_levels_seed,
    _drive_state_priority_effects_seed,
    _self_state_current_emotion_seed,
    _self_state_invariants_seed,
    _self_state_long_term_goals_seed,
    _self_state_personality_seed,
    _self_state_relationship_overview_seed,
    _world_state_affordances_seed,
    _world_state_attention_targets_seed,
    _world_state_constraints_seed,
    _world_state_external_waits_seed,
    _world_state_location_seed,
    _world_state_situation_summary_seed,
    _world_state_surroundings_seed,
)
from otomekairo.infra.sqlite_store_runtime_view import (
    _action_command_summary,
    _action_result_summary,
    _body_state_has_current_shape,
    _commit_log_sync_error_text,
    _decode_body_state_row,
    _decode_optional_json_text,
    _decode_required_json_text,
    _decode_world_state_row,
    _drive_state_has_current_shape,
    _event_log_entry,
    _history_assistant_message,
    _history_user_message,
    _pending_input_cycle_context,
    _pending_input_receipt_summary,
    _pending_input_user_message_payload,
    _public_body_state_summary,
    _public_drive_state_summary,
    _public_emotion_summary,
    _public_persona_update,
    _public_primary_focus,
    _public_world_state_summary,
    _read_latest_retrieval_record,
    _runtime_response_summary,
    _task_cycle_context,
    _world_state_has_current_shape,
)
from otomekairo.infra.sqlite_store_vectors import (
    EMBEDDING_VECTOR_DIMENSION,
    _build_embedding_blob,
    _delete_vec_index_row,
    _mark_vec_item_unsearchable,
    _merge_ranked_event_rows,
    _merge_ranked_memory_rows,
    _replace_vec_index_row,
    _search_vec_similarity_hits,
    _upsert_vec_item_row,
)
from otomekairo.usecase.run_write_memory_job import (
    WriteMemoryJobExecutionState,
    run_write_memory_job,
)
from otomekairo.usecase.runtime_live_state import build_runtime_live_state
from otomekairo.usecase.about_time_text import about_years_from_text, life_stage_from_text
from otomekairo.usecase.write_memory_plan import (
    validate_write_memory_event_snapshots,
)


# Block: Schema constants
SCHEMA_NAME = "core_schema"
SCHEMA_VERSION = 18
STABLE_PREFERENCE_BUCKET_LIMIT = 8
RETRIEVAL_STABLE_PREFERENCE_BUCKET_LIMIT = 24
TIDY_MEMORY_SCOPES = (
    "completed_jobs_gc",
    "stale_preview_gc",
    "stale_vector_gc",
)
SETTINGS_EDITOR_PRESET_TABLE_NAMES = (
    "character_presets",
    "behavior_presets",
    "conversation_presets",
    "memory_presets",
    "motion_presets",
)
# Block: Bootstrap result
@dataclass(frozen=True, slots=True)
class BootstrapResult:
    db_path: Path
    initialized_at: int


# Block: Store implementation
class SqliteStateStore:
    def __init__(self, db_path: Path, initializer_version: str) -> None:
        self._db_path = db_path
        self._initializer_version = initializer_version

    # Block: Public bootstrap
    def initialize(self) -> BootstrapResult:
        now_ms = _now_ms()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            if not self._schema_exists(connection):
                connection.executescript(self._schema_sql())
            else:
                self._migrate_schema(connection, now_ms)
            self._normalize_bootstrap_tables(connection=connection)
            self._ensure_vec_index_schema(connection=connection)
            self._ensure_db_meta(connection, now_ms)
            self._ensure_settings_editor_schema_v12(
                connection=connection,
                now_ms=now_ms,
            )
            self._seed_singletons(connection, now_ms)
        return BootstrapResult(db_path=self._db_path, initialized_at=now_ms)

    # Block: Health snapshot
    def read_health(self) -> dict[str, Any]:
        return {"status": "ok", "server_time": _now_ms()}

    # Block: Status snapshot
    def read_status(self) -> dict[str, Any]:
        now_ms = _now_ms()
        with self._connect() as connection:
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
            retrieval_record = _read_latest_retrieval_record(connection)
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
            persona_revision_row = connection.execute(
                """
                SELECT before_json, after_json, reason, evidence_event_ids_json, created_at
                FROM revisions
                WHERE entity_type = 'self_state.personality'
                ORDER BY created_at DESC
                LIMIT 1
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
        if retrieval_record is not None:
            runtime_payload["last_retrieval"] = build_public_retrieval_summary(retrieval_record)
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
        if persona_revision_row is not None:
            self_state_payload["last_persona_update"] = _public_persona_update(persona_revision_row)
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

    # Block: Latest retrieval run read
    def read_latest_retrieval_run(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            retrieval_record = _read_latest_retrieval_record(connection)
        if retrieval_record is None:
            return None
        return build_public_retrieval_detail(retrieval_record)

    # Block: Recent retrieval runs read
    def read_recent_retrieval_runs(self, *, limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            raise StoreValidationError("retrieval limit must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT cycle_id, created_at, plan_json, candidates_json, selected_json, resolved_event_ids_json
                FROM retrieval_runs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            _retrieval_record_from_row(row)
            for row in rows
        ]

    # Block: Effective settings read
    def read_effective_settings(self, default_settings: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
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

    # Block: Settings snapshot
    def read_settings(self, default_settings: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT override_id, key, status, created_at
                FROM settings_overrides
                WHERE status IN ('queued', 'claimed')
                ORDER BY created_at ASC
                """
            ).fetchall()
        pending_overrides = [
            {
                "override_id": row["override_id"],
                "key": row["key"],
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        return {
            "effective_settings": self.read_effective_settings(default_settings),
            "pending_overrides": pending_overrides,
        }

    # Block: Tidy memory owner state read
    def read_tidy_memory_owner_state(
        self,
        *,
        completed_jobs_cutoff_at: int,
        stale_preview_cutoff_at: int,
        stale_vector_cutoff_at: int,
    ) -> dict[str, dict[str, Any]]:
        for cutoff_at, field_name in (
            (completed_jobs_cutoff_at, "completed_jobs_cutoff_at"),
            (stale_preview_cutoff_at, "stale_preview_cutoff_at"),
            (stale_vector_cutoff_at, "stale_vector_cutoff_at"),
        ):
            if not isinstance(cutoff_at, int):
                raise StoreValidationError(f"{field_name} must be integer")
            if cutoff_at <= 0:
                raise StoreValidationError(f"{field_name} must be positive")
        with self._connect() as connection:
            completed_jobs_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM memory_jobs
                    WHERE status IN ('completed', 'dead_letter')
                      AND completed_at IS NOT NULL
                      AND completed_at < ?
                    """,
                    (completed_jobs_cutoff_at,),
                ).fetchone()[0]
            )
            stale_preview_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM event_preview_cache
                    INNER JOIN events
                            ON events.event_id = event_preview_cache.event_id
                    WHERE events.searchable = 0
                      AND COALESCE(events.updated_at, events.created_at) < ?
                    """,
                    (stale_preview_cutoff_at,),
                ).fetchone()[0]
            )
            stale_vector_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM vec_items
                    WHERE searchable = 0
                      AND source_updated_at < ?
                    """,
                    (stale_vector_cutoff_at,),
                ).fetchone()[0]
            )
            housekeeping_rows = connection.execute(
                """
                SELECT
                    maintenance_scope,
                    last_enqueued_at
                FROM runtime_housekeeping_state
                WHERE maintenance_scope IN ('completed_jobs_gc', 'stale_preview_gc', 'stale_vector_gc')
                """
            ).fetchall()
            active_rows = connection.execute(
                """
                SELECT
                    json_extract(memory_job_payloads.payload_json, '$.maintenance_scope') AS maintenance_scope,
                    MAX(
                        CASE
                            WHEN memory_jobs.status IN ('queued', 'claimed') THEN 1
                            ELSE 0
                        END
                    ) AS has_active_job
                FROM memory_jobs
                INNER JOIN memory_job_payloads
                        ON memory_job_payloads.payload_id = json_extract(memory_jobs.payload_ref_json, '$.payload_id')
                WHERE memory_jobs.job_kind = 'tidy_memory'
                GROUP BY json_extract(memory_job_payloads.payload_json, '$.maintenance_scope')
                """
            ).fetchall()
        owner_state = {
            maintenance_scope: {
                "stale_count": (
                    completed_jobs_count
                    if maintenance_scope == "completed_jobs_gc"
                    else stale_preview_count
                    if maintenance_scope == "stale_preview_gc"
                    else stale_vector_count
                ),
                "last_enqueued_at": None,
                "has_active_job": False,
            }
            for maintenance_scope in TIDY_MEMORY_SCOPES
        }
        for row in housekeeping_rows:
            maintenance_scope = row["maintenance_scope"]
            if not isinstance(maintenance_scope, str) or maintenance_scope not in owner_state:
                continue
            last_enqueued_at = row["last_enqueued_at"]
            owner_state[maintenance_scope]["last_enqueued_at"] = (
                int(last_enqueued_at)
                if isinstance(last_enqueued_at, int)
                else None
            )
        for row in active_rows:
            maintenance_scope = row["maintenance_scope"]
            if not isinstance(maintenance_scope, str) or maintenance_scope not in owner_state:
                continue
            owner_state[maintenance_scope]["has_active_job"] = bool(row["has_active_job"])
        return owner_state

    # Block: Settings editor snapshot
    def read_settings_editor(self, default_settings: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
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
        character_presets = _decode_settings_preset_rows(character_rows)
        behavior_presets = _decode_settings_preset_rows(behavior_rows)
        conversation_presets = _decode_settings_preset_rows(conversation_rows)
        memory_presets = _decode_settings_preset_rows(memory_rows)
        motion_presets = _decode_settings_preset_rows(motion_rows)
        camera_connections = _decode_camera_connection_rows(camera_connection_rows)
        effective_settings = _materialize_effective_settings_from_editor(
            default_settings=default_settings,
            editor_state=editor_state,
            character_presets=character_presets,
            behavior_presets=behavior_presets,
            conversation_presets=conversation_presets,
            memory_presets=memory_presets,
            motion_presets=motion_presets,
        )
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
            "character_presets": character_presets,
            "behavior_presets": behavior_presets,
            "conversation_presets": conversation_presets,
            "memory_presets": memory_presets,
            "motion_presets": motion_presets,
            "camera_connections": camera_connections,
            "constraints": {
                "editable_system_keys": list(build_settings_editor_system_keys()),
            },
            "runtime_projection": {
                "effective_settings": effective_settings,
                "active_motion_preset": _active_preset_payload(
                    preset_entries=motion_presets,
                    preset_id=str(editor_state["active_motion_preset_id"]),
                ),
            },
        }

    # Block: Enabled camera connection snapshot
    def read_enabled_camera_connections(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
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

    # Block: Settings editor save
    def save_settings_editor(
        self,
        *,
        default_settings: dict[str, Any],
        document: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_document = normalize_settings_editor_document(document)
        now_ms = _now_ms()
        with self._connect() as connection:
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
            if editor_row is None:
                raise RuntimeError("settings_editor_state row is missing")
            current_editor_state = _decode_settings_editor_state_row(editor_row)
            current_revision = int(current_editor_state["revision"])
            requested_revision = int(normalized_document["editor_state"]["revision"])
            if requested_revision != current_revision:
                raise StoreConflictError(
                    "settings editor revision does not match",
                    error_code="settings_editor_revision_conflict",
                )
            current_camera_connection_rows = connection.execute(
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
            current_character_presets = _decode_settings_preset_rows(
                _fetch_editor_preset_rows(connection=connection, table_name="character_presets")
            )
            current_behavior_presets = _decode_settings_preset_rows(
                _fetch_editor_preset_rows(connection=connection, table_name="behavior_presets")
            )
            current_conversation_presets = _decode_settings_preset_rows(
                _fetch_editor_preset_rows(connection=connection, table_name="conversation_presets")
            )
            current_memory_presets = _decode_settings_preset_rows(
                _fetch_editor_preset_rows(connection=connection, table_name="memory_presets")
            )
            current_motion_presets = _decode_settings_preset_rows(
                _fetch_editor_preset_rows(connection=connection, table_name="motion_presets")
            )
            current_camera_connections = _decode_camera_connection_rows(current_camera_connection_rows)
            if (
                _canonical_editor_state_for_compare(current_editor_state)
                == normalized_document["editor_state"]
                and current_character_presets == normalized_document["character_presets"]
                and current_behavior_presets == normalized_document["behavior_presets"]
                and current_conversation_presets == normalized_document["conversation_presets"]
                and current_memory_presets == normalized_document["memory_presets"]
                and current_motion_presets == normalized_document["motion_presets"]
                and current_camera_connections == normalized_document["camera_connections"]
            ):
                return self.read_settings_editor(default_settings)
            saved_editor_state = {
                "active_character_preset_id": normalized_document["editor_state"]["active_character_preset_id"],
                "active_behavior_preset_id": normalized_document["editor_state"]["active_behavior_preset_id"],
                "active_conversation_preset_id": normalized_document["editor_state"]["active_conversation_preset_id"],
                "active_memory_preset_id": normalized_document["editor_state"]["active_memory_preset_id"],
                "active_motion_preset_id": normalized_document["editor_state"]["active_motion_preset_id"],
                "system_values": dict(normalized_document["editor_state"]["system_values"]),
                "revision": current_revision + 1,
                "updated_at": now_ms,
            }
            _persist_settings_editor_state(connection=connection, editor_state=saved_editor_state)
            _replace_editor_preset_rows(
                connection=connection,
                table_name="character_presets",
                preset_entries=normalized_document["character_presets"],
                now_ms=now_ms,
            )
            _replace_editor_preset_rows(
                connection=connection,
                table_name="behavior_presets",
                preset_entries=normalized_document["behavior_presets"],
                now_ms=now_ms,
            )
            _replace_editor_preset_rows(
                connection=connection,
                table_name="conversation_presets",
                preset_entries=normalized_document["conversation_presets"],
                now_ms=now_ms,
            )
            _replace_editor_preset_rows(
                connection=connection,
                table_name="memory_presets",
                preset_entries=normalized_document["memory_presets"],
                now_ms=now_ms,
            )
            _replace_editor_preset_rows(
                connection=connection,
                table_name="motion_presets",
                preset_entries=normalized_document["motion_presets"],
                now_ms=now_ms,
            )
            _replace_camera_connections(
                connection=connection,
                camera_connections=normalized_document["camera_connections"],
                now_ms=now_ms,
            )
            change_set_id = _opaque_id("setchg")
            _insert_settings_change_set(
                connection=connection,
                change_set_id=change_set_id,
                editor_state=saved_editor_state,
                character_presets=normalized_document["character_presets"],
                behavior_presets=normalized_document["behavior_presets"],
                conversation_presets=normalized_document["conversation_presets"],
                memory_presets=normalized_document["memory_presets"],
                motion_presets=normalized_document["motion_presets"],
                now_ms=now_ms,
            )
        return self.read_settings_editor(default_settings)

    # Block: Cognition snapshot
    def read_cognition_state(
        self,
        default_settings: dict[str, Any],
        *,
        observation_hint_text: str | None = None,
    ) -> CognitionStateSnapshot:
        with self._connect() as connection:
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
            persona_revision_row = connection.execute(
                """
                SELECT before_json, after_json, reason, evidence_event_ids_json, created_at
                FROM revisions
                WHERE entity_type = 'self_state.personality'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
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
        with self._connect() as connection:
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
                    events.created_at,
                    event_preview_cache.preview_text
                FROM events
                LEFT JOIN event_preview_cache
                       ON event_preview_cache.event_id = events.event_id
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
                **(
                    {"latest_persona_update": _public_persona_update(persona_revision_row)}
                    if persona_revision_row is not None
                    else {}
                ),
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

    # Block: Settings claim
    def claim_next_settings_override(self) -> SettingsOverrideRecord | None:
        now_ms = _now_ms()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT override_id, key, requested_value_json, apply_scope, created_at
                FROM settings_overrides
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE settings_overrides
                SET status = 'claimed',
                    claimed_at = ?
                WHERE override_id = ?
                  AND status = 'queued'
                """,
                (now_ms, row["override_id"]),
            )
        return SettingsOverrideRecord(
            override_id=row["override_id"],
            key=row["key"],
            requested_value_json=json.loads(row["requested_value_json"]),
            apply_scope=row["apply_scope"],
            created_at=int(row["created_at"]),
        )

    # Block: Settings input journal append
    def append_input_journal_for_settings_override(
        self,
        *,
        settings_override: SettingsOverrideRecord,
        cycle_id: str,
    ) -> None:
        self._append_input_journal(
            observation_id=f"obs_{settings_override.override_id}",
            cycle_id=cycle_id,
            source="web_settings",
            kind="settings_override",
            captured_at=settings_override.created_at,
            receipt_summary=(
                f"settings override {settings_override.key} "
                f"({settings_override.apply_scope})"
            ),
            payload_id=settings_override.override_id,
        )

    # Block: Settings finalize
    def finalize_settings_override(
        self,
        *,
        override_id: str,
        key: str,
        requested_value_json: dict[str, Any],
        apply_scope: str,
        cycle_id: str,
        final_status: str,
        reject_reason: str | None = None,
        camera_available: bool,
    ) -> int:
        if final_status not in {"applied", "rejected"}:
            raise StoreValidationError("final_status is invalid")
        resolved_at = _now_ms()
        with self._connect() as connection:
            if final_status == "applied" and apply_scope == "runtime":
                applied_value = decode_requested_value(key, requested_value_json)
                _upsert_runtime_setting_value(
                    connection=connection,
                    key=key,
                    value=applied_value,
                    applied_at=resolved_at,
                )
                self._sync_runtime_live_state(
                    connection=connection,
                    camera_available=camera_available,
                    updated_at=resolved_at,
                    cycle_context=None,
                )
            event_ids = self._insert_settings_override_events(
                connection=connection,
                override_id=override_id,
                cycle_id=cycle_id,
                key=key,
                apply_scope=apply_scope,
                final_status=final_status,
                reject_reason=reject_reason,
                resolved_at=resolved_at,
            )
            enqueued_memory_job_ids = self._enqueue_write_memory_jobs(
                connection=connection,
                cycle_id=cycle_id,
                event_ids=event_ids,
                created_at=resolved_at,
            )
            updated_row_count = connection.execute(
                """
                UPDATE settings_overrides
                SET status = ?,
                    resolved_at = ?,
                    reject_reason = ?
                WHERE override_id = ?
                  AND status = 'claimed'
                """,
                (final_status, resolved_at, reject_reason, override_id),
            ).rowcount
            if updated_row_count != 1:
                raise StoreConflictError("settings override must be claimed before finalization")
            connection.execute(
                """
                INSERT INTO commit_records (
                    cycle_id,
                    committed_at,
                    log_sync_status,
                    commit_payload_json
                )
                VALUES (?, ?, 'pending', ?)
                """,
                (
                    cycle_id,
                    resolved_at,
                    _json_text(
                        {
                            "cycle_kind": "short",
                            "trigger_reason": "external_input",
                            "processed_override_id": override_id,
                            "settings_key": key,
                            "apply_scope": apply_scope,
                            "resolution_status": final_status,
                            "event_ids": event_ids,
                            "enqueued_memory_job_ids": enqueued_memory_job_ids,
                        }
                    ),
                ),
            )
            commit_row = connection.execute(
                """
                SELECT commit_id
                FROM commit_records
                WHERE cycle_id = ?
                """,
                (cycle_id,),
            ).fetchone()
        if commit_row is None:
            raise RuntimeError("commit_records insert did not persist")
        commit_id = int(commit_row["commit_id"])
        self.sync_commit_log(commit_id=commit_id)
        return commit_id

    # Block: Settings change set claim
    def claim_next_settings_change_set(self) -> SettingsChangeSetRecord | None:
        now_ms = _now_ms()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT change_set_id, editor_revision, payload_json, created_at
                FROM settings_change_sets
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE settings_change_sets
                SET status = 'claimed',
                    claimed_at = ?
                WHERE change_set_id = ?
                  AND status = 'queued'
                """,
                (now_ms, row["change_set_id"]),
            )
        return SettingsChangeSetRecord(
            change_set_id=str(row["change_set_id"]),
            editor_revision=int(row["editor_revision"]),
            payload_json=json.loads(row["payload_json"]),
            created_at=int(row["created_at"]),
        )

    # Block: Settings change set finalize
    def finalize_settings_change_set(
        self,
        *,
        change_set: SettingsChangeSetRecord,
        default_settings: dict[str, Any],
        final_status: str,
        reject_reason: str | None = None,
        camera_available: bool,
    ) -> None:
        if final_status not in {"applied", "rejected"}:
            raise StoreValidationError("settings change set final_status is invalid")
        resolved_at = _now_ms()
        with self._connect() as connection:
            if final_status == "applied":
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
                if editor_row is None:
                    raise RuntimeError("settings_editor_state row is missing")
                editor_state = _decode_settings_editor_state_row(editor_row)
                if int(editor_state["revision"]) != change_set.editor_revision:
                    final_status = "rejected"
                    reject_reason = "stale_settings_change_set"
                else:
                    character_presets = _decode_settings_preset_rows(
                        _fetch_editor_preset_rows(connection=connection, table_name="character_presets")
                    )
                    behavior_presets = _decode_settings_preset_rows(
                        _fetch_editor_preset_rows(connection=connection, table_name="behavior_presets")
                    )
                    conversation_presets = _decode_settings_preset_rows(
                        _fetch_editor_preset_rows(connection=connection, table_name="conversation_presets")
                    )
                    memory_presets = _decode_settings_preset_rows(
                        _fetch_editor_preset_rows(connection=connection, table_name="memory_presets")
                    )
                    motion_presets = _decode_settings_preset_rows(
                        _fetch_editor_preset_rows(connection=connection, table_name="motion_presets")
                    )
                    runtime_values = _materialize_effective_settings_from_editor(
                        default_settings=default_settings,
                        editor_state=editor_state,
                        character_presets=character_presets,
                        behavior_presets=behavior_presets,
                        conversation_presets=conversation_presets,
                        memory_presets=memory_presets,
                        motion_presets=motion_presets,
                    )
                    connection.execute(
                        """
                        UPDATE runtime_settings
                        SET values_json = ?,
                            value_updated_at_json = ?,
                            updated_at = ?
                        WHERE row_id = 1
                        """,
                        (
                            _json_text(runtime_values),
                            _json_text({key: resolved_at for key in runtime_values}),
                            resolved_at,
                        ),
                    )
                    self._sync_runtime_live_state(
                        connection=connection,
                        camera_available=camera_available,
                        updated_at=resolved_at,
                        cycle_context=None,
                    )
            updated_row_count = connection.execute(
                """
                UPDATE settings_change_sets
                SET status = ?,
                    resolved_at = ?,
                    reject_reason = ?
                WHERE change_set_id = ?
                  AND status = 'claimed'
                """,
                (final_status, resolved_at, reject_reason, change_set.change_set_id),
            ).rowcount
            if updated_row_count != 1:
                raise StoreConflictError("settings change set must be claimed before finalization")

    # Block: Next boot materialization
    def materialize_next_boot_settings(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT key, requested_value_json, resolved_at
                FROM settings_overrides
                WHERE status = 'applied'
                  AND apply_scope = 'next_boot'
                  AND resolved_at IS NOT NULL
                ORDER BY resolved_at ASC
                """
            ).fetchall()
            if not rows:
                return
            runtime_row = connection.execute(
                """
                SELECT values_json, value_updated_at_json
                FROM runtime_settings
                WHERE row_id = 1
                """
            ).fetchone()
            if runtime_row is None:
                raise RuntimeError("runtime_settings row is missing")
            values = json.loads(runtime_row["values_json"])
            value_updated_at = json.loads(runtime_row["value_updated_at_json"])
            changed = False
            for row in rows:
                key = row["key"]
                current_key_updated_at = int(value_updated_at.get(key, 0))
                resolved_at = int(row["resolved_at"])
                if resolved_at <= current_key_updated_at:
                    continue
                requested_value_json = json.loads(row["requested_value_json"])
                values[key] = decode_requested_value(key, requested_value_json)
                value_updated_at[key] = resolved_at
                changed = True
            if not changed:
                return
            connection.execute(
                """
                UPDATE runtime_settings
                SET values_json = ?,
                    value_updated_at_json = ?,
                    updated_at = ?
                WHERE row_id = 1
                """,
                (
                    _json_text(values),
                    _json_text(value_updated_at),
                    _now_ms(),
                ),
            )

    # Block: Settings write
    def enqueue_settings_override(
        self,
        *,
        key: str,
        requested_value_json: dict[str, Any],
        apply_scope: str,
    ) -> dict[str, Any]:
        override_id = _opaque_id("ovr")
        now_ms = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO settings_overrides (
                    override_id,
                    key,
                    requested_value_json,
                    apply_scope,
                    created_at,
                    status
                )
                VALUES (?, ?, ?, ?, ?, 'queued')
                """,
                (
                    override_id,
                    key,
                    json.dumps(requested_value_json, ensure_ascii=True, separators=(",", ":")),
                    apply_scope,
                    now_ms,
                ),
            )
        return {"accepted": True, "override_id": override_id, "status": "queued"}

    # Block: Chat input write
    def enqueue_chat_message(
        self,
        *,
        text: str | None,
        client_message_id: str | None,
        attachments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        stripped_text = text.strip() if isinstance(text, str) else ""
        if len(stripped_text) > 4000:
            raise StoreValidationError("text is too long")
        if not stripped_text and not attachments:
            raise StoreValidationError("text or attachments must be provided")
        payload: dict[str, Any] = {
            "input_kind": "chat_message",
            "message_kind": "dialogue_turn",
            "trigger_reason": "external_input",
        }
        if stripped_text:
            payload["text"] = stripped_text
        if attachments:
            payload["attachments"] = attachments
        if client_message_id:
            payload["client_message_id"] = client_message_id
        return self._enqueue_pending_input(
            source="web_input",
            client_message_id=client_message_id,
            payload=payload,
            priority=100,
            emit_user_message_event=True,
        )

    # Block: Microphone message write
    def enqueue_microphone_message(
        self,
        *,
        transcript_text: str,
        stt_provider: str,
        stt_language: str,
    ) -> dict[str, Any]:
        stripped_text = transcript_text.strip()
        stripped_provider = stt_provider.strip()
        stripped_language = stt_language.strip()
        if not stripped_text:
            raise StoreValidationError("transcript_text must be non-empty")
        if len(stripped_text) > 4000:
            raise StoreValidationError("transcript_text is too long")
        if not stripped_provider:
            raise StoreValidationError("stt_provider must be non-empty")
        if not stripped_language:
            raise StoreValidationError("stt_language must be non-empty")
        return self._enqueue_pending_input(
            source="microphone",
            client_message_id=None,
            payload={
                "input_kind": "microphone_message",
                "message_kind": "dialogue_turn",
                "trigger_reason": "external_input",
                "text": stripped_text,
                "stt_provider": stripped_provider,
                "stt_language": stripped_language,
            },
            priority=100,
            emit_user_message_event=True,
        )

    # Block: Camera observation write
    def enqueue_camera_observation(
        self,
        *,
        camera_connection_id: str,
        camera_display_name: str,
        capture_id: str,
        image_path: str,
        image_url: str,
        captured_at: int,
    ) -> dict[str, Any]:
        if not isinstance(camera_connection_id, str) or not camera_connection_id:
            raise StoreValidationError("camera_connection_id must be non-empty string")
        if not isinstance(camera_display_name, str) or not camera_display_name:
            raise StoreValidationError("camera_display_name must be non-empty string")
        if not isinstance(capture_id, str) or not capture_id:
            raise StoreValidationError("capture_id must be non-empty string")
        if not isinstance(image_path, str) or not image_path:
            raise StoreValidationError("image_path must be non-empty string")
        if not isinstance(image_url, str) or not image_url:
            raise StoreValidationError("image_url must be non-empty string")
        if isinstance(captured_at, bool) or not isinstance(captured_at, int):
            raise StoreValidationError("captured_at must be integer")
        payload = build_camera_observation_payload(
            camera_connection_id=camera_connection_id,
            camera_display_name=camera_display_name,
            capture_id=capture_id,
            image_path=image_path,
            image_url=image_url,
            captured_at=captured_at,
            trigger_reason="self_initiated",
        )
        enqueue_result = self._enqueue_pending_input(
            source="camera",
            client_message_id=None,
            payload=payload,
            priority=80,
        )
        return {
            **enqueue_result,
            "camera_connection_id": camera_connection_id,
            "camera_display_name": camera_display_name,
            "capture_id": capture_id,
            "image_path": image_path,
            "image_url": image_url,
            "captured_at": captured_at,
        }

    # Block: Idle tick write
    def enqueue_idle_tick(
        self,
        *,
        idle_duration_ms: int,
    ) -> dict[str, Any]:
        if isinstance(idle_duration_ms, bool) or not isinstance(idle_duration_ms, int):
            raise StoreValidationError("idle_duration_ms must be integer")
        if idle_duration_ms <= 0:
            raise StoreValidationError("idle_duration_ms must be positive")
        return self._enqueue_pending_input(
            source="idle_tick",
            client_message_id=None,
            payload={
                "input_kind": "idle_tick",
                "trigger_reason": "idle_tick",
                "idle_duration_ms": idle_duration_ms,
            },
            priority=10,
        )

    # Block: Pending input write
    def _enqueue_pending_input(
        self,
        *,
        source: str,
        client_message_id: str | None,
        payload: dict[str, Any],
        priority: int,
        emit_user_message_event: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(source, str) or not source:
            raise StoreValidationError("source must be non-empty string")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise StoreValidationError("priority must be integer")
        input_id = _opaque_id("inp")
        now_ms = _now_ms()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO pending_inputs (
                        input_id,
                        source,
                        channel,
                        client_message_id,
                        payload_json,
                        created_at,
                        priority,
                        status
                    )
                    VALUES (?, ?, 'browser_chat', ?, ?, ?, ?, 'queued')
                    """,
                    (
                        input_id,
                        source,
                        client_message_id,
                        json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
                        now_ms,
                        priority,
                    ),
                )
                if emit_user_message_event:
                    self._insert_ui_outbound_event(
                        connection=connection,
                        channel="browser_chat",
                        event_type="message",
                        payload=_pending_input_user_message_payload(
                            input_id=input_id,
                            created_at=now_ms,
                            payload=payload,
                        ),
                        source_cycle_id=None,
                        created_at=now_ms,
                    )
        except sqlite3.IntegrityError as error:
            raise StoreConflictError(
                "既に受け付けた入力です",
                error_code="duplicate_client_message_id",
            ) from error
        return {
            "accepted": True,
            "input_id": input_id,
            "status": "queued",
            "channel": "browser_chat",
        }

    # Block: Cancel write
    def enqueue_cancel(self, *, target_message_id: str | None) -> dict[str, Any]:
        input_id = _opaque_id("inp")
        now_ms = _now_ms()
        payload: dict[str, Any] = {
            "input_kind": "cancel",
            "trigger_reason": "external_input",
        }
        if target_message_id:
            payload["target_message_id"] = target_message_id
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO pending_inputs (
                    input_id,
                    source,
                    channel,
                    client_message_id,
                    payload_json,
                    created_at,
                    priority,
                    status
                )
                VALUES (?, 'web_input', 'browser_chat', NULL, ?, ?, ?, 'queued')
                """,
                (
                    input_id,
                    json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
                    now_ms,
                    100,
                ),
            )
        return {"accepted": True, "status": "queued"}

    # Block: Quarantine enqueue
    def enqueue_quarantine_memory(
        self,
        *,
        source_event_ids: list[str],
        targets: list[dict[str, Any]],
        reason_code: str,
        reason_note: str,
    ) -> dict[str, Any]:
        if not source_event_ids:
            raise StoreValidationError("source_event_ids must not be empty")
        if not isinstance(reason_code, str) or not reason_code:
            raise StoreValidationError("reason_code must be non-empty string")
        if not isinstance(reason_note, str) or not reason_note:
            raise StoreValidationError("reason_note must be non-empty string")
        normalized_targets = _normalize_quarantine_targets(targets)
        cycle_id = _opaque_id("cycle")
        created_at = _now_ms()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job_ids = self._enqueue_quarantine_memory_jobs(
                connection=connection,
                cycle_id=cycle_id,
                source_event_ids=source_event_ids,
                targets=normalized_targets,
                reason_code=reason_code,
                reason_note=reason_note,
                created_at=created_at,
            )
        return {
            "accepted": True,
            "cycle_id": cycle_id,
            "job_ids": job_ids,
            "status": "queued",
        }

    # Block: Tidy memory enqueue
    def enqueue_tidy_memory(
        self,
        *,
        maintenance_scope: str,
        retention_cutoff_at: int,
        target_refs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(maintenance_scope, str) or not maintenance_scope:
            raise StoreValidationError("maintenance_scope must be non-empty string")
        if maintenance_scope not in TIDY_MEMORY_SCOPES:
            raise StoreValidationError("maintenance_scope is invalid")
        if isinstance(retention_cutoff_at, bool) or not isinstance(retention_cutoff_at, int):
            raise StoreValidationError("retention_cutoff_at must be integer")
        if retention_cutoff_at <= 0:
            raise StoreValidationError("retention_cutoff_at must be positive")
        normalized_target_refs = None
        if target_refs is not None:
            normalized_target_refs = _normalize_tidy_target_refs(target_refs)
        if maintenance_scope == "completed_jobs_gc" and normalized_target_refs is not None:
            raise StoreValidationError("target_refs is not allowed for completed_jobs_gc")
        if maintenance_scope == "stale_preview_gc" and normalized_target_refs is not None:
            for target_ref in normalized_target_refs:
                if target_ref["entity_type"] != "event":
                    raise StoreValidationError("stale_preview_gc target_refs must be event")
        if maintenance_scope == "stale_vector_gc" and normalized_target_refs is not None:
            for target_ref in normalized_target_refs:
                if target_ref["entity_type"] not in {"event", "memory_state", "event_affect"}:
                    raise StoreValidationError("stale_vector_gc target_refs is invalid")
        cycle_id = _opaque_id("cycle")
        created_at = _now_ms()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job_ids = self._enqueue_tidy_memory_jobs(
                connection=connection,
                cycle_id=cycle_id,
                maintenance_scope=maintenance_scope,
                retention_cutoff_at=retention_cutoff_at,
                target_refs=normalized_target_refs,
                created_at=created_at,
            )
        return {
            "accepted": True,
            "cycle_id": cycle_id,
            "job_ids": job_ids,
            "status": "queued",
        }

    # Block: Stream window read
    def read_stream_window(self, *, channel: str) -> tuple[int | None, int | None]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT MIN(ui_event_id) AS min_id, MAX(ui_event_id) AS max_id
                FROM ui_outbound_events
                WHERE channel = ?
                """,
                (channel,),
            ).fetchone()
        if row is None:
            return (None, None)
        return (row["min_id"], row["max_id"])

    # Block: Chat history read
    def read_chat_history(self, *, channel: str, limit: int = 200) -> dict[str, Any]:
        if not isinstance(channel, str) or not channel:
            raise StoreValidationError("channel must be non-empty string")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise StoreValidationError("limit must be integer")
        if limit <= 0 or limit > 500:
            raise StoreValidationError("limit must be within 1..500")
        with self._connect() as connection:
            user_rows = connection.execute(
                """
                SELECT input_id, created_at, payload_json
                FROM pending_inputs
                WHERE channel = ?
                  AND json_extract(payload_json, '$.input_kind') IN ('chat_message', 'microphone_message')
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (channel, limit),
            ).fetchall()
            assistant_rows = connection.execute(
                """
                SELECT result_id, finished_at, command_json, observed_effects_json
                FROM action_history
                WHERE json_extract(observed_effects_json, '$.final_message_emitted') = 1
                  AND json_type(command_json, '$.parameters.text') = 'text'
                ORDER BY finished_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            stream_window_row = connection.execute(
                """
                SELECT MAX(ui_event_id) AS max_id
                FROM ui_outbound_events
                WHERE channel = ?
                """,
                (channel,),
            ).fetchone()
        messages: list[dict[str, Any]] = []
        for row in user_rows:
            payload = _decode_required_json_text(
                raw_value=row["payload_json"],
                field_name="pending_inputs.payload_json",
            )
            messages.append(
                _history_user_message(
                    input_id=str(row["input_id"]),
                    created_at=int(row["created_at"]),
                    payload=payload,
                )
            )
        for row in assistant_rows:
            command_json = _decode_required_json_text(
                raw_value=row["command_json"],
                field_name="action_history.command_json",
            )
            observed_effects_json = _decode_optional_json_text(
                raw_value=row["observed_effects_json"],
                field_name="action_history.observed_effects_json",
            )
            history_message = _history_assistant_message(
                finished_at=int(row["finished_at"]),
                command_json=command_json,
                observed_effects_json=observed_effects_json,
            )
            if history_message is not None:
                messages.append(history_message)
        messages.sort(key=lambda item: (int(item["created_at"]), str(item["message_id"])))
        if len(messages) > limit:
            messages = messages[-limit:]
        stream_cursor = None
        if stream_window_row is not None and stream_window_row["max_id"] is not None:
            stream_cursor = int(stream_window_row["max_id"])
        return {
            "channel": channel,
            "messages": messages,
            "stream_cursor": stream_cursor,
        }

    # Block: Runtime work state read
    def read_runtime_work_state(self) -> dict[str, bool]:
        with self._connect() as connection:
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

    # Block: Stream retention prune
    def prune_ui_outbound_events(
        self,
        *,
        channel: str,
        retention_window_ms: int,
        retain_minimum_count: int,
    ) -> int:
        if not isinstance(channel, str) or not channel:
            raise StoreValidationError("channel must be non-empty string")
        if retention_window_ms <= 0:
            raise StoreValidationError("retention_window_ms must be positive")
        if retain_minimum_count <= 0:
            raise StoreValidationError("retain_minimum_count must be positive")
        now_ms = _now_ms()
        created_cutoff_at = now_ms - retention_window_ms
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest_row = connection.execute(
                """
                SELECT MAX(ui_event_id) AS latest_ui_event_id
                FROM ui_outbound_events
                WHERE channel = ?
                """,
                (channel,),
            ).fetchone()
            if latest_row is None or latest_row["latest_ui_event_id"] is None:
                return 0
            latest_ui_event_id = int(latest_row["latest_ui_event_id"])
            id_cutoff = latest_ui_event_id - retain_minimum_count
            if id_cutoff <= 0:
                return 0
            deleted_row_count = connection.execute(
                """
                DELETE FROM ui_outbound_events
                WHERE channel = ?
                  AND created_at < ?
                  AND ui_event_id < ?
                """,
                (
                    channel,
                    created_cutoff_at,
                    id_cutoff,
                ),
            ).rowcount
        return int(deleted_row_count)

    # Block: Stream event read
    def read_ui_events(self, *, channel: str, after_event_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT ui_event_id, event_type, payload_json
                FROM ui_outbound_events
                WHERE channel = ?
                  AND ui_event_id > ?
                ORDER BY ui_event_id ASC
                LIMIT ?
                """,
                (channel, after_event_id, limit),
            ).fetchall()
        return [
            {
                "ui_event_id": row["ui_event_id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    # Block: Stream event append
    def append_ui_outbound_event(
        self,
        *,
        channel: str,
        event_type: str,
        payload: dict[str, Any],
        source_cycle_id: str,
    ) -> int:
        created_at = _now_ms()
        with self._connect() as connection:
            return self._insert_ui_outbound_event(
                connection=connection,
                channel=channel,
                event_type=event_type,
                payload=payload,
                source_cycle_id=source_cycle_id,
                created_at=created_at,
            )

    # Block: Stream event insert
    def _insert_ui_outbound_event(
        self,
        *,
        connection: sqlite3.Connection,
        channel: str,
        event_type: str,
        payload: dict[str, Any],
        source_cycle_id: str | None,
        created_at: int,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO ui_outbound_events (
                channel,
                event_type,
                payload_json,
                created_at,
                source_cycle_id
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                channel,
                event_type,
                _json_text(payload),
                created_at,
                source_cycle_id,
            ),
        )
        return int(cursor.lastrowid)

    # Block: Runtime lease
    def acquire_runtime_lease(self, *, owner_token: str, lease_ttl_ms: int) -> None:
        if lease_ttl_ms <= 0:
            raise StoreValidationError("lease_ttl_ms must be positive")
        now_ms = _now_ms()
        expires_at = now_ms + lease_ttl_ms
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT owner_token, expires_at
                FROM runtime_leases
                WHERE lease_name = 'primary_runtime'
                """
            ).fetchone()
            if row is not None and row["owner_token"] != owner_token and row["expires_at"] >= now_ms:
                raise StoreConflictError("primary runtime lease is already held")
            connection.execute(
                """
                INSERT INTO runtime_leases (
                    lease_name,
                    owner_token,
                    acquired_at,
                    heartbeat_at,
                    expires_at
                )
                VALUES ('primary_runtime', ?, ?, ?, ?)
                ON CONFLICT(lease_name) DO UPDATE SET
                    owner_token = excluded.owner_token,
                    acquired_at = CASE
                        WHEN runtime_leases.owner_token = excluded.owner_token
                            THEN runtime_leases.acquired_at
                        ELSE excluded.acquired_at
                    END,
                    heartbeat_at = excluded.heartbeat_at,
                    expires_at = excluded.expires_at
                """,
                (owner_token, now_ms, now_ms, expires_at),
            )

    # Block: Matching cancel claim
    def claim_matching_cancel_input(
        self,
        *,
        channel: str,
        target_message_id: str,
    ) -> PendingInputRecord | None:
        now_ms = _now_ms()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT input_id, source, channel, payload_json, created_at
                FROM pending_inputs
                WHERE status = 'queued'
                  AND channel = ?
                  AND json_extract(payload_json, '$.input_kind') = 'cancel'
                  AND (
                        json_extract(payload_json, '$.target_message_id') IS NULL
                        OR json_extract(payload_json, '$.target_message_id') = ?
                  )
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """,
                (channel, target_message_id),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE pending_inputs
                SET status = 'claimed',
                    claimed_at = ?
                WHERE input_id = ?
                  AND status = 'queued'
                """,
                (now_ms, row["input_id"]),
            )
        return PendingInputRecord(
            input_id=row["input_id"],
            source=row["source"],
            channel=row["channel"],
            created_at=int(row["created_at"]),
            payload=json.loads(row["payload_json"]),
        )

    # Block: Runtime lease release
    def release_runtime_lease(self, *, owner_token: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM runtime_leases
                WHERE lease_name = 'primary_runtime'
                  AND owner_token = ?
                """,
                (owner_token,),
            )

    # Block: Memory job claim
    def claim_next_memory_job(self) -> MemoryJobRecord | None:
        now_ms = _now_ms()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT
                    memory_jobs.job_id,
                    memory_jobs.job_kind,
                    memory_jobs.tries,
                    memory_jobs.created_at,
                    memory_jobs.payload_ref_json
                FROM memory_jobs
                WHERE memory_jobs.status = 'queued'
                ORDER BY memory_jobs.created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE memory_jobs
                SET status = 'claimed',
                    tries = tries + 1,
                    claimed_at = ?,
                    updated_at = ?
                WHERE job_id = ?
                  AND status = 'queued'
                """,
                (now_ms, now_ms, row["job_id"]),
            )
            job_id = str(row["job_id"])
            job_kind = str(row["job_kind"])
            try:
                payload_ref = _resolve_memory_job_payload_ref(row["payload_ref_json"])
                payload_row = connection.execute(
                    """
                    SELECT job_kind, payload_json
                    FROM memory_job_payloads
                    WHERE payload_id = ?
                    """,
                    (payload_ref["payload_id"],),
                ).fetchone()
                if payload_row is None:
                    self._dead_letter_claimed_memory_job(
                        connection=connection,
                        job_id=job_id,
                        dead_lettered_at=now_ms,
                        last_error=f"missing memory_job_payloads row for payload_id={payload_ref['payload_id']}",
                    )
                    return None
                payload = json.loads(payload_row["payload_json"])
                if not isinstance(payload, dict):
                    raise RuntimeError("memory_job_payloads.payload_json must be object")
                if str(payload_row["job_kind"]) != job_kind:
                    raise RuntimeError("memory_job_payloads.job_kind must match memory_jobs.job_kind")
                if str(payload.get("job_kind")) != job_kind:
                    raise RuntimeError("memory_job_payloads.payload_json.job_kind must match memory_jobs.job_kind")
                return MemoryJobRecord(
                    job_id=job_id,
                    job_kind=job_kind,
                    tries=int(row["tries"]),
                    created_at=int(row["created_at"]),
                    payload=payload,
                )
            except Exception as error:
                self._dead_letter_claimed_memory_job(
                    connection=connection,
                    job_id=job_id,
                    dead_lettered_at=now_ms,
                    last_error=_memory_job_error_text(error),
                )
                return None

    # Block: Memory job failure
    def fail_claimed_memory_job(
        self,
        *,
        memory_job: MemoryJobRecord,
        error: Exception,
        max_tries: int,
    ) -> str:
        if max_tries <= 0:
            raise StoreValidationError("max_tries must be positive")
        failed_at = _now_ms()
        error_text = _memory_job_error_text(error)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job_row = connection.execute(
                """
                SELECT tries, status
                FROM memory_jobs
                WHERE job_id = ?
                """,
                (memory_job.job_id,),
            ).fetchone()
            if job_row is None:
                raise RuntimeError("memory job is missing")
            if job_row["status"] != "claimed":
                raise StoreConflictError("memory job must be claimed before failure handling")
            tries = int(job_row["tries"])
            next_status = "dead_letter" if tries >= max_tries else "queued"
            completed_at = failed_at if next_status == "dead_letter" else None
            connection.execute(
                """
                UPDATE memory_jobs
                SET status = ?,
                    updated_at = ?,
                    claimed_at = CASE
                        WHEN ? = 'queued' THEN NULL
                        ELSE claimed_at
                    END,
                    completed_at = ?,
                    last_error = ?
                WHERE job_id = ?
                  AND status = 'claimed'
                """,
                (
                    next_status,
                    failed_at,
                    next_status,
                    completed_at,
                    error_text,
                    memory_job.job_id,
                ),
            )
        return next_status

    # Block: Dead letter handling
    def _dead_letter_claimed_memory_job(
        self,
        *,
        connection: sqlite3.Connection,
        job_id: str,
        dead_lettered_at: int,
        last_error: str,
    ) -> None:
        updated_row_count = connection.execute(
            """
            UPDATE memory_jobs
            SET status = 'dead_letter',
                updated_at = ?,
                completed_at = ?,
                last_error = ?
            WHERE job_id = ?
              AND status = 'claimed'
            """,
            (
                dead_lettered_at,
                dead_lettered_at,
                last_error,
                job_id,
            ),
        ).rowcount
        if updated_row_count != 1:
            raise StoreConflictError("memory job must be claimed before dead letter handling")

    # Block: Memory job apply
    def complete_write_memory_job(self, *, memory_job: MemoryJobRecord) -> str:
        now_ms = _now_ms()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return run_write_memory_job(
                connection=connection,
                store=self,
                memory_job=memory_job,
                now_ms=now_ms,
            )

    # Block: Claimed memory job ensure
    def ensure_claimed_memory_job_in_transaction(
        self,
        *,
        connection: sqlite3.Connection,
        job_id: str,
    ) -> None:
        self._ensure_claimed_memory_job(
            connection=connection,
            job_id=job_id,
        )

    # Block: Write memory execution state load
    def load_write_memory_job_execution_state(
        self,
        *,
        connection: sqlite3.Connection,
        memory_job: MemoryJobRecord,
        validated_payload: dict[str, Any],
    ) -> WriteMemoryJobExecutionState:
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
            SELECT personality_json,
                   personality_updated_at,
                   current_emotion_json
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
            current_personality=_decoded_object_json(self_state_row["personality_json"]),
            current_personality_updated_at=int(self_state_row["personality_updated_at"]),
        )

    # Block: Write memory plan apply wrapper
    def apply_write_memory_plan_in_transaction(
        self,
        *,
        connection: sqlite3.Connection,
        memory_write_plan: dict[str, Any],
        created_at: int,
    ) -> dict[str, list[dict[str, Any]]]:
        return self._apply_write_memory_plan(
            connection=connection,
            memory_write_plan=memory_write_plan,
            created_at=created_at,
        )

    # Block: Persona update apply wrapper
    def apply_persona_updates_in_transaction(
        self,
        *,
        connection: sqlite3.Connection,
        current_personality: dict[str, Any],
        updated_personality: dict[str, Any],
        persona_updates: dict[str, Any],
        updated_at: int,
    ) -> bool:
        return self._apply_persona_updates(
            connection=connection,
            current_personality=current_personality,
            updated_personality=updated_personality,
            persona_updates=persona_updates,
            updated_at=updated_at,
        )

    # Block: Write memory followup enqueue
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
        self._enqueue_refresh_preview_jobs(
            connection=connection,
            cycle_id=cycle_id,
            event_rows=event_rows,
            created_at=created_at,
        )
        self._enqueue_embedding_sync_jobs(
            connection=connection,
            cycle_id=cycle_id,
            source_event_ids=source_event_ids,
            targets=embedding_targets,
            embedding_model=self._require_runtime_setting_string(
                connection=connection,
                key="llm.embedding_model",
            ),
            created_at=created_at,
        )

    # Block: Claimed memory job complete
    def mark_memory_job_completed_in_transaction(
        self,
        *,
        connection: sqlite3.Connection,
        job_id: str,
        completed_at: int,
    ) -> None:
        self._mark_memory_job_completed(
            connection=connection,
            job_id=job_id,
            completed_at=completed_at,
        )

    # Block: Revision insert
    def _insert_revision(
        self,
        *,
        connection: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        before_json: dict[str, Any],
        after_json: dict[str, Any],
        revision_reason: str,
        evidence_event_ids: list[str],
        created_at: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO revisions (
                revision_id,
                entity_type,
                entity_id,
                before_json,
                after_json,
                reason,
                evidence_event_ids_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _opaque_id("rev"),
                entity_type,
                entity_id,
                _json_text(before_json),
                _json_text(after_json),
                revision_reason,
                _json_text(evidence_event_ids),
                created_at,
            ),
        )

    # Block: Persona update apply
    def _apply_persona_updates(
        self,
        *,
        connection: sqlite3.Connection,
        current_personality: dict[str, Any],
        updated_personality: dict[str, Any],
        persona_updates: dict[str, Any],
        updated_at: int,
    ) -> bool:
        evidence_event_ids = _string_list(
            persona_updates.get("evidence_event_ids"),
            field_name="persona_updates.evidence_event_ids",
        )
        base_personality_updated_at = int(persona_updates["base_personality_updated_at"])
        updated_row_count = connection.execute(
            """
            UPDATE self_state
            SET personality_json = ?,
                personality_updated_at = ?,
                updated_at = ?
            WHERE row_id = 1
              AND personality_updated_at = ?
            """,
            (
                _json_text(updated_personality),
                updated_at,
                updated_at,
                base_personality_updated_at,
            ),
        ).rowcount
        if updated_row_count != 1:
            return False
        connection.execute(
            """
            INSERT INTO revisions (
                revision_id,
                entity_type,
                entity_id,
                before_json,
                after_json,
                reason,
                evidence_event_ids_json,
                created_at
            )
            VALUES (?, 'self_state.personality', 'self_state', ?, ?, ?, ?, ?)
            """,
            (
                _opaque_id("rev"),
                _json_text(current_personality),
                _json_text(updated_personality),
                f"write_memory applied persona_updates: {persona_updates['evidence_summary']}",
                _json_text(evidence_event_ids),
                updated_at,
            ),
        )
        return True

    # Block: Memory state insert
    def _insert_memory_state_with_revision(
        self,
        *,
        connection: sqlite3.Connection,
        memory_kind: str,
        body_text: str,
        payload_json: dict[str, Any],
        confidence: float,
        importance: float,
        memory_strength: float,
        last_confirmed_at: int,
        evidence_event_ids: list[str],
        created_at: int,
        revision_reason: str,
    ) -> dict[str, Any]:
        memory_state_id = _opaque_id("mem")
        connection.execute(
            """
            INSERT INTO memory_states (
                memory_state_id,
                memory_kind,
                body_text,
                payload_json,
                confidence,
                importance,
                memory_strength,
                searchable,
                last_confirmed_at,
                evidence_event_ids_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                memory_state_id,
                memory_kind,
                body_text,
                _json_text(payload_json),
                confidence,
                importance,
                memory_strength,
                last_confirmed_at,
                _json_text(evidence_event_ids),
                created_at,
                created_at,
            ),
        )
        self._insert_revision(
            connection=connection,
            entity_type="memory_states",
            entity_id=memory_state_id,
            before_json={},
            after_json=_memory_state_revision_json(
                memory_kind=memory_kind,
                body_text=body_text,
                payload_json=payload_json,
                confidence=confidence,
                importance=importance,
                memory_strength=memory_strength,
                searchable=True,
                last_confirmed_at=last_confirmed_at,
                evidence_event_ids=evidence_event_ids,
                created_at=created_at,
                updated_at=created_at,
                valid_from_ts=None,
                valid_to_ts=None,
                last_accessed_at=None,
            ),
            revision_reason=revision_reason,
            evidence_event_ids=evidence_event_ids,
            created_at=created_at,
        )
        return _memory_state_target(
            entity_id=memory_state_id,
            source_updated_at=created_at,
            current_searchable=True,
        )

    # Block: Write memory plan apply
    def _apply_write_memory_plan(
        self,
        *,
        connection: sqlite3.Connection,
        memory_write_plan: dict[str, Any],
        created_at: int,
    ) -> dict[str, list[dict[str, Any]]]:
        memory_state_targets, state_embedding_targets, state_id_by_ref = self._apply_state_updates(
            connection=connection,
            state_updates=list(memory_write_plan["state_updates"]),
            created_at=created_at,
        )
        self._sync_current_emotion_from_long_mood_state(
            connection=connection,
            state_updates=list(memory_write_plan["state_updates"]),
            state_id_by_ref=state_id_by_ref,
            created_at=created_at,
        )
        self._apply_preference_updates(
            connection=connection,
            preference_updates=list(memory_write_plan["preference_updates"]),
            created_at=created_at,
        )
        event_affect_targets = self._apply_event_affect_updates(
            connection=connection,
            event_affect_updates=list(memory_write_plan["event_affect"]),
            created_at=created_at,
        )
        self._apply_event_about_time(
            connection=connection,
            event_annotations=list(memory_write_plan["event_annotations"]),
            created_at=created_at,
        )
        self._apply_event_entities(
            connection=connection,
            event_annotations=list(memory_write_plan["event_annotations"]),
            created_at=created_at,
        )
        self._apply_context_updates(
            connection=connection,
            context_updates=dict(memory_write_plan["context_updates"]),
            state_id_by_ref=state_id_by_ref,
            created_at=created_at,
        )
        self._apply_state_about_time(
            connection=connection,
            state_updates=list(memory_write_plan["state_updates"]),
            state_id_by_ref=state_id_by_ref,
            created_at=created_at,
        )
        self._apply_state_entities(
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

    # Block: State update apply
    def _apply_state_updates(
        self,
        *,
        connection: sqlite3.Connection,
        state_updates: list[dict[str, Any]],
        created_at: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
        memory_state_targets: list[dict[str, Any]] = []
        embedding_targets: list[dict[str, Any]] = []
        state_id_by_ref: dict[str, str] = {}
        for state_update in state_updates:
            operation = str(state_update["operation"])
            if operation == "upsert":
                if str(state_update["memory_kind"]) == "long_mood_state":
                    memory_state_target = self._upsert_long_mood_state_with_revision(
                        connection=connection,
                        state_update=state_update,
                        created_at=created_at,
                    )
                else:
                    memory_state_target = self._insert_memory_state_with_revision(
                        connection=connection,
                        memory_kind=str(state_update["memory_kind"]),
                        body_text=str(state_update["body_text"]),
                        payload_json=dict(state_update["payload"]),
                        confidence=float(state_update["confidence"]),
                        importance=float(state_update["importance"]),
                        memory_strength=float(state_update["memory_strength"]),
                        last_confirmed_at=int(state_update["last_confirmed_at"]),
                        evidence_event_ids=list(state_update["evidence_event_ids"]),
                        created_at=created_at,
                        revision_reason=str(state_update["revision_reason"]),
                    )
                embedding_targets.append(dict(memory_state_target))
            else:
                memory_state_target, embedding_target = self._apply_existing_memory_state_update(
                    connection=connection,
                    state_update=state_update,
                    created_at=created_at,
                )
                if embedding_target is not None:
                    embedding_targets.append(embedding_target)
            memory_state_targets.append(memory_state_target)
            state_id_by_ref[str(state_update["state_ref"])] = str(memory_state_target["entity_id"])
        return (memory_state_targets, embedding_targets, state_id_by_ref)

    # Block: Long mood state upsert
    def _upsert_long_mood_state_with_revision(
        self,
        *,
        connection: sqlite3.Connection,
        state_update: dict[str, Any],
        created_at: int,
    ) -> dict[str, Any]:
        existing_row = connection.execute(
            """
            SELECT
                memory_state_id,
                memory_kind,
                body_text,
                payload_json,
                confidence,
                importance,
                memory_strength,
                searchable,
                last_confirmed_at,
                evidence_event_ids_json,
                created_at,
                updated_at,
                valid_from_ts,
                valid_to_ts,
                last_accessed_at
            FROM memory_states
            WHERE memory_kind = 'long_mood_state'
            ORDER BY searchable DESC, updated_at DESC, created_at DESC, memory_state_id DESC
            LIMIT 1
            """
        ).fetchone()
        if existing_row is None:
            return self._insert_memory_state_with_revision(
                connection=connection,
                memory_kind=str(state_update["memory_kind"]),
                body_text=str(state_update["body_text"]),
                payload_json=dict(state_update["payload"]),
                confidence=float(state_update["confidence"]),
                importance=float(state_update["importance"]),
                memory_strength=float(state_update["memory_strength"]),
                last_confirmed_at=int(state_update["last_confirmed_at"]),
                evidence_event_ids=list(state_update["evidence_event_ids"]),
                created_at=created_at,
                revision_reason=str(state_update["revision_reason"]),
            )
        before_json = _memory_state_revision_json_from_row(existing_row)
        after_json = _memory_state_revision_json(
            memory_kind="long_mood_state",
            body_text=str(state_update["body_text"]),
            payload_json=dict(state_update["payload"]),
            confidence=float(state_update["confidence"]),
            importance=float(state_update["importance"]),
            memory_strength=float(state_update["memory_strength"]),
            searchable=True,
            last_confirmed_at=int(state_update["last_confirmed_at"]),
            evidence_event_ids=_merged_unique_strings(
                _decoded_string_array_json(existing_row["evidence_event_ids_json"]),
                list(state_update["evidence_event_ids"]),
            ),
            created_at=int(existing_row["created_at"]),
            updated_at=created_at,
            valid_from_ts=existing_row["valid_from_ts"],
            valid_to_ts=None,
            last_accessed_at=existing_row["last_accessed_at"],
        )
        if after_json != before_json:
            connection.execute(
                """
                UPDATE memory_states
                SET body_text = ?,
                    payload_json = ?,
                    confidence = ?,
                    importance = ?,
                    memory_strength = ?,
                    searchable = 1,
                    last_confirmed_at = ?,
                    evidence_event_ids_json = ?,
                    updated_at = ?,
                    valid_to_ts = NULL
                WHERE memory_state_id = ?
                """,
                (
                    str(state_update["body_text"]),
                    _json_text(dict(state_update["payload"])),
                    float(state_update["confidence"]),
                    float(state_update["importance"]),
                    float(state_update["memory_strength"]),
                    int(state_update["last_confirmed_at"]),
                    _json_text(list(after_json["evidence_event_ids"])),
                    created_at,
                    str(existing_row["memory_state_id"]),
                ),
            )
            self._insert_revision(
                connection=connection,
                entity_type="memory_states",
                entity_id=str(existing_row["memory_state_id"]),
                before_json=before_json,
                after_json=after_json,
                revision_reason=str(state_update["revision_reason"]),
                evidence_event_ids=list(after_json["evidence_event_ids"]),
                created_at=created_at,
            )
        return _memory_state_target(
            entity_id=str(existing_row["memory_state_id"]),
            source_updated_at=created_at if after_json != before_json else int(existing_row["updated_at"]),
            current_searchable=True,
        )

    # Block: Current emotion sync
    def _sync_current_emotion_from_long_mood_state(
        self,
        *,
        connection: sqlite3.Connection,
        state_updates: list[dict[str, Any]],
        state_id_by_ref: dict[str, str],
        created_at: int,
    ) -> None:
        long_mood_state_update = next(
            (
                state_update
                for state_update in state_updates
                if str(state_update["operation"]) == "upsert"
                and str(state_update["memory_kind"]) == "long_mood_state"
            ),
            None,
        )
        if long_mood_state_update is None:
            return
        target_state_id = state_id_by_ref.get(str(long_mood_state_update["state_ref"]))
        if target_state_id is None:
            raise RuntimeError("long_mood_state state_ref must resolve to memory_state_id")
        state_row = self._fetch_memory_state_row_for_update(
            connection=connection,
            memory_state_id=target_state_id,
        )
        mood_payload = _decoded_object_json(state_row["payload_json"])
        next_current_emotion = _current_emotion_json_from_long_mood_payload(
            payload=mood_payload,
        )
        self._update_self_state_current_emotion(
            connection=connection,
            current_emotion_json=next_current_emotion,
            revision_reason="write_memory synced current emotion from long mood state",
            evidence_event_ids=list(long_mood_state_update["evidence_event_ids"]),
            created_at=created_at,
        )

    # Block: Current emotion update
    def _update_self_state_current_emotion(
        self,
        *,
        connection: sqlite3.Connection,
        current_emotion_json: dict[str, Any],
        revision_reason: str,
        evidence_event_ids: list[str],
        created_at: int,
    ) -> None:
        self_state_row = connection.execute(
            """
            SELECT current_emotion_json
            FROM self_state
            WHERE row_id = 1
            """
        ).fetchone()
        if self_state_row is None:
            raise RuntimeError("self_state row is missing")
        before_json = _decoded_object_json(self_state_row["current_emotion_json"])
        if before_json == current_emotion_json:
            return
        connection.execute(
            """
            UPDATE self_state
            SET current_emotion_json = ?,
                updated_at = ?
            WHERE row_id = 1
            """,
            (
                _json_text(current_emotion_json),
                created_at,
            ),
        )
        self._insert_revision(
            connection=connection,
            entity_type="self_state.current_emotion",
            entity_id="self_state",
            before_json=before_json,
            after_json=current_emotion_json,
            revision_reason=revision_reason,
            evidence_event_ids=evidence_event_ids,
            created_at=created_at,
        )

    # Block: Existing state update apply
    def _apply_existing_memory_state_update(
        self,
        *,
        connection: sqlite3.Connection,
        state_update: dict[str, Any],
        created_at: int,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        target_state_row = self._fetch_memory_state_row_for_update(
            connection=connection,
            memory_state_id=str(state_update["target_state_id"]),
        )
        target_memory_kind = str(target_state_row["memory_kind"])
        if target_memory_kind != str(state_update["memory_kind"]):
            raise RuntimeError("write_memory state_updates.memory_kind must match target_state_id memory_kind")
        operation = str(state_update["operation"])
        before_json = _memory_state_revision_json_from_row(target_state_row)
        if operation == "close":
            after_json = self._closed_memory_state_revision_json(
                before_json=before_json,
                valid_to_ts=int(state_update["valid_to_ts"]),
                evidence_event_ids=list(state_update["evidence_event_ids"]),
                updated_at=created_at,
            )
        elif operation == "mark_done":
            after_json = self._done_memory_state_revision_json(
                before_json=before_json,
                done_at=int(state_update["done_at"]),
                done_reason=str(state_update["done_reason"]),
                evidence_event_ids=list(state_update["evidence_event_ids"]),
                updated_at=created_at,
            )
        elif operation == "revise_confidence":
            after_json = self._revised_memory_state_revision_json(
                before_json=before_json,
                confidence=float(state_update["confidence"]),
                importance=float(state_update["importance"]),
                memory_strength=float(state_update["memory_strength"]),
                last_confirmed_at=int(state_update["last_confirmed_at"]),
                evidence_event_ids=list(state_update["evidence_event_ids"]),
                updated_at=created_at,
            )
        else:
            raise RuntimeError("write_memory state_updates.operation is invalid")
        if after_json == before_json:
            return (
                _memory_state_target(
                    entity_id=str(target_state_row["memory_state_id"]),
                    source_updated_at=int(target_state_row["updated_at"]),
                    current_searchable=bool(target_state_row["searchable"]),
                ),
                None,
            )
        connection.execute(
            """
            UPDATE memory_states
            SET payload_json = ?,
                confidence = ?,
                importance = ?,
                memory_strength = ?,
                searchable = ?,
                last_confirmed_at = ?,
                evidence_event_ids_json = ?,
                updated_at = ?,
                valid_to_ts = ?
            WHERE memory_state_id = ?
            """,
            (
                _json_text(after_json["payload"]),
                float(after_json["confidence"]),
                float(after_json["importance"]),
                float(after_json["memory_strength"]),
                1 if bool(after_json["searchable"]) else 0,
                int(after_json["last_confirmed_at"]),
                _json_text(list(after_json["evidence_event_ids"])),
                int(after_json["updated_at"]),
                after_json.get("valid_to_ts"),
                str(target_state_row["memory_state_id"]),
            ),
        )
        self._insert_revision(
            connection=connection,
            entity_type="memory_states",
            entity_id=str(target_state_row["memory_state_id"]),
            before_json=before_json,
            after_json=after_json,
            revision_reason=str(state_update["revision_reason"]),
            evidence_event_ids=list(after_json["evidence_event_ids"]),
            created_at=created_at,
        )
        memory_state_target = _memory_state_target(
            entity_id=str(target_state_row["memory_state_id"]),
            source_updated_at=created_at,
            current_searchable=bool(after_json["searchable"]),
        )
        embedding_target = None
        if bool(before_json["searchable"]) != bool(after_json["searchable"]):
            embedding_target = dict(memory_state_target)
        return (memory_state_target, embedding_target)

    # Block: Memory state fetch for update
    def _fetch_memory_state_row_for_update(
        self,
        *,
        connection: sqlite3.Connection,
        memory_state_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT
                memory_state_id,
                memory_kind,
                body_text,
                payload_json,
                confidence,
                importance,
                memory_strength,
                searchable,
                last_confirmed_at,
                evidence_event_ids_json,
                created_at,
                updated_at,
                valid_from_ts,
                valid_to_ts,
                last_accessed_at
            FROM memory_states
            WHERE memory_state_id = ?
            """,
            (memory_state_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("write_memory state_updates.target_state_id is missing")
        return row

    # Block: Closed state revision json
    def _closed_memory_state_revision_json(
        self,
        *,
        before_json: dict[str, Any],
        valid_to_ts: int,
        evidence_event_ids: list[str],
        updated_at: int,
    ) -> dict[str, Any]:
        return {
            **before_json,
            "searchable": False,
            "last_confirmed_at": updated_at,
            "evidence_event_ids": _merged_unique_strings(
                list(before_json["evidence_event_ids"]),
                evidence_event_ids,
            ),
            "updated_at": updated_at,
            "valid_to_ts": valid_to_ts,
        }

    # Block: Done state revision json
    def _done_memory_state_revision_json(
        self,
        *,
        before_json: dict[str, Any],
        done_at: int,
        done_reason: str,
        evidence_event_ids: list[str],
        updated_at: int,
    ) -> dict[str, Any]:
        after_payload = dict(before_json["payload"])
        after_payload["status"] = "done"
        after_payload["done_at"] = done_at
        after_payload["done_reason"] = done_reason
        after_payload["done_evidence_event_ids"] = _merged_unique_strings(
            _string_list_or_empty(after_payload.get("done_evidence_event_ids")),
            evidence_event_ids,
        )
        return {
            **before_json,
            "payload": after_payload,
            "searchable": False,
            "last_confirmed_at": updated_at,
            "evidence_event_ids": _merged_unique_strings(
                list(before_json["evidence_event_ids"]),
                evidence_event_ids,
            ),
            "updated_at": updated_at,
            "valid_to_ts": done_at,
        }

    # Block: Revised state revision json
    def _revised_memory_state_revision_json(
        self,
        *,
        before_json: dict[str, Any],
        confidence: float,
        importance: float,
        memory_strength: float,
        last_confirmed_at: int,
        evidence_event_ids: list[str],
        updated_at: int,
    ) -> dict[str, Any]:
        return {
            **before_json,
            "confidence": confidence,
            "importance": importance,
            "memory_strength": memory_strength,
            "last_confirmed_at": last_confirmed_at,
            "evidence_event_ids": _merged_unique_strings(
                list(before_json["evidence_event_ids"]),
                evidence_event_ids,
            ),
            "updated_at": updated_at,
        }

    # Block: Preference update apply
    def _apply_preference_updates(
        self,
        *,
        connection: sqlite3.Connection,
        preference_updates: list[dict[str, Any]],
        created_at: int,
    ) -> None:
        for preference_update in preference_updates:
            self._upsert_preference_memory_with_revision(
                connection=connection,
                preference_update=preference_update,
                created_at=created_at,
            )

    # Block: Event affect apply
    def _apply_event_affect_updates(
        self,
        *,
        connection: sqlite3.Connection,
        event_affect_updates: list[dict[str, Any]],
        created_at: int,
    ) -> list[dict[str, Any]]:
        embedding_targets: list[dict[str, Any]] = []
        for event_affect_update in event_affect_updates:
            embedding_targets.append(
                self._upsert_event_affect_with_revision(
                    connection=connection,
                    event_affect_update=event_affect_update,
                    created_at=created_at,
                )
            )
        return embedding_targets

    # Block: Context update apply
    def _apply_context_updates(
        self,
        *,
        connection: sqlite3.Connection,
        context_updates: dict[str, Any],
        state_id_by_ref: dict[str, str],
        created_at: int,
    ) -> None:
        for event_link_update in list(context_updates["event_links"]):
            self._upsert_event_link_with_revision(
                connection=connection,
                event_link_update=event_link_update,
                created_at=created_at,
            )
        for event_thread_update in list(context_updates["event_threads"]):
            self._upsert_event_thread_with_revision(
                connection=connection,
                event_thread_update=event_thread_update,
                created_at=created_at,
            )
        for state_link_update in list(context_updates["state_links"]):
            from_state_id = state_id_by_ref.get(str(state_link_update["from_state_ref"]))
            to_state_id = state_id_by_ref.get(str(state_link_update["to_state_ref"]))
            if from_state_id is None or to_state_id is None:
                raise RuntimeError("write_memory state_links must resolve to inserted state refs")
            self._upsert_state_link_with_revision(
                connection=connection,
                from_state_id=from_state_id,
                to_state_id=to_state_id,
                state_link_update=state_link_update,
                created_at=created_at,
            )

    # Block: イベント時制反映
    def _apply_event_about_time(
        self,
        *,
        connection: sqlite3.Connection,
        event_annotations: list[dict[str, Any]],
        created_at: int,
    ) -> None:
        for event_annotation in event_annotations:
            self._replace_event_about_time(
                connection=connection,
                event_annotation=event_annotation,
                created_at=created_at,
            )

    # Block: イベント時制置換
    def _replace_event_about_time(
        self,
        *,
        connection: sqlite3.Connection,
        event_annotation: dict[str, Any],
        created_at: int,
    ) -> None:
        event_id = str(event_annotation["event_id"])
        connection.execute(
            """
            DELETE FROM event_about_time
            WHERE event_id = ?
            """,
            (event_id,),
        )
        about_time = event_annotation.get("about_time")
        if not isinstance(about_time, dict):
            return
        connection.execute(
            """
            INSERT INTO event_about_time (
                event_about_time_id,
                event_id,
                about_start_ts,
                about_end_ts,
                about_year_start,
                about_year_end,
                life_stage,
                confidence,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _opaque_id("eat"),
                event_id,
                about_time.get("about_start_ts"),
                about_time.get("about_end_ts"),
                about_time.get("about_year_start"),
                about_time.get("about_year_end"),
                about_time.get("life_stage"),
                float(about_time["about_time_confidence"]),
                created_at,
                created_at,
            ),
        )

    # Block: イベントエンティティ反映
    def _apply_event_entities(
        self,
        *,
        connection: sqlite3.Connection,
        event_annotations: list[dict[str, Any]],
        created_at: int,
    ) -> None:
        for event_annotation in event_annotations:
            self._replace_event_entities(
                connection=connection,
                event_annotation=event_annotation,
                created_at=created_at,
            )

    # Block: イベントエンティティ置換
    def _replace_event_entities(
        self,
        *,
        connection: sqlite3.Connection,
        event_annotation: dict[str, Any],
        created_at: int,
    ) -> None:
        event_id = str(event_annotation["event_id"])
        connection.execute(
            """
            DELETE FROM event_entities
            WHERE event_id = ?
            """,
            (event_id,),
        )
        for entity_entry in _event_entity_entries_from_annotation(event_annotation):
            connection.execute(
                """
                INSERT INTO event_entities (
                    event_entity_id,
                    event_id,
                    entity_type_norm,
                    entity_name_raw,
                    entity_name_norm,
                    confidence,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _opaque_id("een"),
                    event_id,
                    str(entity_entry["entity_type_norm"]),
                    str(entity_entry["entity_name_raw"]),
                    _normalized_entity_name(str(entity_entry["entity_name_raw"])),
                    float(entity_entry["confidence"]),
                    created_at,
                ),
            )

    # Block: 状態エンティティ反映
    def _apply_state_entities(
        self,
        *,
        connection: sqlite3.Connection,
        state_updates: list[dict[str, Any]],
        state_id_by_ref: dict[str, str],
        created_at: int,
    ) -> None:
        applied_state_ids: set[str] = set()
        for state_update in state_updates:
            operation = str(state_update["operation"])
            state_id = (
                state_id_by_ref.get(str(state_update["state_ref"]))
                if operation == "upsert"
                else str(state_update["target_state_id"])
            )
            if not isinstance(state_id, str) or not state_id or state_id in applied_state_ids:
                continue
            state_row = self._fetch_memory_state_row_for_update(
                connection=connection,
                memory_state_id=state_id,
            )
            self._replace_state_entities(
                connection=connection,
                state_row=state_row,
                created_at=created_at,
            )
            applied_state_ids.add(state_id)

    # Block: 状態時制反映
    def _apply_state_about_time(
        self,
        *,
        connection: sqlite3.Connection,
        state_updates: list[dict[str, Any]],
        state_id_by_ref: dict[str, str],
        created_at: int,
    ) -> None:
        applied_state_ids: set[str] = set()
        for state_update in state_updates:
            operation = str(state_update["operation"])
            state_id = (
                state_id_by_ref.get(str(state_update["state_ref"]))
                if operation == "upsert"
                else str(state_update["target_state_id"])
            )
            if not isinstance(state_id, str) or not state_id or state_id in applied_state_ids:
                continue
            state_row = self._fetch_memory_state_row_for_update(
                connection=connection,
                memory_state_id=state_id,
            )
            self._replace_state_about_time(
                connection=connection,
                state_row=state_row,
                created_at=created_at,
            )
            applied_state_ids.add(state_id)

    # Block: 状態時制置換
    def _replace_state_about_time(
        self,
        *,
        connection: sqlite3.Connection,
        state_row: sqlite3.Row,
        created_at: int,
    ) -> None:
        memory_state_id = str(state_row["memory_state_id"])
        connection.execute(
            """
            DELETE FROM state_about_time
            WHERE memory_state_id = ?
            """,
            (memory_state_id,),
        )
        about_time = _state_about_time_from_row(state_row)
        if about_time is None:
            return
        connection.execute(
            """
            INSERT INTO state_about_time (
                state_about_time_id,
                memory_state_id,
                about_start_ts,
                about_end_ts,
                about_year_start,
                about_year_end,
                life_stage,
                confidence,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _opaque_id("sat"),
                memory_state_id,
                about_time["about_start_ts"],
                about_time["about_end_ts"],
                about_time["about_year_start"],
                about_time["about_year_end"],
                about_time["life_stage"],
                about_time["confidence"],
                created_at,
                created_at,
            ),
        )

    # Block: 状態エンティティ置換
    def _replace_state_entities(
        self,
        *,
        connection: sqlite3.Connection,
        state_row: sqlite3.Row,
        created_at: int,
    ) -> None:
        memory_state_id = str(state_row["memory_state_id"])
        connection.execute(
            """
            DELETE FROM state_entities
            WHERE memory_state_id = ?
            """,
            (memory_state_id,),
        )
        for entity_entry in _state_entity_entries_from_row(state_row):
            connection.execute(
                """
                INSERT INTO state_entities (
                    state_entity_id,
                    memory_state_id,
                    entity_type_norm,
                    entity_name_raw,
                    entity_name_norm,
                    confidence,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _opaque_id("sen"),
                    memory_state_id,
                    entity_entry["entity_type_norm"],
                    entity_entry["entity_name_raw"],
                    entity_entry["entity_name_norm"],
                    entity_entry["confidence"],
                    created_at,
                ),
            )

    # Block: Preference memory upsert
    def _upsert_preference_memory_with_revision(
        self,
        *,
        connection: sqlite3.Connection,
        preference_update: dict[str, Any],
        created_at: int,
    ) -> None:
        target_entity_ref = dict(preference_update["target_entity_ref"])
        target_key = _preference_target_key(target_entity_ref=target_entity_ref)
        target_entity_ref_json = _normalized_target_entity_ref_json(target_entity_ref)
        existing_row = connection.execute(
            """
            SELECT preference_id,
                   owner_scope,
                   target_entity_ref_json,
                   target_key,
                   domain,
                   polarity,
                   status,
                   confidence,
                   evidence_event_ids_json,
                   created_at,
                   updated_at
            FROM preference_memory
            WHERE owner_scope = ?
              AND domain = ?
              AND target_key = ?
              AND polarity = ?
            ORDER BY updated_at DESC, created_at DESC, preference_id DESC
            LIMIT 1
            """,
            (
                str(preference_update["owner_scope"]),
                str(preference_update["domain"]),
                target_key,
                str(preference_update["polarity"]),
            ),
        ).fetchone()
        merged_evidence_event_ids = _merged_unique_strings(
            _decoded_string_array_json(
                existing_row["evidence_event_ids_json"] if existing_row is not None else None
            ),
            list(preference_update["evidence_event_ids"]),
        )
        after_json = {
            "owner_scope": str(preference_update["owner_scope"]),
            "target_entity_ref": target_entity_ref,
            "domain": str(preference_update["domain"]),
            "polarity": str(preference_update["polarity"]),
            "status": str(preference_update["status"]),
            "confidence": float(preference_update["confidence"]),
        }
        if existing_row is None:
            preference_id = _opaque_id("pref")
            connection.execute(
                """
                INSERT INTO preference_memory (
                    preference_id,
                    owner_scope,
                    target_entity_ref_json,
                    target_key,
                    domain,
                    polarity,
                    status,
                    confidence,
                    evidence_event_ids_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    preference_id,
                    str(preference_update["owner_scope"]),
                    target_entity_ref_json,
                    target_key,
                    str(preference_update["domain"]),
                    str(preference_update["polarity"]),
                    str(preference_update["status"]),
                    float(preference_update["confidence"]),
                    _json_text(merged_evidence_event_ids),
                    created_at,
                    created_at,
                ),
            )
            self._insert_revision(
                connection=connection,
                entity_type="preference_memory",
                entity_id=preference_id,
                before_json={},
                after_json=after_json,
                revision_reason=str(preference_update["revision_reason"]),
                evidence_event_ids=merged_evidence_event_ids,
                created_at=created_at,
            )
            self._sync_stable_preference_projection(
                connection=connection,
                preference_row={
                    "preference_id": preference_id,
                    "owner_scope": str(preference_update["owner_scope"]),
                    "target_entity_ref_json": target_entity_ref_json,
                    "target_key": target_key,
                    "domain": str(preference_update["domain"]),
                    "polarity": str(preference_update["polarity"]),
                    "status": str(preference_update["status"]),
                    "confidence": float(preference_update["confidence"]),
                    "evidence_event_ids_json": _json_text(merged_evidence_event_ids),
                    "created_at": created_at,
                    "updated_at": created_at,
                },
            )
            return
        preference_id = str(existing_row["preference_id"])
        before_json = {
            "owner_scope": str(existing_row["owner_scope"]),
            "target_entity_ref": _decoded_object_json(existing_row["target_entity_ref_json"]),
            "domain": str(existing_row["domain"]),
            "polarity": str(existing_row["polarity"]),
            "status": str(existing_row["status"]),
            "confidence": float(existing_row["confidence"]),
        }
        connection.execute(
            """
            UPDATE preference_memory
            SET target_entity_ref_json = ?,
                target_key = ?,
                status = ?,
                confidence = ?,
                evidence_event_ids_json = ?,
                updated_at = ?
            WHERE preference_id = ?
            """,
            (
                target_entity_ref_json,
                target_key,
                str(preference_update["status"]),
                float(preference_update["confidence"]),
                _json_text(merged_evidence_event_ids),
                created_at,
                preference_id,
            ),
        )
        self._insert_revision(
            connection=connection,
            entity_type="preference_memory",
            entity_id=preference_id,
            before_json=before_json,
            after_json=after_json,
            revision_reason=str(preference_update["revision_reason"]),
            evidence_event_ids=merged_evidence_event_ids,
            created_at=created_at,
        )
        self._sync_stable_preference_projection(
            connection=connection,
            preference_row={
                "preference_id": preference_id,
                "owner_scope": str(existing_row["owner_scope"]),
                "target_entity_ref_json": target_entity_ref_json,
                "target_key": str(existing_row["target_key"]),
                "domain": str(existing_row["domain"]),
                "polarity": str(existing_row["polarity"]),
                "status": str(preference_update["status"]),
                "confidence": float(preference_update["confidence"]),
                "evidence_event_ids_json": _json_text(merged_evidence_event_ids),
                "created_at": int(existing_row["created_at"]),
                "updated_at": created_at,
            },
        )

    # Block: Stable preference projection sync
    def _sync_stable_preference_projection(
        self,
        *,
        connection: sqlite3.Connection,
        preference_row: dict[str, Any] | sqlite3.Row,
    ) -> None:
        owner_scope = str(preference_row["owner_scope"])
        target_entity_ref_json = str(preference_row["target_entity_ref_json"])
        target_key = str(preference_row["target_key"])
        domain = str(preference_row["domain"])
        polarity = str(preference_row["polarity"])
        status = str(preference_row["status"])
        if owner_scope != "self" or status not in {"confirmed", "revoked"}:
            connection.execute(
                """
                DELETE FROM stable_preference_projection
                WHERE owner_scope = ?
                  AND domain = ?
                  AND target_key = ?
                  AND polarity = ?
                """,
                (owner_scope, domain, target_key, polarity),
            )
            return
        connection.execute(
            """
            INSERT INTO stable_preference_projection (
                owner_scope,
                target_entity_ref_json,
                target_key,
                domain,
                polarity,
                preference_id,
                status,
                confidence,
                evidence_event_ids_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_scope, domain, target_key, polarity)
            DO UPDATE SET
                target_entity_ref_json = excluded.target_entity_ref_json,
                preference_id = excluded.preference_id,
                status = excluded.status,
                confidence = excluded.confidence,
                evidence_event_ids_json = excluded.evidence_event_ids_json,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            (
                owner_scope,
                target_entity_ref_json,
                target_key,
                domain,
                polarity,
                str(preference_row["preference_id"]),
                status,
                float(preference_row["confidence"]),
                str(preference_row["evidence_event_ids_json"]),
                int(preference_row["created_at"]),
                int(preference_row["updated_at"]),
            ),
        )

    # Block: Stable preference projection rebuild
    def _rebuild_stable_preference_projection(
        self,
        *,
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute("DELETE FROM stable_preference_projection")
        seen_keys: set[tuple[str, str, str, str]] = set()
        for row in connection.execute(
            """
            SELECT
                preference_id,
                owner_scope,
                target_entity_ref_json,
                target_key,
                domain,
                polarity,
                status,
                confidence,
                evidence_event_ids_json,
                created_at,
                updated_at
            FROM preference_memory
            ORDER BY updated_at DESC, created_at DESC, preference_id DESC
            """
        ).fetchall():
            projection_key = (
                str(row["owner_scope"]),
                str(row["domain"]),
                str(row["target_key"]),
                str(row["polarity"]),
            )
            if projection_key in seen_keys:
                continue
            seen_keys.add(projection_key)
            self._sync_stable_preference_projection(
                connection=connection,
                preference_row=row,
            )

    # Block: Event affect upsert
    def _upsert_event_affect_with_revision(
        self,
        *,
        connection: sqlite3.Connection,
        event_affect_update: dict[str, Any],
        created_at: int,
    ) -> dict[str, Any]:
        existing_row = connection.execute(
            """
            SELECT event_affect_id,
                   event_id,
                   moment_affect_text,
                   moment_affect_labels_json,
                   vad_json,
                   confidence,
                   created_at
            FROM event_affects
            WHERE event_id = ?
            """,
            (str(event_affect_update["event_id"]),),
        ).fetchone()
        after_json = {
            "event_id": str(event_affect_update["event_id"]),
            "moment_affect_text": str(event_affect_update["moment_affect_text"]),
            "moment_affect_labels": list(event_affect_update["moment_affect_labels"]),
            "vad": dict(event_affect_update["vad"]),
            "confidence": float(event_affect_update["confidence"]),
        }
        if existing_row is None:
            event_affect_id = _opaque_id("eaf")
            connection.execute(
                """
                INSERT INTO event_affects (
                    event_affect_id,
                    event_id,
                    moment_affect_text,
                    moment_affect_labels_json,
                    vad_json,
                    confidence,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_affect_id,
                    str(event_affect_update["event_id"]),
                    str(event_affect_update["moment_affect_text"]),
                    _json_text(list(event_affect_update["moment_affect_labels"])),
                    _json_text(dict(event_affect_update["vad"])),
                    float(event_affect_update["confidence"]),
                    created_at,
                ),
            )
            self._insert_revision(
                connection=connection,
                entity_type="event_affects",
                entity_id=event_affect_id,
                before_json={},
                after_json=after_json,
                revision_reason=str(event_affect_update["revision_reason"]),
                evidence_event_ids=list(event_affect_update["evidence_event_ids"]),
                created_at=created_at,
            )
            return {
                "entity_type": "event_affect",
                "entity_id": event_affect_id,
                "source_updated_at": created_at,
                "current_searchable": True,
            }
        event_affect_id = str(existing_row["event_affect_id"])
        before_json = {
            "event_id": str(existing_row["event_id"]),
            "moment_affect_text": str(existing_row["moment_affect_text"]),
            "moment_affect_labels": _decoded_string_array_json(existing_row["moment_affect_labels_json"]),
            "vad": _decoded_object_json(existing_row["vad_json"]),
            "confidence": float(existing_row["confidence"]),
        }
        connection.execute(
            """
            UPDATE event_affects
            SET moment_affect_text = ?,
                moment_affect_labels_json = ?,
                vad_json = ?,
                confidence = ?
            WHERE event_affect_id = ?
            """,
            (
                str(event_affect_update["moment_affect_text"]),
                _json_text(list(event_affect_update["moment_affect_labels"])),
                _json_text(dict(event_affect_update["vad"])),
                float(event_affect_update["confidence"]),
                event_affect_id,
            ),
        )
        self._insert_revision(
            connection=connection,
            entity_type="event_affects",
            entity_id=event_affect_id,
            before_json=before_json,
            after_json=after_json,
            revision_reason=str(event_affect_update["revision_reason"]),
            evidence_event_ids=list(event_affect_update["evidence_event_ids"]),
            created_at=created_at,
        )
        return {
            "entity_type": "event_affect",
            "entity_id": event_affect_id,
            "source_updated_at": created_at,
            "current_searchable": True,
        }

    # Block: Event link upsert
    def _upsert_event_link_with_revision(
        self,
        *,
        connection: sqlite3.Connection,
        event_link_update: dict[str, Any],
        created_at: int,
    ) -> None:
        existing_row = connection.execute(
            """
            SELECT event_link_id,
                   from_event_id,
                   to_event_id,
                   label,
                   confidence,
                   evidence_event_ids_json
            FROM event_links
            WHERE from_event_id = ?
              AND to_event_id = ?
              AND label = ?
            """,
            (
                str(event_link_update["from_event_id"]),
                str(event_link_update["to_event_id"]),
                str(event_link_update["label"]),
            ),
        ).fetchone()
        merged_evidence_event_ids = _merged_unique_strings(
            _decoded_string_array_json(
                existing_row["evidence_event_ids_json"] if existing_row is not None else None
            ),
            list(event_link_update["evidence_event_ids"]),
        )
        after_json = {
            "from_event_id": str(event_link_update["from_event_id"]),
            "to_event_id": str(event_link_update["to_event_id"]),
            "label": str(event_link_update["label"]),
            "confidence": float(event_link_update["confidence"]),
        }
        if existing_row is None:
            event_link_id = _opaque_id("eln")
            connection.execute(
                """
                INSERT INTO event_links (
                    event_link_id,
                    from_event_id,
                    to_event_id,
                    label,
                    confidence,
                    evidence_event_ids_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_link_id,
                    str(event_link_update["from_event_id"]),
                    str(event_link_update["to_event_id"]),
                    str(event_link_update["label"]),
                    float(event_link_update["confidence"]),
                    _json_text(merged_evidence_event_ids),
                    created_at,
                    created_at,
                ),
            )
            self._insert_revision(
                connection=connection,
                entity_type="event_links",
                entity_id=event_link_id,
                before_json={},
                after_json=after_json,
                revision_reason=str(event_link_update["revision_reason"]),
                evidence_event_ids=merged_evidence_event_ids,
                created_at=created_at,
            )
            return
        event_link_id = str(existing_row["event_link_id"])
        before_json = {
            "from_event_id": str(existing_row["from_event_id"]),
            "to_event_id": str(existing_row["to_event_id"]),
            "label": str(existing_row["label"]),
            "confidence": float(existing_row["confidence"]),
        }
        connection.execute(
            """
            UPDATE event_links
            SET confidence = ?,
                evidence_event_ids_json = ?,
                updated_at = ?
            WHERE event_link_id = ?
            """,
            (
                float(event_link_update["confidence"]),
                _json_text(merged_evidence_event_ids),
                created_at,
                event_link_id,
            ),
        )
        self._insert_revision(
            connection=connection,
            entity_type="event_links",
            entity_id=event_link_id,
            before_json=before_json,
            after_json=after_json,
            revision_reason=str(event_link_update["revision_reason"]),
            evidence_event_ids=merged_evidence_event_ids,
            created_at=created_at,
        )

    # Block: Event thread upsert
    def _upsert_event_thread_with_revision(
        self,
        *,
        connection: sqlite3.Connection,
        event_thread_update: dict[str, Any],
        created_at: int,
    ) -> None:
        existing_row = connection.execute(
            """
            SELECT event_thread_id,
                   event_id,
                   thread_key,
                   confidence,
                   created_at,
                   updated_at,
                   thread_role
            FROM event_threads
            WHERE event_id = ?
              AND thread_key = ?
            """,
            (
                str(event_thread_update["event_id"]),
                str(event_thread_update["thread_key"]),
            ),
        ).fetchone()
        after_json = {
            "event_id": str(event_thread_update["event_id"]),
            "thread_key": str(event_thread_update["thread_key"]),
            "confidence": float(event_thread_update["confidence"]),
            "thread_role": event_thread_update.get("thread_role"),
        }
        if existing_row is None:
            event_thread_id = _opaque_id("eth")
            connection.execute(
                """
                INSERT INTO event_threads (
                    event_thread_id,
                    event_id,
                    thread_key,
                    confidence,
                    created_at,
                    updated_at,
                    thread_role
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_thread_id,
                    str(event_thread_update["event_id"]),
                    str(event_thread_update["thread_key"]),
                    float(event_thread_update["confidence"]),
                    created_at,
                    created_at,
                    event_thread_update.get("thread_role"),
                ),
            )
            self._insert_revision(
                connection=connection,
                entity_type="event_threads",
                entity_id=event_thread_id,
                before_json={},
                after_json=after_json,
                revision_reason=str(event_thread_update["revision_reason"]),
                evidence_event_ids=list(event_thread_update["evidence_event_ids"]),
                created_at=created_at,
            )
            return
        event_thread_id = str(existing_row["event_thread_id"])
        before_json = {
            "event_id": str(existing_row["event_id"]),
            "thread_key": str(existing_row["thread_key"]),
            "confidence": float(existing_row["confidence"]),
            "thread_role": existing_row["thread_role"],
        }
        connection.execute(
            """
            UPDATE event_threads
            SET confidence = ?,
                updated_at = ?,
                thread_role = ?
            WHERE event_thread_id = ?
            """,
            (
                float(event_thread_update["confidence"]),
                created_at,
                event_thread_update.get("thread_role"),
                event_thread_id,
            ),
        )
        self._insert_revision(
            connection=connection,
            entity_type="event_threads",
            entity_id=event_thread_id,
            before_json=before_json,
            after_json=after_json,
            revision_reason=str(event_thread_update["revision_reason"]),
            evidence_event_ids=list(event_thread_update["evidence_event_ids"]),
            created_at=created_at,
        )

    # Block: State link upsert
    def _upsert_state_link_with_revision(
        self,
        *,
        connection: sqlite3.Connection,
        from_state_id: str,
        to_state_id: str,
        state_link_update: dict[str, Any],
        created_at: int,
    ) -> None:
        existing_row = connection.execute(
            """
            SELECT state_link_id,
                   from_state_id,
                   to_state_id,
                   label,
                   confidence,
                   evidence_event_ids_json
            FROM state_links
            WHERE from_state_id = ?
              AND to_state_id = ?
              AND label = ?
            """,
            (
                from_state_id,
                to_state_id,
                str(state_link_update["label"]),
            ),
        ).fetchone()
        merged_evidence_event_ids = _merged_unique_strings(
            _decoded_string_array_json(
                existing_row["evidence_event_ids_json"] if existing_row is not None else None
            ),
            list(state_link_update["evidence_event_ids"]),
        )
        after_json = {
            "from_state_id": from_state_id,
            "to_state_id": to_state_id,
            "label": str(state_link_update["label"]),
            "confidence": float(state_link_update["confidence"]),
        }
        if existing_row is None:
            state_link_id = _opaque_id("sln")
            connection.execute(
                """
                INSERT INTO state_links (
                    state_link_id,
                    from_state_id,
                    to_state_id,
                    label,
                    confidence,
                    evidence_event_ids_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state_link_id,
                    from_state_id,
                    to_state_id,
                    str(state_link_update["label"]),
                    float(state_link_update["confidence"]),
                    _json_text(merged_evidence_event_ids),
                    created_at,
                    created_at,
                ),
            )
            self._insert_revision(
                connection=connection,
                entity_type="state_links",
                entity_id=state_link_id,
                before_json={},
                after_json=after_json,
                revision_reason=str(state_link_update["revision_reason"]),
                evidence_event_ids=merged_evidence_event_ids,
                created_at=created_at,
            )
            return
        state_link_id = str(existing_row["state_link_id"])
        before_json = {
            "from_state_id": str(existing_row["from_state_id"]),
            "to_state_id": str(existing_row["to_state_id"]),
            "label": str(existing_row["label"]),
            "confidence": float(existing_row["confidence"]),
        }
        connection.execute(
            """
            UPDATE state_links
            SET confidence = ?,
                evidence_event_ids_json = ?,
                updated_at = ?
            WHERE state_link_id = ?
            """,
            (
                float(state_link_update["confidence"]),
                _json_text(merged_evidence_event_ids),
                created_at,
                state_link_id,
            ),
        )
        self._insert_revision(
            connection=connection,
            entity_type="state_links",
            entity_id=state_link_id,
            before_json=before_json,
            after_json=after_json,
            revision_reason=str(state_link_update["revision_reason"]),
            evidence_event_ids=merged_evidence_event_ids,
            created_at=created_at,
        )

    def complete_refresh_preview_job(
        self,
        *,
        memory_job: MemoryJobRecord,
        embedding_model: str,
    ) -> str:
        if memory_job.job_kind != "refresh_preview":
            raise StoreValidationError("memory_job.job_kind must be refresh_preview")
        if not isinstance(embedding_model, str) or not embedding_model:
            raise StoreValidationError("embedding_model must be non-empty string")
        target_event_id = str(memory_job.payload["target_event_id"])
        target_event_updated_at = int(memory_job.payload["target_event_updated_at"])
        now_ms = _now_ms()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_claimed_memory_job(
                connection=connection,
                job_id=memory_job.job_id,
            )
            event_row = _fetch_events_for_ids(
                connection=connection,
                event_ids=[target_event_id],
            )[0]
            event_entity_rows = _fetch_event_entities_for_memory_snapshot(
                connection=connection,
                event_ids=[target_event_id],
            )
            event_thread_rows = _fetch_event_threads_for_memory_snapshot(
                connection=connection,
                event_ids=[target_event_id],
            )
            event_about_time_row = _fetch_event_about_time_for_preview(
                connection=connection,
                event_id=target_event_id,
            )
            event_affect_row = _fetch_event_affect_for_preview(
                connection=connection,
                event_id=target_event_id,
            )
            preview_text = _build_event_preview_text(
                event_row=event_row,
                event_entity_rows=event_entity_rows,
                event_thread_rows=event_thread_rows,
                event_about_time_row=event_about_time_row,
                event_affect_row=event_affect_row,
            )
            preview_id = self._upsert_event_preview_cache(
                connection=connection,
                event_id=target_event_id,
                preview_text=preview_text,
                source_event_updated_at=target_event_updated_at,
                updated_at=now_ms,
            )
            self._enqueue_embedding_sync_jobs(
                connection=connection,
                cycle_id=str(memory_job.payload["cycle_id"]),
                source_event_ids=[target_event_id],
                targets=[
                    {
                        "entity_type": "event",
                        "entity_id": target_event_id,
                        "source_updated_at": target_event_updated_at,
                        "current_searchable": bool(event_row["searchable"]),
                    }
                ],
                embedding_model=embedding_model,
                created_at=now_ms,
            )
            self._mark_memory_job_completed(
                connection=connection,
                job_id=memory_job.job_id,
                completed_at=now_ms,
            )
        return preview_id

    # Block: Embedding sync apply
    def complete_embedding_sync_job(self, *, memory_job: MemoryJobRecord) -> int:
        if memory_job.job_kind != "embedding_sync":
            raise StoreValidationError("memory_job.job_kind must be embedding_sync")
        embedding_model = memory_job.payload["embedding_model"]
        requested_scopes = memory_job.payload["requested_scopes"]
        targets = memory_job.payload["targets"]
        if not isinstance(embedding_model, str) or not embedding_model:
            raise StoreValidationError("embedding_sync embedding_model must be non-empty string")
        if not isinstance(requested_scopes, list) or not requested_scopes:
            raise StoreValidationError("embedding_sync requested_scopes must not be empty")
        if not isinstance(targets, list) or not targets:
            raise StoreValidationError("embedding_sync targets must not be empty")
        normalized_scopes = _normalize_embedding_scopes(requested_scopes)
        now_ms = _now_ms()
        updated_scope_count = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_claimed_memory_job(
                connection=connection,
                job_id=memory_job.job_id,
            )
            for target in targets:
                entity_type = str(target["entity_type"])
                entity_id = str(target["entity_id"])
                source_updated_at = int(target["source_updated_at"])
                current_searchable = bool(target["current_searchable"])
                # Block: Scope application
                for embedding_scope in normalized_scopes:
                    if current_searchable:
                        embedding_blob = _build_embedding_blob(
                            source_text=_resolve_embedding_source_text(
                                connection=connection,
                                entity_type=entity_type,
                                entity_id=entity_id,
                            ),
                            embedding_model=embedding_model,
                            embedding_scope=embedding_scope,
                        )
                        vec_row_id = _upsert_vec_item_row(
                            connection=connection,
                            entity_type=entity_type,
                            entity_id=entity_id,
                            embedding_model=embedding_model,
                            embedding_scope=embedding_scope,
                            source_updated_at=source_updated_at,
                            embedding_blob=embedding_blob,
                        )
                        _replace_vec_index_row(
                            connection=connection,
                            vec_row_id=vec_row_id,
                            embedding_blob=embedding_blob,
                        )
                    else:
                        vec_row_id = _mark_vec_item_unsearchable(
                            connection=connection,
                            entity_type=entity_type,
                            entity_id=entity_id,
                            embedding_model=embedding_model,
                            embedding_scope=embedding_scope,
                            source_updated_at=source_updated_at,
                        )
                        if vec_row_id is not None:
                            _delete_vec_index_row(
                                connection=connection,
                                vec_row_id=vec_row_id,
                            )
                    updated_scope_count += 1
            self._mark_memory_job_completed(
                connection=connection,
                job_id=memory_job.job_id,
                completed_at=now_ms,
            )
        return updated_scope_count

    # Block: Quarantine apply
    def complete_quarantine_memory_job(
        self,
        *,
        memory_job: MemoryJobRecord,
        embedding_model: str,
    ) -> int:
        if memory_job.job_kind != "quarantine_memory":
            raise StoreValidationError("memory_job.job_kind must be quarantine_memory")
        if not isinstance(embedding_model, str) or not embedding_model:
            raise StoreValidationError("embedding_model must be non-empty string")
        source_event_ids = memory_job.payload["source_event_ids"]
        targets = _normalize_quarantine_targets(memory_job.payload["targets"])
        reason_code = memory_job.payload["reason_code"]
        reason_note = memory_job.payload["reason_note"]
        if not isinstance(source_event_ids, list) or not source_event_ids:
            raise StoreValidationError("quarantine_memory source_event_ids must not be empty")
        if not isinstance(reason_code, str) or not reason_code:
            raise StoreValidationError("quarantine_memory reason_code must be non-empty string")
        if not isinstance(reason_note, str) or not reason_note:
            raise StoreValidationError("quarantine_memory reason_note must be non-empty string")
        now_ms = _now_ms()
        affected_count = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_claimed_memory_job(
                connection=connection,
                job_id=memory_job.job_id,
            )
            embedding_targets: list[dict[str, Any]] = []
            for raw_target in targets:
                entity_type = raw_target["entity_type"]
                entity_id = raw_target["entity_id"]
                source_updated_at, changed = self._quarantine_searchable_target(
                    connection=connection,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    updated_at=now_ms,
                )
                if changed:
                    self._insert_quarantine_revision(
                        connection=connection,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        source_event_ids=source_event_ids,
                        reason_code=reason_code,
                        reason_note=reason_note,
                        created_at=now_ms,
                    )
                    affected_count += 1
                    embedding_targets.append(
                        {
                            "entity_type": entity_type,
                            "entity_id": entity_id,
                            "source_updated_at": source_updated_at,
                            "current_searchable": False,
                        }
                    )
            if embedding_targets:
                self._enqueue_embedding_sync_jobs(
                    connection=connection,
                    cycle_id=str(memory_job.payload["cycle_id"]),
                    source_event_ids=source_event_ids,
                    targets=embedding_targets,
                    embedding_model=embedding_model,
                    created_at=now_ms,
                )
            self._mark_memory_job_completed(
                connection=connection,
                job_id=memory_job.job_id,
                completed_at=now_ms,
            )
            self._touch_runtime_housekeeping_state(
                connection=connection,
                maintenance_scope=maintenance_scope,
                last_enqueued_at=None,
                last_completed_at=now_ms,
                updated_at=now_ms,
            )
        return affected_count

    # Block: Tidy memory apply
    def complete_tidy_memory_job(self, *, memory_job: MemoryJobRecord) -> int:
        if memory_job.job_kind != "tidy_memory":
            raise StoreValidationError("memory_job.job_kind must be tidy_memory")
        payload = memory_job.payload
        maintenance_scope = payload.get("maintenance_scope")
        retention_cutoff_at = payload.get("retention_cutoff_at")
        source_event_ids = payload.get("source_event_ids")
        if not isinstance(maintenance_scope, str) or not maintenance_scope:
            raise StoreValidationError("tidy_memory maintenance_scope must be non-empty string")
        if isinstance(retention_cutoff_at, bool) or not isinstance(retention_cutoff_at, int):
            raise StoreValidationError("tidy_memory retention_cutoff_at must be integer")
        if retention_cutoff_at <= 0:
            raise StoreValidationError("tidy_memory retention_cutoff_at must be positive")
        if not isinstance(source_event_ids, list):
            raise StoreValidationError("tidy_memory source_event_ids must be a list")
        for event_id in source_event_ids:
            if not isinstance(event_id, str) or not event_id:
                raise StoreValidationError("tidy_memory source_event_ids must contain non-empty strings")
        raw_target_refs = payload.get("target_refs")
        target_refs = None
        if raw_target_refs is not None:
            target_refs = _normalize_tidy_target_refs(raw_target_refs)
        now_ms = _now_ms()
        affected_count = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_claimed_memory_job(
                connection=connection,
                job_id=memory_job.job_id,
            )

            # Block: Completed jobs GC
            if maintenance_scope == "completed_jobs_gc":
                if target_refs is not None:
                    raise StoreValidationError("tidy_memory target_refs is not allowed for completed_jobs_gc")
                affected_count += connection.execute(
                    """
                    DELETE FROM memory_jobs
                    WHERE status IN ('completed', 'dead_letter')
                      AND completed_at IS NOT NULL
                      AND completed_at < ?
                    """,
                    (retention_cutoff_at,),
                ).rowcount
                affected_count += connection.execute(
                    """
                    DELETE FROM memory_job_payloads
                    WHERE payload_id NOT IN (
                        SELECT json_extract(payload_ref_json, '$.payload_id')
                        FROM memory_jobs
                        WHERE json_extract(payload_ref_json, '$.payload_id') IS NOT NULL
                    )
                    """,
                ).rowcount

            # Block: Stale preview GC
            elif maintenance_scope == "stale_preview_gc":
                target_event_ids = None
                if target_refs is not None:
                    target_event_ids = []
                    for target_ref in target_refs:
                        if target_ref["entity_type"] != "event":
                            raise StoreValidationError("tidy_memory stale_preview_gc target_refs must be event")
                        target_event_ids.append(target_ref["entity_id"])
                placeholders = ""
                parameters: tuple[Any, ...] = (retention_cutoff_at,)
                if target_event_ids:
                    placeholders = ",".join("?" for _ in target_event_ids)
                    parameters = (retention_cutoff_at, *target_event_ids)
                affected_count += connection.execute(
                    f"""
                    DELETE FROM event_preview_cache
                    WHERE EXISTS (
                        SELECT 1
                        FROM events
                        WHERE events.event_id = event_preview_cache.event_id
                          AND events.searchable = 0
                          AND COALESCE(events.updated_at, events.created_at) < ?
                    )
                    {f"AND event_preview_cache.event_id IN ({placeholders})" if placeholders else ""}
                    """,
                    parameters,
                ).rowcount

            # Block: Stale vector GC
            elif maintenance_scope == "stale_vector_gc":
                if target_refs is not None:
                    for target_ref in target_refs:
                        if target_ref["entity_type"] not in {"event", "memory_state", "event_affect"}:
                            raise StoreValidationError("tidy_memory stale_vector_gc target_refs is invalid")
                if target_refs is None:
                    connection.execute(
                        """
                        DELETE FROM vec_items_index
                        WHERE rowid IN (
                            SELECT rowid
                            FROM vec_items
                            WHERE searchable = 0
                              AND source_updated_at < ?
                        )
                        """,
                        (retention_cutoff_at,),
                    )
                    affected_count += connection.execute(
                        """
                        DELETE FROM vec_items
                        WHERE searchable = 0
                          AND source_updated_at < ?
                        """,
                        (retention_cutoff_at,),
                    ).rowcount
                else:
                    where_clauses = ["searchable = 0", "source_updated_at < ?"]
                    parameters: list[Any] = [retention_cutoff_at]
                    entity_conditions: list[str] = []
                    for target_ref in target_refs:
                        entity_conditions.append("(entity_type = ? AND entity_id = ?)")
                        parameters.append(target_ref["entity_type"])
                        parameters.append(target_ref["entity_id"])
                    where_clauses.append("(" + " OR ".join(entity_conditions) + ")")
                    where_sql = " AND ".join(where_clauses)
                    rowids = [
                        int(row["rowid"])
                        for row in connection.execute(
                            f"SELECT rowid FROM vec_items WHERE {where_sql}",
                            tuple(parameters),
                        ).fetchall()
                    ]
                    if rowids:
                        placeholder_sql = ",".join("?" for _ in rowids)
                        connection.execute(
                            f"DELETE FROM vec_items_index WHERE rowid IN ({placeholder_sql})",
                            tuple(rowids),
                        )
                        affected_count += connection.execute(
                            f"DELETE FROM vec_items WHERE rowid IN ({placeholder_sql})",
                            tuple(rowids),
                        ).rowcount

            else:
                raise StoreValidationError("tidy_memory maintenance_scope is invalid")

            self._mark_memory_job_completed(
                connection=connection,
                job_id=memory_job.job_id,
                completed_at=now_ms,
            )
        return affected_count

    # Block: Memory job state helpers
    def _ensure_claimed_memory_job(
        self,
        *,
        connection: sqlite3.Connection,
        job_id: str,
    ) -> None:
        job_row = connection.execute(
            """
            SELECT status
            FROM memory_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if job_row is None:
            raise RuntimeError("memory job is missing")
        if job_row["status"] != "claimed":
            raise StoreConflictError("memory job must be claimed before completion")

    def _mark_memory_job_completed(
        self,
        *,
        connection: sqlite3.Connection,
        job_id: str,
        completed_at: int,
    ) -> None:
        updated_row_count = connection.execute(
            """
            UPDATE memory_jobs
            SET status = 'completed',
                completed_at = ?,
                updated_at = ?,
                last_error = NULL
            WHERE job_id = ?
              AND status = 'claimed'
            """,
            (completed_at, completed_at, job_id),
        ).rowcount
        if updated_row_count != 1:
            raise StoreConflictError("memory job must be claimed before completion")

    # Block: Quarantine target update
    def _quarantine_searchable_target(
        self,
        *,
        connection: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        updated_at: int,
    ) -> tuple[int, bool]:
        if entity_type == "event":
            row = connection.execute(
                """
                SELECT searchable, COALESCE(updated_at, created_at) AS source_updated_at
                FROM events
                WHERE event_id = ?
                """,
                (entity_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("event is missing for quarantine_memory")
            if int(row["searchable"]) == 0:
                return (int(row["source_updated_at"]), False)
            connection.execute(
                """
                UPDATE events
                SET searchable = 0,
                    updated_at = ?
                WHERE event_id = ?
                """,
                (updated_at, entity_id),
            )
            return (updated_at, True)
        if entity_type == "memory_state":
            row = connection.execute(
                """
                SELECT searchable, updated_at
                FROM memory_states
                WHERE memory_state_id = ?
                """,
                (entity_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("memory_state is missing for quarantine_memory")
            if int(row["searchable"]) == 0:
                return (int(row["updated_at"]), False)
            connection.execute(
                """
                UPDATE memory_states
                SET searchable = 0,
                    updated_at = ?
                WHERE memory_state_id = ?
                """,
                (updated_at, entity_id),
            )
            return (updated_at, True)
        raise StoreValidationError("quarantine_memory target entity_type is invalid")

    # Block: Quarantine revision insert
    def _insert_quarantine_revision(
        self,
        *,
        connection: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        source_event_ids: list[str],
        reason_code: str,
        reason_note: str,
        created_at: int,
    ) -> None:
        revision_entity_type = {
            "event": "events",
            "memory_state": "memory_states",
        }.get(entity_type)
        if revision_entity_type is None:
            raise StoreValidationError("quarantine_memory revision entity_type is invalid")
        connection.execute(
            """
            INSERT INTO revisions (
                revision_id,
                entity_type,
                entity_id,
                before_json,
                after_json,
                reason,
                evidence_event_ids_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _opaque_id("rev"),
                revision_entity_type,
                entity_id,
                _json_text({"searchable": True}),
                _json_text({"searchable": False}),
                f"quarantine_memory {reason_code}: {reason_note}",
                _json_text(source_event_ids),
                created_at,
            ),
        )

    def _upsert_event_preview_cache(
        self,
        *,
        connection: sqlite3.Connection,
        event_id: str,
        preview_text: str,
        source_event_updated_at: int,
        updated_at: int,
    ) -> str:
        preview_row = connection.execute(
            """
            SELECT preview_id
            FROM event_preview_cache
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
        preview_id = _opaque_id("prv")
        if preview_row is None:
            connection.execute(
                """
                INSERT INTO event_preview_cache (
                    preview_id,
                    event_id,
                    preview_text,
                    source_event_updated_at,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    preview_id,
                    event_id,
                    preview_text,
                    source_event_updated_at,
                    updated_at,
                    updated_at,
                ),
            )
            return preview_id
        preview_id = str(preview_row["preview_id"])
        connection.execute(
            """
            UPDATE event_preview_cache
            SET preview_text = ?,
                source_event_updated_at = ?,
                updated_at = ?
            WHERE event_id = ?
            """,
            (
                preview_text,
                source_event_updated_at,
                updated_at,
                event_id,
            ),
        )
        return preview_id

    def _require_runtime_setting_string(
        self,
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

    def _ensure_runtime_settings_defaults(
        self,
        *,
        connection: sqlite3.Connection,
        now_ms: int,
    ) -> None:
        default_values = build_default_settings()
        runtime_settings_row = connection.execute(
            """
            SELECT values_json, value_updated_at_json
            FROM runtime_settings
            WHERE row_id = 1
            """
        ).fetchone()
        if runtime_settings_row is None:
            raise RuntimeError("runtime_settings row is missing")
        current_values = json.loads(runtime_settings_row["values_json"])
        current_updated_at = json.loads(runtime_settings_row["value_updated_at_json"])
        merged_values = _merge_runtime_settings(
            default_values,
            _normalize_runtime_settings_values(
                default_settings=default_values,
                runtime_values=current_values,
            ),
        )
        merged_updated_at = _normalize_runtime_settings_updated_at(
            default_settings=default_values,
            current_updated_at=current_updated_at,
            now_ms=now_ms,
        )
        if (
            merged_values == current_values
            and merged_updated_at == current_updated_at
        ):
            return
        connection.execute(
            """
            UPDATE runtime_settings
            SET values_json = ?,
                value_updated_at_json = ?,
                updated_at = ?
            WHERE row_id = 1
            """,
            (
                _json_text(merged_values),
                _json_text(merged_updated_at),
                now_ms,
            ),
        )

    # Block: Runtime housekeeping defaults
    def _ensure_runtime_housekeeping_state_defaults(
        self,
        *,
        connection: sqlite3.Connection,
        now_ms: int,
    ) -> None:
        for maintenance_scope in TIDY_MEMORY_SCOPES:
            connection.execute(
                """
                INSERT INTO runtime_housekeeping_state (
                    maintenance_scope,
                    last_enqueued_at,
                    last_completed_at,
                    updated_at
                )
                VALUES (?, NULL, NULL, ?)
                ON CONFLICT(maintenance_scope) DO NOTHING
                """,
                (maintenance_scope, now_ms),
            )

    def _ensure_settings_editor_defaults(
        self,
        *,
        connection: sqlite3.Connection,
        now_ms: int,
    ) -> None:
        default_settings = build_default_settings()
        editor_seed = build_default_settings_editor_state(default_settings)
        connection.execute(
            """
            INSERT INTO settings_editor_state (
                row_id,
                active_character_preset_id,
                active_behavior_preset_id,
                active_conversation_preset_id,
                active_memory_preset_id,
                active_motion_preset_id,
                system_values_json,
                revision,
                updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(row_id) DO NOTHING
            """,
            (
                editor_seed["active_character_preset_id"],
                editor_seed["active_behavior_preset_id"],
                editor_seed["active_conversation_preset_id"],
                editor_seed["active_memory_preset_id"],
                editor_seed["active_motion_preset_id"],
                _json_text(editor_seed["system_values_json"]),
                int(editor_seed["revision"]),
                now_ms,
            ),
        )
        preset_seed_catalogs = build_default_settings_editor_presets(default_settings)
        for table_name in SETTINGS_EDITOR_PRESET_TABLE_NAMES:
            for index, preset_seed in enumerate(preset_seed_catalogs[table_name]):
                connection.execute(
                    f"""
                    INSERT INTO {table_name} (
                        preset_id,
                        preset_name,
                        payload_json,
                        archived,
                        sort_order,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, 0, ?, ?, ?)
                    ON CONFLICT(preset_id) DO NOTHING
                    """,
                    (
                        preset_seed["preset_id"],
                        preset_seed["preset_name"],
                        _json_text(preset_seed["payload"]),
                        (index + 1) * 10,
                        now_ms,
                        now_ms,
                    ),
                )
        camera_connection_seeds = build_default_camera_connections()
        for index, camera_connection_seed in enumerate(camera_connection_seeds):
            connection.execute(
                """
                INSERT INTO camera_connections (
                    camera_connection_id,
                    is_enabled,
                    display_name,
                    host,
                    username,
                    password,
                    sort_order,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(camera_connection_id) DO NOTHING
                """,
                (
                    camera_connection_seed["camera_connection_id"],
                    1 if bool(camera_connection_seed["is_enabled"]) else 0,
                    camera_connection_seed["display_name"],
                    camera_connection_seed["host"],
                    camera_connection_seed["username"],
                    camera_connection_seed["password"],
                    (index + 1) * 10,
                    now_ms,
                    now_ms,
                ),
            )
        # Block: Settings editor system values migration
        editor_row = connection.execute(
            """
            SELECT system_values_json
            FROM settings_editor_state
            WHERE row_id = 1
            """
        ).fetchone()
        if editor_row is None:
            raise RuntimeError("settings_editor_state row is missing")
        raw_system_values = json.loads(editor_row["system_values_json"])
        normalized_system_values = _normalize_settings_editor_system_values(raw_system_values)
        if normalized_system_values != raw_system_values:
            connection.execute(
                """
                UPDATE settings_editor_state
                SET system_values_json = ?,
                    updated_at = ?
                WHERE row_id = 1
                """,
                (
                    _json_text(normalized_system_values),
                    now_ms,
                ),
            )

    # Block: Legacy settings editor seed for schema v5
    def _ensure_settings_editor_defaults_v5(
        self,
        *,
        connection: sqlite3.Connection,
        now_ms: int,
    ) -> None:
        default_settings = build_default_settings()
        editor_seed = build_default_settings_editor_state(default_settings)
        connection.execute(
            """
            INSERT INTO settings_editor_state (
                row_id,
                active_behavior_preset_id,
                active_llm_preset_id,
                active_memory_preset_id,
                active_output_preset_id,
                direct_values_json,
                revision,
                updated_at,
                last_applied_change_set_id
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(row_id) DO NOTHING
            """,
            (
                editor_seed["active_behavior_preset_id"],
                editor_seed["active_llm_preset_id"],
                editor_seed["active_memory_preset_id"],
                editor_seed["active_output_preset_id"],
                _json_text(editor_seed["system_values_json"]),
                int(editor_seed["revision"]),
                now_ms,
            ),
        )
        preset_seeds = _legacy_settings_preset_seeds_from_defaults(default_settings)
        for index, preset_seed in enumerate(preset_seeds):
            connection.execute(
                """
                INSERT INTO settings_presets (
                    preset_id,
                    preset_kind,
                    preset_name,
                    payload_json,
                    archived,
                    sort_order,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(preset_id) DO NOTHING
                """,
                (
                    preset_seed["preset_id"],
                    preset_seed["preset_kind"],
                    preset_seed["preset_name"],
                    _json_text(preset_seed["payload"]),
                    (index + 1) * 10,
                    now_ms,
                    now_ms,
                ),
            )

    # Block: Settings editor schema normalization
    def _normalize_bootstrap_tables(
        self,
        *,
        connection: sqlite3.Connection,
    ) -> None:
        self._ensure_runtime_settings_table(connection=connection)
        self._ensure_camera_connections_table(connection=connection)
        self._ensure_settings_editor_state_table(connection=connection)

    # Block: Runtime settings table normalization
    def _ensure_runtime_settings_table(
        self,
        *,
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_settings (
                row_id INTEGER PRIMARY KEY CHECK (row_id = 1),
                values_json TEXT NOT NULL,
                value_updated_at_json TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )

    # Block: Camera connection table normalization
    def _ensure_camera_connections_table(
        self,
        *,
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS camera_connections (
                camera_connection_id TEXT PRIMARY KEY,
                is_enabled INTEGER NOT NULL CHECK (is_enabled IN (0, 1)),
                display_name TEXT NOT NULL,
                host TEXT NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_camera_connections_sort
                ON camera_connections (sort_order ASC, updated_at DESC)
            """
        )

    # Block: Settings editor table normalization
    def _ensure_settings_editor_state_table(
        self,
        *,
        connection: sqlite3.Connection,
    ) -> None:
        if self._table_exists(connection, "settings_editor_state"):
            self._drop_stale_settings_editor_state_tables(connection=connection)
            return
        recovered_editor_state = self._recover_settings_editor_state_row(
            connection=connection,
        )
        self._create_settings_editor_state_table(connection=connection)
        if recovered_editor_state is not None:
            connection.execute(
                """
                INSERT INTO settings_editor_state (
                    row_id,
                    active_character_preset_id,
                    active_behavior_preset_id,
                    active_conversation_preset_id,
                    active_memory_preset_id,
                    active_motion_preset_id,
                    system_values_json,
                    revision,
                    updated_at
                )
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recovered_editor_state["active_character_preset_id"],
                    recovered_editor_state["active_behavior_preset_id"],
                    recovered_editor_state["active_conversation_preset_id"],
                    recovered_editor_state["active_memory_preset_id"],
                    recovered_editor_state["active_motion_preset_id"],
                    recovered_editor_state["system_values_json"],
                    recovered_editor_state["revision"],
                    recovered_editor_state["updated_at"],
                ),
            )
        self._drop_stale_settings_editor_state_tables(connection=connection)

    # Block: Settings editor current table creation
    def _create_settings_editor_state_table(
        self,
        *,
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS settings_editor_state (
                row_id INTEGER PRIMARY KEY CHECK (row_id = 1),
                active_character_preset_id TEXT NOT NULL,
                active_behavior_preset_id TEXT NOT NULL,
                active_conversation_preset_id TEXT NOT NULL,
                active_memory_preset_id TEXT NOT NULL,
                active_motion_preset_id TEXT NOT NULL,
                system_values_json TEXT NOT NULL,
                revision INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )

    # Block: Settings editor repair row recovery
    def _recover_settings_editor_state_row(
        self,
        *,
        connection: sqlite3.Connection,
    ) -> dict[str, Any] | None:
        candidate_rows: list[tuple[int, int, str, dict[str, Any]]] = []
        expected_column_names = {
            "row_id",
            "active_character_preset_id",
            "active_behavior_preset_id",
            "active_conversation_preset_id",
            "active_memory_preset_id",
            "active_motion_preset_id",
            "system_values_json",
            "revision",
            "updated_at",
        }
        temp_table_rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name GLOB 'settings_editor_state_v*'
            ORDER BY name ASC
            """
        ).fetchall()
        for temp_table_row in temp_table_rows:
            table_name = str(temp_table_row["name"])
            if not self._table_has_columns(
                connection=connection,
                table_name=table_name,
                expected_column_names=expected_column_names,
            ):
                continue
            quoted_table_name = _quoted_identifier(table_name)
            row = connection.execute(
                f"""
                SELECT
                    active_character_preset_id,
                    active_behavior_preset_id,
                    active_conversation_preset_id,
                    active_memory_preset_id,
                    active_motion_preset_id,
                    system_values_json,
                    revision,
                    updated_at
                FROM {quoted_table_name}
                WHERE row_id = 1
                """
            ).fetchone()
            if row is None:
                continue
            recovered_row = {
                "active_character_preset_id": str(row["active_character_preset_id"]),
                "active_behavior_preset_id": str(row["active_behavior_preset_id"]),
                "active_conversation_preset_id": str(row["active_conversation_preset_id"]),
                "active_memory_preset_id": str(row["active_memory_preset_id"]),
                "active_motion_preset_id": str(row["active_motion_preset_id"]),
                "system_values_json": str(row["system_values_json"]),
                "revision": int(row["revision"]),
                "updated_at": int(row["updated_at"]),
            }
            candidate_rows.append(
                (
                    recovered_row["updated_at"],
                    recovered_row["revision"],
                    table_name,
                    recovered_row,
                )
            )
        if not candidate_rows:
            return None
        candidate_rows.sort(reverse=True)
        return candidate_rows[0][3]

    # Block: Settings editor stale residue cleanup
    def _drop_stale_settings_editor_state_tables(
        self,
        *,
        connection: sqlite3.Connection,
    ) -> None:
        temp_table_rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name GLOB 'settings_editor_state_v*'
            ORDER BY name ASC
            """
        ).fetchall()
        for temp_table_row in temp_table_rows:
            table_name = str(temp_table_row["name"])
            connection.execute(f"DROP TABLE IF EXISTS {_quoted_identifier(table_name)}")

    # Block: Table existence
    def _table_exists(
        self,
        connection: sqlite3.Connection,
        table_name: str,
    ) -> bool:
        row = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = ?
            """,
            (table_name,),
        ).fetchone()
        return row is not None

    # Block: Table column check
    def _table_has_columns(
        self,
        *,
        connection: sqlite3.Connection,
        table_name: str,
        expected_column_names: set[str],
    ) -> bool:
        quoted_table_name = _quoted_identifier(table_name)
        column_rows = connection.execute(
            f"""
            PRAGMA table_info({quoted_table_name})
            """
        ).fetchall()
        if not column_rows:
            return False
        column_names = {str(row["name"]) for row in column_rows}
        return expected_column_names.issubset(column_names)

    def _ensure_settings_editor_schema_v12(
        self,
        *,
        connection: sqlite3.Connection,
        now_ms: int,
    ) -> None:
        column_rows = connection.execute(
            """
            PRAGMA table_info(settings_editor_state)
            """
        ).fetchall()
        if not column_rows:
            raise RuntimeError("settings_editor_state table is missing after bootstrap normalization")
        column_names = {str(row["name"]) for row in column_rows}
        expected_column_names = {
            "row_id",
            "active_character_preset_id",
            "active_behavior_preset_id",
            "active_conversation_preset_id",
            "active_memory_preset_id",
            "active_motion_preset_id",
            "system_values_json",
            "revision",
            "updated_at",
        }
        if column_names != expected_column_names:
            raise RuntimeError("settings_editor_state schema must already be normalized to v12")
        camera_column_rows = connection.execute(
            """
            PRAGMA table_info(camera_connections)
            """
        ).fetchall()
        camera_column_names = {str(row["name"]) for row in camera_column_rows}
        expected_camera_column_names = {
            "camera_connection_id",
            "is_enabled",
            "display_name",
            "host",
            "username",
            "password",
            "sort_order",
            "created_at",
            "updated_at",
        }
        if camera_column_names != expected_camera_column_names:
            raise RuntimeError("camera_connections schema must already be normalized to v12")
        for table_name in SETTINGS_EDITOR_PRESET_TABLE_NAMES:
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    preset_id TEXT PRIMARY KEY,
                    preset_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    archived INTEGER NOT NULL CHECK (archived IN (0, 1)),
                    sort_order INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{table_name}_archived_sort
                    ON {table_name} (archived, sort_order ASC, updated_at DESC)
                """
            )
        self._ensure_settings_editor_defaults(connection=connection, now_ms=now_ms)

    # Block: Pending input claim
    def claim_next_pending_input(self) -> PendingInputRecord | None:
        now_ms = _now_ms()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT input_id, source, channel, payload_json, created_at
                FROM pending_inputs
                WHERE status = 'queued'
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE pending_inputs
                SET status = 'claimed',
                    claimed_at = ?
                WHERE input_id = ?
                  AND status = 'queued'
                """,
                (now_ms, row["input_id"]),
            )
        return PendingInputRecord(
            input_id=row["input_id"],
            source=row["source"],
            channel=row["channel"],
            created_at=int(row["created_at"]),
            payload=json.loads(row["payload_json"]),
        )

    # Block: Pending input discard
    def discard_queued_pending_input(
        self,
        *,
        input_id: str,
        discard_reason: str,
    ) -> bool:
        if not isinstance(input_id, str) or not input_id:
            raise StoreValidationError("input_id must be non-empty string")
        if not isinstance(discard_reason, str) or not discard_reason:
            raise StoreValidationError("discard_reason must be non-empty string")
        resolved_at = _now_ms()
        with self._connect() as connection:
            updated_row_count = connection.execute(
                """
                UPDATE pending_inputs
                SET status = 'discarded',
                    resolved_at = ?,
                    discard_reason = ?
                WHERE input_id = ?
                  AND status = 'queued'
                """,
                (resolved_at, discard_reason, input_id),
            ).rowcount
        if updated_row_count not in {0, 1}:
            raise StoreConflictError("pending input discard updated unexpected row count")
        return updated_row_count == 1

    # Block: Waiting browse task claim
    def claim_next_waiting_browse_task(self) -> TaskStateRecord | None:
        now_ms = _now_ms()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT task_id,
                       task_kind,
                       task_status,
                       goal_hint,
                       completion_hint_json,
                       resume_condition_json,
                       interruptible,
                       priority,
                       title,
                       step_hints_json,
                       created_at,
                       updated_at
                FROM task_state
                WHERE task_kind = 'browse'
                  AND task_status = 'waiting_external'
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            updated_row_count = connection.execute(
                """
                UPDATE task_state
                SET task_status = 'active',
                    updated_at = ?
                WHERE task_id = ?
                  AND task_status = 'waiting_external'
                """,
                (now_ms, row["task_id"]),
            ).rowcount
            if updated_row_count != 1:
                return None
        return TaskStateRecord(
            task_id=str(row["task_id"]),
            task_kind=str(row["task_kind"]),
            task_status="active",
            goal_hint=str(row["goal_hint"]),
            completion_hint=json.loads(row["completion_hint_json"]),
            resume_condition=json.loads(row["resume_condition_json"]),
            interruptible=bool(row["interruptible"]),
            priority=int(row["priority"]),
            title=(str(row["title"]) if row["title"] is not None else None),
            step_hints=json.loads(row["step_hints_json"]),
            created_at=int(row["created_at"]),
            updated_at=now_ms,
        )

    # Block: Attention state replace
    def _replace_attention_state(
        self,
        *,
        connection: sqlite3.Connection,
        attention_snapshot: dict[str, Any],
    ) -> None:
        primary_focus = attention_snapshot.get("primary_focus")
        secondary_focuses = attention_snapshot.get("secondary_focuses")
        suppressed_items = attention_snapshot.get("suppressed_items")
        revisit_queue = attention_snapshot.get("revisit_queue")
        updated_at = attention_snapshot.get("updated_at")
        if not isinstance(primary_focus, dict):
            raise StoreValidationError("attention_snapshot.primary_focus must be an object")
        if not isinstance(secondary_focuses, list):
            raise StoreValidationError("attention_snapshot.secondary_focuses must be a list")
        if not isinstance(suppressed_items, list):
            raise StoreValidationError("attention_snapshot.suppressed_items must be a list")
        if not isinstance(revisit_queue, list):
            raise StoreValidationError("attention_snapshot.revisit_queue must be a list")
        if isinstance(updated_at, bool) or not isinstance(updated_at, int):
            raise StoreValidationError("attention_snapshot.updated_at must be integer")
        updated_row_count = connection.execute(
            """
            UPDATE attention_state
            SET primary_focus_json = ?,
                secondary_focuses_json = ?,
                suppressed_items_json = ?,
                revisit_queue_json = ?,
                updated_at = ?
            WHERE row_id = 1
            """,
            (
                _json_text(primary_focus),
                _json_text(secondary_focuses),
                _json_text(suppressed_items),
                _json_text(revisit_queue),
                updated_at,
            ),
        ).rowcount
        if updated_row_count != 1:
            raise RuntimeError("attention_state row is missing")

    # Block: Body state replace
    def _replace_body_state(
        self,
        *,
        connection: sqlite3.Connection,
        body_state: dict[str, Any],
    ) -> None:
        posture = body_state.get("posture")
        mobility = body_state.get("mobility")
        sensor_availability = body_state.get("sensor_availability")
        output_locks = body_state.get("output_locks")
        load = body_state.get("load")
        updated_at = body_state.get("updated_at")
        if not isinstance(posture, dict):
            raise StoreValidationError("body_state.posture must be an object")
        if not isinstance(mobility, dict):
            raise StoreValidationError("body_state.mobility must be an object")
        if not isinstance(sensor_availability, dict):
            raise StoreValidationError("body_state.sensor_availability must be an object")
        if not isinstance(output_locks, dict):
            raise StoreValidationError("body_state.output_locks must be an object")
        if not isinstance(load, dict):
            raise StoreValidationError("body_state.load must be an object")
        if isinstance(updated_at, bool) or not isinstance(updated_at, int):
            raise StoreValidationError("body_state.updated_at must be integer")
        updated_row_count = connection.execute(
            """
            UPDATE body_state
            SET posture_json = ?,
                mobility_json = ?,
                sensor_availability_json = ?,
                output_locks_json = ?,
                load_json = ?,
                updated_at = ?
            WHERE row_id = 1
            """,
            (
                _json_text(posture),
                _json_text(mobility),
                _json_text(sensor_availability),
                _json_text(output_locks),
                _json_text(load),
                updated_at,
            ),
        ).rowcount
        if updated_row_count != 1:
            raise RuntimeError("body_state row is missing")

    # Block: World state replace
    def _replace_world_state(
        self,
        *,
        connection: sqlite3.Connection,
        world_state: dict[str, Any],
    ) -> None:
        location = world_state.get("location")
        situation_summary = world_state.get("situation_summary")
        surroundings = world_state.get("surroundings")
        affordances = world_state.get("affordances")
        constraints = world_state.get("constraints")
        attention_targets = world_state.get("attention_targets")
        external_waits = world_state.get("external_waits")
        updated_at = world_state.get("updated_at")
        if not isinstance(location, dict):
            raise StoreValidationError("world_state.location must be an object")
        if not isinstance(situation_summary, str) or not situation_summary:
            raise StoreValidationError("world_state.situation_summary must be non-empty string")
        if not isinstance(surroundings, dict):
            raise StoreValidationError("world_state.surroundings must be an object")
        if not isinstance(affordances, dict):
            raise StoreValidationError("world_state.affordances must be an object")
        if not isinstance(constraints, dict):
            raise StoreValidationError("world_state.constraints must be an object")
        if not isinstance(attention_targets, dict):
            raise StoreValidationError("world_state.attention_targets must be an object")
        if not isinstance(external_waits, dict):
            raise StoreValidationError("world_state.external_waits must be an object")
        if isinstance(updated_at, bool) or not isinstance(updated_at, int):
            raise StoreValidationError("world_state.updated_at must be integer")
        updated_row_count = connection.execute(
            """
            UPDATE world_state
            SET location_json = ?,
                situation_summary = ?,
                surroundings_json = ?,
                affordances_json = ?,
                constraints_json = ?,
                attention_targets_json = ?,
                external_waits_json = ?,
                updated_at = ?
            WHERE row_id = 1
            """,
            (
                _json_text(location),
                situation_summary,
                _json_text(surroundings),
                _json_text(affordances),
                _json_text(constraints),
                _json_text(attention_targets),
                _json_text(external_waits),
                updated_at,
            ),
        ).rowcount
        if updated_row_count != 1:
            raise RuntimeError("world_state row is missing")

    # Block: Drive state replace
    def _replace_drive_state(
        self,
        *,
        connection: sqlite3.Connection,
        drive_state: dict[str, Any],
    ) -> None:
        drive_levels = drive_state.get("drive_levels")
        priority_effects = drive_state.get("priority_effects")
        updated_at = drive_state.get("updated_at")
        if not isinstance(drive_levels, dict):
            raise StoreValidationError("drive_state.drive_levels must be an object")
        if not isinstance(priority_effects, dict):
            raise StoreValidationError("drive_state.priority_effects must be an object")
        if isinstance(updated_at, bool) or not isinstance(updated_at, int):
            raise StoreValidationError("drive_state.updated_at must be integer")
        updated_row_count = connection.execute(
            """
            UPDATE drive_state
            SET drive_levels_json = ?,
                priority_effects_json = ?,
                updated_at = ?
            WHERE row_id = 1
            """,
            (
                _json_text(drive_levels),
                _json_text(priority_effects),
                updated_at,
            ),
        ).rowcount
        if updated_row_count != 1:
            raise RuntimeError("drive_state row is missing")

    # Block: Runtime live state sync
    def _sync_runtime_live_state(
        self,
        *,
        connection: sqlite3.Connection,
        camera_available: bool,
        updated_at: int,
        cycle_context: dict[str, Any] | None,
    ) -> None:
        runtime_settings_row = connection.execute(
            """
            SELECT values_json
            FROM runtime_settings
            WHERE row_id = 1
            """
        ).fetchone()
        attention_row = connection.execute(
            """
            SELECT primary_focus_json, secondary_focuses_json
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
            """
        ).fetchall()
        if (
            runtime_settings_row is None
            or attention_row is None
            or body_row is None
            or world_row is None
        ):
            raise RuntimeError("runtime live state source rows are missing")
        live_state = build_runtime_live_state(
            effective_settings=json.loads(runtime_settings_row["values_json"]),
            camera_available=camera_available,
            attention_state={
                "primary_focus": json.loads(attention_row["primary_focus_json"]),
                "secondary_focuses": json.loads(attention_row["secondary_focuses_json"]),
            },
            active_tasks=[_task_snapshot_entry(row) for row in active_task_rows],
            waiting_tasks=[_task_snapshot_entry(row) for row in waiting_task_rows],
            previous_body_state=_decode_body_state_row(body_row),
            previous_world_state=_decode_world_state_row(world_row),
            cycle_context=cycle_context,
            updated_at=updated_at,
        )
        self._replace_body_state(
            connection=connection,
            body_state=live_state["body_state"],
        )
        self._replace_world_state(
            connection=connection,
            world_state=live_state["world_state"],
        )
        self._replace_drive_state(
            connection=connection,
            drive_state=live_state["drive_state"],
        )

    # Block: Pending input journal append
    def append_input_journal_for_pending_input(
        self,
        *,
        pending_input: PendingInputRecord,
        cycle_id: str,
    ) -> None:
        self._append_input_journal(
            observation_id=f"obs_{pending_input.input_id}",
            cycle_id=cycle_id,
            source=normalize_observation_source(
                source=pending_input.source,
                payload=pending_input.payload,
            ),
            kind=normalize_observation_kind(payload=pending_input.payload),
            captured_at=pending_input.created_at,
            receipt_summary=_pending_input_receipt_summary(pending_input),
            payload_id=pending_input.input_id,
        )

    # Block: Cycle finalize
    def finalize_pending_input_cycle(
        self,
        *,
        pending_input: PendingInputRecord,
        cycle_id: str,
        resolution_status: str,
        action_results: list[ActionHistoryRecord],
        task_mutations: list[TaskStateMutationRecord],
        pending_input_mutations: list[PendingInputMutationRecord],
        ui_events: list[dict[str, Any]],
        commit_payload: dict[str, Any],
        retrieval_run: dict[str, Any] | None = None,
        attention_snapshot: dict[str, Any] | None = None,
        discard_reason: str | None = None,
        camera_available: bool,
    ) -> int:
        if resolution_status not in {"consumed", "discarded"}:
            raise StoreValidationError("resolution_status is invalid")
        resolved_at = _now_ms()
        with self._connect() as connection:
            self._apply_task_state_mutations(
                connection=connection,
                task_mutations=task_mutations,
            )
            if attention_snapshot is not None:
                self._replace_attention_state(
                    connection=connection,
                    attention_snapshot=attention_snapshot,
                )
            followup_input_ids = self._insert_pending_input_mutations(
                connection=connection,
                pending_input_mutations=pending_input_mutations,
            )
            event_ids = self._insert_pending_input_events(
                connection=connection,
                pending_input=pending_input,
                cycle_id=cycle_id,
                action_results=action_results,
                ui_events=ui_events,
                resolved_at=resolved_at,
            )
            enqueued_memory_job_ids = self._enqueue_write_memory_jobs(
                connection=connection,
                cycle_id=cycle_id,
                event_ids=event_ids,
                created_at=resolved_at,
            )
            retrieval_run_id = self._insert_retrieval_run(
                connection=connection,
                cycle_id=cycle_id,
                retrieval_run=retrieval_run,
                resolved_event_ids=event_ids,
                created_at=resolved_at,
            )
            updated_row_count = connection.execute(
                """
                UPDATE pending_inputs
                SET status = ?,
                    resolved_at = ?,
                    discard_reason = ?
                WHERE input_id = ?
                  AND status = 'claimed'
                """,
                (resolution_status, resolved_at, discard_reason, pending_input.input_id),
            ).rowcount
            if updated_row_count != 1:
                raise StoreConflictError("pending input must be claimed before finalization")
            self._sync_runtime_live_state(
                connection=connection,
                camera_available=camera_available,
                updated_at=resolved_at,
                cycle_context=_pending_input_cycle_context(
                    pending_input=pending_input,
                    resolution_status=resolution_status,
                    action_results=action_results,
                    pending_input_mutations=pending_input_mutations,
                ),
            )
            connection.execute(
                """
                INSERT INTO commit_records (
                    cycle_id,
                    committed_at,
                    log_sync_status,
                    commit_payload_json
                )
                VALUES (?, ?, 'pending', ?)
                """,
                (
                    cycle_id,
                    resolved_at,
                    _json_text(
                        {
                            **commit_payload,
                            "followup_input_ids": followup_input_ids,
                            "event_ids": event_ids,
                            "enqueued_memory_job_ids": enqueued_memory_job_ids,
                            **(
                                {"retrieval_run_id": retrieval_run_id}
                                if retrieval_run_id is not None
                                else {}
                            ),
                        }
                    ),
                ),
            )
            commit_id = connection.execute(
                """
                SELECT commit_id
                FROM commit_records
                WHERE cycle_id = ?
                """,
                (cycle_id,),
            ).fetchone()
        if commit_id is None:
            raise RuntimeError("commit_records insert did not persist")
        finalized_commit_id = int(commit_id["commit_id"])
        self.sync_commit_log(commit_id=finalized_commit_id)
        return finalized_commit_id

    # Block: Retrieval run write
    def _insert_retrieval_run(
        self,
        *,
        connection: sqlite3.Connection,
        cycle_id: str,
        retrieval_run: dict[str, Any] | None,
        resolved_event_ids: list[str],
        created_at: int,
    ) -> str | None:
        if retrieval_run is None:
            return None
        plan_json = retrieval_run.get("plan_json")
        candidates_json = retrieval_run.get("candidates_json")
        selected_json = retrieval_run.get("selected_json")
        if not isinstance(plan_json, dict):
            raise StoreValidationError("retrieval_run.plan_json must be object")
        if not isinstance(candidates_json, dict):
            raise StoreValidationError("retrieval_run.candidates_json must be object")
        if not isinstance(selected_json, dict):
            raise StoreValidationError("retrieval_run.selected_json must be object")
        run_id = _opaque_id("retr")
        connection.execute(
            """
            INSERT INTO retrieval_runs (
                run_id,
                cycle_id,
                created_at,
                plan_json,
                candidates_json,
                selected_json,
                resolved_event_ids_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                cycle_id,
                created_at,
                _json_text(plan_json),
                _json_text(candidates_json),
                _json_text(selected_json),
                _json_text(resolved_event_ids),
            ),
        )
        return run_id

    # Block: Task cycle finalize
    def finalize_task_cycle(
        self,
        *,
        task: TaskStateRecord,
        cycle_id: str,
        final_status: str,
        action_results: list[ActionHistoryRecord],
        pending_input_mutations: list[PendingInputMutationRecord],
        ui_events: list[dict[str, Any]],
        commit_payload: dict[str, Any],
        camera_available: bool,
    ) -> int:
        if final_status not in {"completed", "abandoned"}:
            raise StoreValidationError("task final_status is invalid")
        resolved_at = _now_ms()
        with self._connect() as connection:
            updated_row_count = connection.execute(
                """
                UPDATE task_state
                SET task_status = ?,
                    updated_at = ?
                WHERE task_id = ?
                  AND task_status = 'active'
                """,
                (final_status, resolved_at, task.task_id),
            ).rowcount
            if updated_row_count != 1:
                raise StoreConflictError("task must be active before finalization")
            followup_input_ids = self._insert_pending_input_mutations(
                connection=connection,
                pending_input_mutations=pending_input_mutations,
            )
            event_ids = self._insert_task_cycle_events(
                connection=connection,
                cycle_id=cycle_id,
                action_results=action_results,
                ui_events=ui_events,
                resolved_at=resolved_at,
            )
            enqueued_memory_job_ids = self._enqueue_write_memory_jobs(
                connection=connection,
                cycle_id=cycle_id,
                event_ids=event_ids,
                created_at=resolved_at,
            )
            self._sync_runtime_live_state(
                connection=connection,
                camera_available=camera_available,
                updated_at=resolved_at,
                cycle_context=_task_cycle_context(
                    task=task,
                    final_status=final_status,
                    action_results=action_results,
                    pending_input_mutations=pending_input_mutations,
                ),
            )
            connection.execute(
                """
                INSERT INTO commit_records (
                    cycle_id,
                    committed_at,
                    log_sync_status,
                    commit_payload_json
                )
                VALUES (?, ?, 'pending', ?)
                """,
                (
                    cycle_id,
                    resolved_at,
                    _json_text(
                        {
                            **commit_payload,
                            "followup_input_ids": followup_input_ids,
                            "event_ids": event_ids,
                            "enqueued_memory_job_ids": enqueued_memory_job_ids,
                        }
                    ),
                ),
            )
            commit_id = connection.execute(
                """
                SELECT commit_id
                FROM commit_records
                WHERE cycle_id = ?
                """,
                (cycle_id,),
            ).fetchone()
        if commit_id is None:
            raise RuntimeError("commit_records insert did not persist")
        finalized_commit_id = int(commit_id["commit_id"])
        self.sync_commit_log(commit_id=finalized_commit_id)
        return finalized_commit_id

    # Block: Pending input mutation write
    def _insert_pending_input_mutations(
        self,
        *,
        connection: sqlite3.Connection,
        pending_input_mutations: list[PendingInputMutationRecord],
    ) -> list[str]:
        inserted_input_ids: list[str] = []
        for pending_input_mutation in pending_input_mutations:
            if pending_input_mutation.priority < 0:
                raise StoreValidationError("pending input mutation.priority must be non-negative")
            input_kind = pending_input_mutation.payload.get("input_kind")
            if not isinstance(input_kind, str) or not input_kind:
                raise StoreValidationError("pending input mutation.payload.input_kind must be non-empty string")
            input_id = _opaque_id("inp")
            connection.execute(
                """
                INSERT INTO pending_inputs (
                    input_id,
                    source,
                    channel,
                    client_message_id,
                    payload_json,
                    created_at,
                    priority,
                    status
                )
                VALUES (?, ?, ?, NULL, ?, ?, ?, 'queued')
                """,
                (
                    input_id,
                    pending_input_mutation.source,
                    pending_input_mutation.channel,
                    _json_text(pending_input_mutation.payload),
                    pending_input_mutation.created_at,
                    pending_input_mutation.priority,
                ),
            )
            inserted_input_ids.append(input_id)
        return inserted_input_ids

    # Block: Task state mutation apply
    def _apply_task_state_mutations(
        self,
        *,
        connection: sqlite3.Connection,
        task_mutations: list[TaskStateMutationRecord],
    ) -> None:
        for task_mutation in task_mutations:
            if task_mutation.task_status != "waiting_external":
                raise StoreValidationError("task mutation.task_status is invalid")
            if task_mutation.priority < 0:
                raise StoreValidationError("task mutation.priority must be non-negative")
            connection.execute(
                """
                INSERT INTO task_state (
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
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_mutation.task_id,
                    task_mutation.task_kind,
                    task_mutation.task_status,
                    task_mutation.goal_hint,
                    _json_text(task_mutation.completion_hint),
                    _json_text(task_mutation.resume_condition),
                    1 if task_mutation.interruptible else 0,
                    task_mutation.priority,
                    task_mutation.created_at,
                    task_mutation.created_at,
                    task_mutation.title,
                    _json_text(task_mutation.step_hints),
                ),
            )

    # Block: Task event write
    def _insert_task_cycle_events(
        self,
        *,
        connection: sqlite3.Connection,
        cycle_id: str,
        action_results: list[ActionHistoryRecord],
        ui_events: list[dict[str, Any]],
        resolved_at: int,
    ) -> list[str]:
        event_ids = self._insert_action_history(
            connection=connection,
            cycle_id=cycle_id,
            action_results=action_results,
            input_journal_refs_json=None,
        )
        response_summary = _runtime_response_summary(ui_events)
        if response_summary is None:
            return event_ids
        response_created_at = resolved_at
        if action_results:
            response_created_at = max(action_result.finished_at for action_result in action_results) + 1
        event_ids.append(
            self._insert_event(
                connection=connection,
                cycle_id=cycle_id,
                created_at=response_created_at,
                source="runtime",
                kind="external_response",
                searchable=True,
                result_summary=response_summary,
            )
        )
        return event_ids

    # Block: Memory job enqueue
    def _enqueue_write_memory_jobs(
        self,
        *,
        connection: sqlite3.Connection,
        cycle_id: str,
        event_ids: list[str],
        created_at: int,
    ) -> list[str]:
        if not event_ids:
            return []
        primary_event_id = event_ids[0]
        idempotency_key = _write_memory_job_idempotency_key(cycle_id=cycle_id, event_ids=event_ids)
        event_snapshot_refs = _event_snapshot_refs_for_write_memory_job(
            connection=connection,
            event_ids=event_ids,
        )
        payload_json = {
            "job_kind": "write_memory",
            "cycle_id": cycle_id,
            "source_event_ids": event_ids,
            "created_at": created_at,
            "idempotency_key": idempotency_key,
            "primary_event_id": primary_event_id,
            "reflection_seed_ref": {
                "ref_kind": "event",
                "ref_id": primary_event_id,
            },
            "event_snapshot_refs": event_snapshot_refs,
        }
        return [
            self._insert_memory_job(
                connection=connection,
                job_kind="write_memory",
                payload_json=payload_json,
                idempotency_key=idempotency_key,
                created_at=created_at,
            )
        ]

    def _enqueue_refresh_preview_jobs(
        self,
        *,
        connection: sqlite3.Connection,
        cycle_id: str,
        event_rows: list[sqlite3.Row],
        created_at: int,
    ) -> list[str]:
        job_ids: list[str] = []
        for event_row in event_rows:
            event_id = str(event_row["event_id"])
            source_event_updated_at = int(event_row["source_updated_at"])
            payload_json = {
                "job_kind": "refresh_preview",
                "cycle_id": cycle_id,
                "source_event_ids": [event_id],
                "created_at": created_at,
                "idempotency_key": _refresh_preview_job_idempotency_key(
                    cycle_id=cycle_id,
                    event_id=event_id,
                    event_updated_at=source_event_updated_at,
                ),
                "target_event_id": event_id,
                "target_event_updated_at": source_event_updated_at,
                "preview_reason": "event_created",
            }
            job_ids.append(
                self._insert_memory_job(
                    connection=connection,
                    job_kind="refresh_preview",
                    payload_json=payload_json,
                    idempotency_key=str(payload_json["idempotency_key"]),
                    created_at=created_at,
                )
            )
        return job_ids

    # Block: Embedding sync enqueue
    def _enqueue_embedding_sync_jobs(
        self,
        *,
        connection: sqlite3.Connection,
        cycle_id: str,
        source_event_ids: list[str],
        targets: list[dict[str, Any]],
        embedding_model: str,
        created_at: int,
    ) -> list[str]:
        if not targets:
            return []
        idempotency_key = _embedding_sync_job_idempotency_key(
            cycle_id=cycle_id,
            embedding_model=embedding_model,
            targets=targets,
        )
        payload_json = {
            "job_kind": "embedding_sync",
            "cycle_id": cycle_id,
            "source_event_ids": source_event_ids,
            "created_at": created_at,
            "idempotency_key": idempotency_key,
            "embedding_model": embedding_model,
            "requested_scopes": ["recent", "global"],
            "targets": targets,
        }
        return [
            self._insert_memory_job(
                connection=connection,
                job_kind="embedding_sync",
                payload_json=payload_json,
                idempotency_key=idempotency_key,
                created_at=created_at,
            )
        ]

    # Block: Quarantine enqueue
    def _enqueue_quarantine_memory_jobs(
        self,
        *,
        connection: sqlite3.Connection,
        cycle_id: str,
        source_event_ids: list[str],
        targets: list[dict[str, Any]],
        reason_code: str,
        reason_note: str,
        created_at: int,
    ) -> list[str]:
        if not source_event_ids:
            raise StoreValidationError("quarantine_memory source_event_ids must not be empty")
        if not isinstance(reason_code, str) or not reason_code:
            raise StoreValidationError("quarantine_memory reason_code must be non-empty string")
        if not isinstance(reason_note, str) or not reason_note:
            raise StoreValidationError("quarantine_memory reason_note must be non-empty string")
        normalized_targets = _normalize_quarantine_targets(targets)
        idempotency_key = _quarantine_memory_job_idempotency_key(
            cycle_id=cycle_id,
            reason_code=reason_code,
            targets=normalized_targets,
        )
        payload_json = {
            "job_kind": "quarantine_memory",
            "cycle_id": cycle_id,
            "source_event_ids": source_event_ids,
            "created_at": created_at,
            "idempotency_key": idempotency_key,
            "reason_code": reason_code,
            "reason_note": reason_note,
            "targets": normalized_targets,
        }
        return [
            self._insert_memory_job(
                connection=connection,
                job_kind="quarantine_memory",
                payload_json=payload_json,
                idempotency_key=idempotency_key,
                created_at=created_at,
            )
        ]

    # Block: Tidy memory enqueue
    def _enqueue_tidy_memory_jobs(
        self,
        *,
        connection: sqlite3.Connection,
        cycle_id: str,
        maintenance_scope: str,
        retention_cutoff_at: int,
        target_refs: list[dict[str, str]] | None,
        created_at: int,
    ) -> list[str]:
        if maintenance_scope not in TIDY_MEMORY_SCOPES:
            raise StoreValidationError("tidy_memory maintenance_scope is invalid")
        if retention_cutoff_at <= 0:
            raise StoreValidationError("tidy_memory retention_cutoff_at must be positive")
        normalized_target_refs = None
        if target_refs is not None:
            normalized_target_refs = _normalize_tidy_target_refs(target_refs)
        if maintenance_scope == "completed_jobs_gc" and normalized_target_refs is not None:
            raise StoreValidationError("tidy_memory target_refs is not allowed for completed_jobs_gc")
        if maintenance_scope == "stale_preview_gc" and normalized_target_refs is not None:
            for target_ref in normalized_target_refs:
                if target_ref["entity_type"] != "event":
                    raise StoreValidationError("tidy_memory stale_preview_gc target_refs must be event")
        if maintenance_scope == "stale_vector_gc" and normalized_target_refs is not None:
            for target_ref in normalized_target_refs:
                if target_ref["entity_type"] not in {"event", "memory_state", "event_affect"}:
                    raise StoreValidationError("tidy_memory stale_vector_gc target_refs is invalid")
        idempotency_key = _tidy_memory_job_idempotency_key(
            cycle_id=cycle_id,
            maintenance_scope=maintenance_scope,
            retention_cutoff_at=retention_cutoff_at,
            target_refs=normalized_target_refs,
        )
        existing_job_id = self._find_memory_job_id_by_idempotency_key(
            connection=connection,
            idempotency_key=idempotency_key,
        )
        if existing_job_id is not None:
            return [existing_job_id]
        payload_json: dict[str, Any] = {
            "job_kind": "tidy_memory",
            "cycle_id": cycle_id,
            "source_event_ids": [],
            "created_at": created_at,
            "idempotency_key": idempotency_key,
            "maintenance_scope": maintenance_scope,
            "retention_cutoff_at": retention_cutoff_at,
        }
        if normalized_target_refs is not None:
            payload_json["target_refs"] = normalized_target_refs
        job_id = self._insert_memory_job(
            connection=connection,
            job_kind="tidy_memory",
            payload_json=payload_json,
            idempotency_key=idempotency_key,
            created_at=created_at,
        )
        self._touch_runtime_housekeeping_state(
            connection=connection,
            maintenance_scope=maintenance_scope,
            last_enqueued_at=created_at,
            last_completed_at=None,
            updated_at=created_at,
        )
        return [job_id]

    # Block: Runtime housekeeping touch
    def _touch_runtime_housekeeping_state(
        self,
        *,
        connection: sqlite3.Connection,
        maintenance_scope: str,
        last_enqueued_at: int | None,
        last_completed_at: int | None,
        updated_at: int,
    ) -> None:
        if maintenance_scope not in TIDY_MEMORY_SCOPES:
            raise StoreValidationError("runtime housekeeping maintenance_scope is invalid")
        self._ensure_runtime_housekeeping_state_defaults(
            connection=connection,
            now_ms=updated_at,
        )
        current_row = connection.execute(
            """
            SELECT last_enqueued_at, last_completed_at
            FROM runtime_housekeeping_state
            WHERE maintenance_scope = ?
            """,
            (maintenance_scope,),
        ).fetchone()
        if current_row is None:
            raise RuntimeError("runtime_housekeeping_state row is missing")
        next_last_enqueued_at = (
            last_enqueued_at
            if last_enqueued_at is not None
            else current_row["last_enqueued_at"]
        )
        next_last_completed_at = (
            last_completed_at
            if last_completed_at is not None
            else current_row["last_completed_at"]
        )
        connection.execute(
            """
            UPDATE runtime_housekeeping_state
            SET last_enqueued_at = ?,
                last_completed_at = ?,
                updated_at = ?
            WHERE maintenance_scope = ?
            """,
            (
                next_last_enqueued_at,
                next_last_completed_at,
                updated_at,
                maintenance_scope,
            ),
        )

    def _insert_memory_job(
        self,
        *,
        connection: sqlite3.Connection,
        job_kind: str,
        payload_json: dict[str, Any],
        idempotency_key: str,
        created_at: int,
    ) -> str:
        existing_job_id = self._find_memory_job_id_by_idempotency_key(
            connection=connection,
            idempotency_key=idempotency_key,
        )
        if existing_job_id is not None:
            return existing_job_id
        payload_id = _opaque_id("mjp")
        job_id = _opaque_id("mjob")
        connection.execute(
            """
            INSERT INTO memory_job_payloads (
                payload_id,
                payload_kind,
                payload_version,
                job_kind,
                payload_json,
                created_at,
                idempotency_key
            )
            VALUES (?, 'memory_job_payload', 1, ?, ?, ?, ?)
            """,
            (
                payload_id,
                job_kind,
                _json_text(payload_json),
                created_at,
                idempotency_key,
            ),
        )
        connection.execute(
            """
            INSERT INTO memory_jobs (
                job_id,
                job_kind,
                payload_ref_json,
                status,
                tries,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, 'queued', 0, ?, ?)
            """,
            (
                job_id,
                job_kind,
                _json_text(
                    {
                        "payload_kind": "memory_job_payload",
                        "payload_id": payload_id,
                        "payload_version": 1,
                    }
                ),
                created_at,
                created_at,
            ),
        )
        return job_id

    # Block: Memory job idempotency lookup
    def _find_memory_job_id_by_idempotency_key(
        self,
        *,
        connection: sqlite3.Connection,
        idempotency_key: str,
    ) -> str | None:
        payload_row = connection.execute(
            """
            SELECT payload_id
            FROM memory_job_payloads
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        if payload_row is None:
            return None
        job_row = connection.execute(
            """
            SELECT job_id
            FROM memory_jobs
            WHERE json_extract(payload_ref_json, '$.payload_id') = ?
            ORDER BY created_at ASC, rowid ASC
            LIMIT 1
            """,
            (payload_row["payload_id"],),
        ).fetchone()
        if job_row is None:
            raise RuntimeError("memory_job_payload exists without memory_job")
        return str(job_row["job_id"])

    # Block: Input journal write
    def _append_input_journal(
        self,
        *,
        observation_id: str,
        cycle_id: str,
        source: str,
        kind: str,
        captured_at: int,
        receipt_summary: str,
        payload_id: str,
    ) -> None:
        now_ms = _now_ms()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO input_journal (
                    journal_id,
                    observation_id,
                    cycle_id,
                    source,
                    kind,
                    captured_at,
                    receipt_summary,
                    payload_ref_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _opaque_id("jrnl"),
                    observation_id,
                    cycle_id,
                    source,
                    kind,
                    captured_at,
                    receipt_summary,
                    _json_text(
                        {
                            "payload_kind": "input_payload",
                            "payload_id": payload_id,
                            "payload_version": 1,
                        }
                    ),
                    now_ms,
                ),
            )

    # Block: Settings event write
    def _insert_settings_override_events(
        self,
        *,
        connection: sqlite3.Connection,
        override_id: str,
        cycle_id: str,
        key: str,
        apply_scope: str,
        final_status: str,
        reject_reason: str | None,
        resolved_at: int,
    ) -> list[str]:
        summary = f"settings {key} {final_status} ({apply_scope})"
        if reject_reason:
            summary = f"{summary}: {reject_reason}"
        return [
            self._insert_event(
                connection=connection,
                cycle_id=cycle_id,
                created_at=resolved_at,
                source="runtime",
                kind="internal_decision",
                searchable=True,
                result_summary=summary,
                payload_ref_json=_json_text(
                    {
                        "payload_kind": "input_payload",
                        "payload_id": override_id,
                        "payload_version": 1,
                    }
                ),
                input_journal_refs_json=_json_text([f"obs_{override_id}"]),
            )
        ]

    # Block: Pending input event write
    def _insert_pending_input_events(
        self,
        *,
        connection: sqlite3.Connection,
        pending_input: PendingInputRecord,
        cycle_id: str,
        action_results: list[ActionHistoryRecord],
        ui_events: list[dict[str, Any]],
        resolved_at: int,
    ) -> list[str]:
        input_journal_refs_json = _json_text([f"obs_{pending_input.input_id}"])
        event_ids = [
            self._insert_event(
                connection=connection,
                cycle_id=cycle_id,
                created_at=pending_input.created_at,
                source=normalize_observation_source(
                    source=pending_input.source,
                    payload=pending_input.payload,
                ),
                kind="observation",
                searchable=True,
                observation_summary=_pending_input_receipt_summary(pending_input),
                payload_ref_json=_json_text(
                    {
                        "payload_kind": "input_payload",
                        "payload_id": pending_input.input_id,
                        "payload_version": 1,
                    }
                ),
                input_journal_refs_json=input_journal_refs_json,
            )
        ]
        event_ids.extend(
            self._insert_action_history(
                connection=connection,
                cycle_id=cycle_id,
                action_results=action_results,
                input_journal_refs_json=input_journal_refs_json,
            )
        )
        response_summary = _runtime_response_summary(ui_events)
        if response_summary is None:
            return event_ids
        response_created_at = resolved_at
        if action_results:
            response_created_at = max(action_result.finished_at for action_result in action_results) + 1
        event_ids.append(
            self._insert_event(
                connection=connection,
                cycle_id=cycle_id,
                created_at=response_created_at,
                source="runtime",
                kind="external_response",
                searchable=True,
                result_summary=response_summary,
                input_journal_refs_json=input_journal_refs_json,
            )
        )
        return event_ids

    # Block: Action history write
    def _insert_action_history(
        self,
        *,
        connection: sqlite3.Connection,
        cycle_id: str,
        action_results: list[ActionHistoryRecord],
        input_journal_refs_json: str | None,
    ) -> list[str]:
        event_ids: list[str] = []
        for action_result in action_results:
            if action_result.status not in {"succeeded", "failed", "stopped"}:
                raise StoreValidationError("action status is invalid")
            connection.execute(
                """
                INSERT INTO action_history (
                    result_id,
                    cycle_id,
                    command_id,
                    action_type,
                    command_json,
                    started_at,
                    finished_at,
                    status,
                    failure_mode,
                    observed_effects_json,
                    raw_result_ref_json,
                    adapter_trace_ref_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_result.result_id,
                    cycle_id,
                    action_result.command_id,
                    action_result.action_type,
                    _json_text(action_result.command),
                    action_result.started_at,
                    action_result.finished_at,
                    action_result.status,
                    action_result.failure_mode,
                    (
                        _json_text(action_result.observed_effects)
                        if action_result.observed_effects is not None
                        else None
                    ),
                    (
                        _json_text(action_result.raw_result_ref)
                        if action_result.raw_result_ref is not None
                        else None
                    ),
                    (
                        _json_text(action_result.adapter_trace_ref)
                        if action_result.adapter_trace_ref is not None
                        else None
                    ),
                ),
            )
            event_ids.append(
                self._insert_event(
                    connection=connection,
                    cycle_id=cycle_id,
                    created_at=action_result.started_at,
                    source="runtime",
                    kind="action",
                    searchable=True,
                    action_summary=_action_command_summary(action_result),
                    input_journal_refs_json=input_journal_refs_json,
                )
            )
            event_ids.append(
                self._insert_event(
                    connection=connection,
                    cycle_id=cycle_id,
                    created_at=action_result.finished_at,
                    source="runtime",
                    kind="action_result",
                    searchable=True,
                    result_summary=_action_result_summary(action_result),
                    input_journal_refs_json=input_journal_refs_json,
                )
            )
        return event_ids

    # Block: Event insert
    def _insert_event(
        self,
        *,
        connection: sqlite3.Connection,
        cycle_id: str,
        created_at: int,
        source: str,
        kind: str,
        searchable: bool,
        observation_summary: str | None = None,
        action_summary: str | None = None,
        result_summary: str | None = None,
        payload_ref_json: str | None = None,
        input_journal_refs_json: str | None = None,
    ) -> str:
        event_id = _opaque_id("evt")
        connection.execute(
            """
            INSERT INTO events (
                event_id,
                cycle_id,
                created_at,
                source,
                kind,
                searchable,
                observation_summary,
                action_summary,
                result_summary,
                payload_ref_json,
                input_journal_refs_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                cycle_id,
                created_at,
                source,
                kind,
                1 if searchable else 0,
                observation_summary,
                action_summary,
                result_summary,
                payload_ref_json,
                input_journal_refs_json,
            ),
        )
        return event_id

    # Block: Commit log replay
    def sync_pending_commit_logs(self, *, max_commits: int = 8) -> int:
        if isinstance(max_commits, bool) or not isinstance(max_commits, int):
            raise StoreValidationError("max_commits must be integer")
        if max_commits <= 0:
            raise StoreValidationError("max_commits must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT commit_id
                FROM commit_records
                WHERE log_sync_status IN ('pending', 'needs_replay')
                ORDER BY committed_at ASC, commit_id ASC
                LIMIT ?
                """,
                (max_commits,),
            ).fetchall()
        synced_count = 0
        for row in rows:
            if self.sync_commit_log(commit_id=int(row["commit_id"])):
                synced_count += 1
        return synced_count

    # Block: Commit log sync
    def sync_commit_log(self, *, commit_id: int) -> bool:
        if isinstance(commit_id, bool) or not isinstance(commit_id, int):
            raise StoreValidationError("commit_id must be integer")
        if commit_id <= 0:
            raise StoreValidationError("commit_id must be positive")
        try:
            if self._events_log_contains_commit_id(commit_id=commit_id):
                self._update_commit_log_sync_status(
                    commit_id=commit_id,
                    status="synced",
                    last_log_sync_error=None,
                )
                return True
            commit_log_entry = self._build_commit_log_entry(commit_id=commit_id)
            self._append_commit_log_entry(commit_log_entry=commit_log_entry)
            self._update_commit_log_sync_status(
                commit_id=commit_id,
                status="synced",
                last_log_sync_error=None,
            )
            return True
        except Exception as error:
            self._update_commit_log_sync_status(
                commit_id=commit_id,
                status="needs_replay",
                last_log_sync_error=_commit_log_sync_error_text(error),
            )
            return False

    # Block: Commit log entry build
    def _build_commit_log_entry(self, *, commit_id: int) -> dict[str, Any]:
        with self._connect() as connection:
            commit_row = connection.execute(
                """
                SELECT commit_id, cycle_id, committed_at, commit_payload_json
                FROM commit_records
                WHERE commit_id = ?
                """,
                (commit_id,),
            ).fetchone()
            if commit_row is None:
                raise RuntimeError("commit_record is missing")
            commit_payload = json.loads(commit_row["commit_payload_json"])
            event_ids = commit_payload.get("event_ids", [])
            if not isinstance(event_ids, list):
                raise RuntimeError("commit_payload_json.event_ids must be a list")
            event_rows = (
                _fetch_events_for_ids(connection=connection, event_ids=[str(event_id) for event_id in event_ids])
                if event_ids
                else []
            )
        return {
            "commit_id": int(commit_row["commit_id"]),
            "cycle_id": str(commit_row["cycle_id"]),
            "committed_at": int(commit_row["committed_at"]),
            "commit_payload": commit_payload,
            "events": [_event_log_entry(row) for row in event_rows],
        }

    # Block: Commit log append
    def _append_commit_log_entry(self, *, commit_log_entry: dict[str, Any]) -> None:
        events_log_path = self._events_log_path()
        events_log_path.parent.mkdir(parents=True, exist_ok=True)
        with events_log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(_json_text(commit_log_entry))
            log_file.write("\n")

    # Block: Commit id scan
    def _events_log_contains_commit_id(self, *, commit_id: int) -> bool:
        events_log_path = self._events_log_path()
        if not events_log_path.exists():
            return False
        with events_log_path.open("r", encoding="utf-8") as log_file:
            for line in log_file:
                stripped_line = line.strip()
                if not stripped_line:
                    continue
                log_entry = json.loads(stripped_line)
                if not isinstance(log_entry, dict):
                    raise RuntimeError("events.jsonl entry must be an object")
                logged_commit_id = log_entry.get("commit_id")
                if isinstance(logged_commit_id, int) and logged_commit_id == commit_id:
                    return True
        return False

    # Block: Commit log status update
    def _update_commit_log_sync_status(
        self,
        *,
        commit_id: int,
        status: str,
        last_log_sync_error: str | None,
    ) -> None:
        if status not in {"synced", "needs_replay"}:
            raise StoreValidationError("status is invalid")
        with self._connect() as connection:
            updated_row_count = connection.execute(
                """
                UPDATE commit_records
                SET log_sync_status = ?,
                    last_log_sync_error = ?
                WHERE commit_id = ?
                """,
                (status, last_log_sync_error, commit_id),
            ).rowcount
        if updated_row_count != 1:
            raise RuntimeError("commit_record must exist before log sync update")

    # Block: Events log path
    def _events_log_path(self) -> Path:
        return self._db_path.parent / "events.jsonl"

    # Block: SQLite connection
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
        return connection

    # Block: Schema existence
    def _schema_exists(self, connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'db_meta'
            """
        ).fetchone()
        return row is not None

    # Block: Schema file load
    def _schema_sql(self) -> str:
        schema_path = _repo_root() / "sql" / "core_schema.sql"
        return schema_path.read_text(encoding="utf-8")

    # Block: Schema migration
    def _migrate_schema(self, connection: sqlite3.Connection, now_ms: int) -> None:
        current_version = self._read_schema_version(connection)
        if current_version is None:
            return
        if current_version == SCHEMA_VERSION:
            return
        if current_version > SCHEMA_VERSION:
            raise RuntimeError("schema_version is newer than this initializer")
        if current_version not in {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17}:
            raise RuntimeError("unsupported schema_version for migration")
        while current_version < SCHEMA_VERSION:
            if current_version == 2:
                self._migrate_schema_2_to_3(connection=connection, now_ms=now_ms)
                current_version = 3
                continue
            if current_version == 3:
                self._migrate_schema_3_to_4(connection=connection, now_ms=now_ms)
                current_version = 4
                continue
            if current_version == 4:
                self._migrate_schema_4_to_5(connection=connection, now_ms=now_ms)
                current_version = 5
                continue
            if current_version == 5:
                self._migrate_schema_5_to_6(connection=connection, now_ms=now_ms)
                current_version = 6
                continue
            if current_version == 6:
                self._migrate_schema_6_to_7(connection=connection, now_ms=now_ms)
                current_version = 7
                continue
            if current_version == 7:
                self._migrate_schema_7_to_8(connection=connection, now_ms=now_ms)
                current_version = 8
                continue
            if current_version == 8:
                self._migrate_schema_8_to_9(connection=connection, now_ms=now_ms)
                current_version = 9
                continue
            if current_version == 9:
                self._migrate_schema_9_to_10(connection=connection, now_ms=now_ms)
                current_version = 10
                continue
            if current_version == 10:
                self._migrate_schema_10_to_11(connection=connection, now_ms=now_ms)
                current_version = 11
                continue
            if current_version == 11:
                self._migrate_schema_11_to_12(connection=connection, now_ms=now_ms)
                current_version = 12
                continue
            if current_version == 12:
                self._migrate_schema_12_to_13(connection=connection, now_ms=now_ms)
                current_version = 13
                continue
            if current_version == 13:
                self._migrate_schema_13_to_14(connection=connection, now_ms=now_ms)
                current_version = 14
                continue
            if current_version == 14:
                self._migrate_schema_14_to_15(connection=connection, now_ms=now_ms)
                current_version = 15
                continue
            if current_version == 15:
                self._migrate_schema_15_to_16(connection=connection, now_ms=now_ms)
                current_version = 16
                continue
            if current_version == 16:
                self._migrate_schema_16_to_17(connection=connection, now_ms=now_ms)
                current_version = 17
                continue
            if current_version == 17:
                self._migrate_schema_17_to_18(connection=connection, now_ms=now_ms)
                current_version = 18
                continue
            raise RuntimeError("unsupported schema_version for migration")

    # Block: Schema migration 2->3
    def _migrate_schema_2_to_3(self, *, connection: sqlite3.Connection, now_ms: int) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_settings (
                row_id INTEGER PRIMARY KEY CHECK (row_id = 1),
                values_json TEXT NOT NULL,
                value_updated_at_json TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO runtime_settings (
                row_id,
                values_json,
                value_updated_at_json,
                updated_at
            )
            VALUES (1, ?, ?, ?)
            ON CONFLICT(row_id) DO NOTHING
            """,
            (_json_text({}), _json_text({}), now_ms),
        )
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?,
                updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (_json_text(3), now_ms),
        )

    # Block: Schema migration 3->4
    def _migrate_schema_3_to_4(self, *, connection: sqlite3.Connection, now_ms: int) -> None:
        self._ensure_vec_index_schema(connection=connection)
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?,
                updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (_json_text(4), now_ms),
        )

    # Block: Schema migration 4->5
    def _migrate_schema_4_to_5(self, *, connection: sqlite3.Connection, now_ms: int) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS settings_editor_state (
                row_id INTEGER PRIMARY KEY CHECK (row_id = 1),
                active_behavior_preset_id TEXT NOT NULL,
                active_llm_preset_id TEXT NOT NULL,
                active_memory_preset_id TEXT NOT NULL,
                active_output_preset_id TEXT NOT NULL,
                direct_values_json TEXT NOT NULL,
                revision INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_applied_change_set_id TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS settings_presets (
                preset_id TEXT PRIMARY KEY,
                preset_kind TEXT NOT NULL CHECK (
                    preset_kind IN ('behavior', 'llm', 'memory', 'output')
                ),
                preset_name TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                archived INTEGER NOT NULL CHECK (archived IN (0, 1)),
                sort_order INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_settings_presets_kind_archived_sort
                ON settings_presets (preset_kind, archived, sort_order ASC, updated_at DESC)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS settings_change_sets (
                change_set_id TEXT PRIMARY KEY,
                editor_revision INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('queued', 'claimed', 'applied', 'rejected')
                ),
                claimed_at INTEGER,
                resolved_at INTEGER,
                reject_reason TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_settings_change_sets_status_created
                ON settings_change_sets (status, created_at ASC)
            """
        )
        self._ensure_runtime_settings_defaults(
            connection=connection,
            now_ms=now_ms,
        )
        self._ensure_settings_editor_defaults_v5(
            connection=connection,
            now_ms=now_ms,
        )
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?,
                updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (_json_text(5), now_ms),
        )

    # Block: Schema migration 5->6
    def _migrate_schema_5_to_6(self, *, connection: sqlite3.Connection, now_ms: int) -> None:
        runtime_row = connection.execute(
            """
            SELECT values_json, value_updated_at_json
            FROM runtime_settings
            WHERE row_id = 1
            """
        ).fetchone()
        if runtime_row is None:
            raise RuntimeError("runtime_settings row is missing")
        default_settings = build_default_settings()
        normalized_values = _normalize_runtime_settings_values(
            default_settings=default_settings,
            runtime_values=json.loads(runtime_row["values_json"]),
        )
        normalized_updated_at = _normalize_runtime_settings_updated_at(
            default_settings=default_settings,
            current_updated_at=json.loads(runtime_row["value_updated_at_json"]),
            now_ms=now_ms,
        )
        connection.execute(
            """
            UPDATE runtime_settings
            SET values_json = ?,
                value_updated_at_json = ?,
                updated_at = ?
            WHERE row_id = 1
            """,
            (
                _json_text(normalized_values),
                _json_text(normalized_updated_at),
                now_ms,
            ),
        )
        editor_row = connection.execute(
            """
            SELECT direct_values_json, revision
            FROM settings_editor_state
            WHERE row_id = 1
            """
        ).fetchone()
        editor_seed = _legacy_settings_editor_state_seed_v5(default_settings)
        editor_system_values = dict(editor_seed["system_values_json"])
        editor_revision = int(editor_seed["revision"])
        if editor_row is not None:
            legacy_direct_values = json.loads(editor_row["direct_values_json"])
            if isinstance(legacy_direct_values, dict):
                for key in build_settings_editor_system_keys():
                    if key in legacy_direct_values:
                        editor_system_values[key] = legacy_direct_values[key]
            editor_revision = int(editor_row["revision"]) + 1
            connection.execute(
                """
                UPDATE settings_editor_state
                SET active_behavior_preset_id = ?,
                    active_llm_preset_id = ?,
                    active_memory_preset_id = ?,
                    active_output_preset_id = ?,
                    direct_values_json = ?,
                    revision = ?,
                    updated_at = ?,
                    last_applied_change_set_id = NULL
                WHERE row_id = 1
                """,
                (
                    editor_seed["active_behavior_preset_id"],
                    editor_seed["active_llm_preset_id"],
                    editor_seed["active_memory_preset_id"],
                    editor_seed["active_output_preset_id"],
                    _json_text(editor_system_values),
                    editor_revision,
                    now_ms,
                ),
            )
        connection.execute("DELETE FROM settings_presets")
        connection.execute("DELETE FROM settings_change_sets")
        preset_seeds = _legacy_settings_preset_seeds_from_defaults(
            _merge_runtime_settings(default_settings, normalized_values),
        )
        for index, preset_seed in enumerate(preset_seeds):
            connection.execute(
                """
                INSERT INTO settings_presets (
                    preset_id,
                    preset_kind,
                    preset_name,
                    payload_json,
                    archived,
                    sort_order,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    preset_seed["preset_id"],
                    preset_seed["preset_kind"],
                    preset_seed["preset_name"],
                    _json_text(preset_seed["payload"]),
                    (index + 1) * 10,
                    now_ms,
                    now_ms,
                ),
            )
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?,
                updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (_json_text(6), now_ms),
        )

    # Block: Schema migration 6->7
    def _migrate_schema_6_to_7(self, *, connection: sqlite3.Connection, now_ms: int) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS camera_connections (
                camera_connection_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                host TEXT NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_camera_connections_sort
                ON camera_connections (sort_order ASC, updated_at DESC)
            """
        )
        self._ensure_runtime_settings_defaults(
            connection=connection,
            now_ms=now_ms,
        )
        connection.execute("DELETE FROM settings_change_sets")
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?,
                updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (_json_text(7), now_ms),
        )

    # Block: Schema migration 7->8
    def _migrate_schema_7_to_8(self, *, connection: sqlite3.Connection, now_ms: int) -> None:
        default_settings = build_default_settings()
        preset_seed_catalogs = build_default_settings_editor_presets(default_settings)
        legacy_editor_row = connection.execute(
            """
            SELECT *
            FROM settings_editor_state
            WHERE row_id = 1
            """
        ).fetchone()
        legacy_preset_rows = connection.execute(
            """
            SELECT
                preset_id,
                preset_kind,
                preset_name,
                payload_json,
                archived,
                sort_order,
                created_at,
                updated_at
            FROM settings_presets
            ORDER BY preset_kind ASC, sort_order ASC, updated_at DESC
            """
        ).fetchall()
        legacy_presets_by_kind = {
            "behavior": [],
            "llm": [],
            "memory": [],
            "output": [],
        }
        for legacy_preset_row in legacy_preset_rows:
            legacy_presets_by_kind[str(legacy_preset_row["preset_kind"])].append(legacy_preset_row)
        temporary_editor_table = f"settings_editor_state_v7_{uuid.uuid4().hex}"
        connection.execute(f"ALTER TABLE settings_editor_state RENAME TO {temporary_editor_table}")
        connection.execute("DROP TABLE settings_presets")
        connection.execute("DROP INDEX IF EXISTS idx_settings_presets_kind_archived_sort")
        connection.execute(
            """
            CREATE TABLE settings_editor_state (
                row_id INTEGER PRIMARY KEY CHECK (row_id = 1),
                active_character_preset_id TEXT NOT NULL,
                active_behavior_preset_id TEXT NOT NULL,
                active_conversation_preset_id TEXT NOT NULL,
                active_memory_preset_id TEXT NOT NULL,
                active_motion_preset_id TEXT NOT NULL,
                active_camera_connection_id TEXT,
                system_values_json TEXT NOT NULL,
                revision INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_applied_change_set_id TEXT
            )
            """
        )
        for table_name in SETTINGS_EDITOR_PRESET_TABLE_NAMES:
            connection.execute(
                f"""
                CREATE TABLE {table_name} (
                    preset_id TEXT PRIMARY KEY,
                    preset_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    archived INTEGER NOT NULL CHECK (archived IN (0, 1)),
                    sort_order INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                f"""
                CREATE INDEX idx_{table_name}_archived_sort
                    ON {table_name} (archived, sort_order ASC, updated_at DESC)
                """
            )
        active_output_payload = _active_legacy_output_payload_for_migration(
            legacy_presets_by_kind=legacy_presets_by_kind,
            active_output_preset_id=(
                str(legacy_editor_row["active_output_preset_id"])
                if legacy_editor_row is not None
                else None
            ),
            default_settings=default_settings,
        )
        system_values = _build_v8_system_values_from_v7_row(
            legacy_editor_row=legacy_editor_row,
            active_output_payload=active_output_payload,
            default_settings=default_settings,
        )
        editor_seed = build_default_settings_editor_state(default_settings)
        migrated_editor_state = {
            "active_character_preset_id": (
                str(legacy_editor_row["active_output_preset_id"])
                if legacy_editor_row is not None
                else editor_seed["active_character_preset_id"]
            ),
            "active_behavior_preset_id": (
                str(legacy_editor_row["active_behavior_preset_id"])
                if legacy_editor_row is not None
                else editor_seed["active_behavior_preset_id"]
            ),
            "active_conversation_preset_id": (
                str(legacy_editor_row["active_llm_preset_id"])
                if legacy_editor_row is not None
                else editor_seed["active_conversation_preset_id"]
            ),
            "active_memory_preset_id": (
                str(legacy_editor_row["active_memory_preset_id"])
                if legacy_editor_row is not None
                else editor_seed["active_memory_preset_id"]
            ),
            "active_motion_preset_id": editor_seed["active_motion_preset_id"],
            "active_camera_connection_id": (
                str(legacy_editor_row["active_camera_connection_id"])
                if legacy_editor_row is not None and legacy_editor_row["active_camera_connection_id"] is not None
                else None
            ),
            "system_values_json": system_values,
            "revision": int(legacy_editor_row["revision"]) if legacy_editor_row is not None else 1,
            "updated_at": int(legacy_editor_row["updated_at"]) if legacy_editor_row is not None else now_ms,
            "last_applied_change_set_id": (
                str(legacy_editor_row["last_applied_change_set_id"])
                if legacy_editor_row is not None and legacy_editor_row["last_applied_change_set_id"] is not None
                else None
            ),
        }
        connection.execute(
            """
            INSERT INTO settings_editor_state (
                row_id,
                active_character_preset_id,
                active_behavior_preset_id,
                active_conversation_preset_id,
                active_memory_preset_id,
                active_motion_preset_id,
                active_camera_connection_id,
                system_values_json,
                revision,
                updated_at,
                last_applied_change_set_id
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                migrated_editor_state["active_character_preset_id"],
                migrated_editor_state["active_behavior_preset_id"],
                migrated_editor_state["active_conversation_preset_id"],
                migrated_editor_state["active_memory_preset_id"],
                migrated_editor_state["active_motion_preset_id"],
                migrated_editor_state["active_camera_connection_id"],
                _json_text(migrated_editor_state["system_values_json"]),
                migrated_editor_state["revision"],
                migrated_editor_state["updated_at"],
                migrated_editor_state["last_applied_change_set_id"],
            ),
        )
        _insert_migrated_editor_presets(
            connection=connection,
            table_name="character_presets",
            preset_rows=legacy_presets_by_kind["output"],
            payload_builder=lambda payload: _migrate_output_payload_to_character_payload(
                legacy_payload=payload,
                default_settings=default_settings,
            ),
            fallback_entries=preset_seed_catalogs["character_presets"],
            now_ms=now_ms,
        )
        _insert_migrated_editor_presets(
            connection=connection,
            table_name="behavior_presets",
            preset_rows=legacy_presets_by_kind["behavior"],
            payload_builder=lambda payload: _migrate_behavior_payload_to_v8(
                legacy_payload=payload,
                default_settings=default_settings,
            ),
            fallback_entries=preset_seed_catalogs["behavior_presets"],
            now_ms=now_ms,
        )
        _insert_migrated_editor_presets(
            connection=connection,
            table_name="conversation_presets",
            preset_rows=legacy_presets_by_kind["llm"],
            payload_builder=lambda payload: _migrate_llm_payload_to_conversation_payload(
                legacy_payload=payload,
                default_settings=default_settings,
            ),
            fallback_entries=preset_seed_catalogs["conversation_presets"],
            now_ms=now_ms,
        )
        _insert_migrated_editor_presets(
            connection=connection,
            table_name="memory_presets",
            preset_rows=legacy_presets_by_kind["memory"],
            payload_builder=lambda payload: _migrate_memory_payload_to_v8(
                legacy_payload=payload,
                default_settings=default_settings,
            ),
            fallback_entries=preset_seed_catalogs["memory_presets"],
            now_ms=now_ms,
        )
        _insert_migrated_editor_presets(
            connection=connection,
            table_name="motion_presets",
            preset_rows=[],
            payload_builder=lambda payload: payload,
            fallback_entries=preset_seed_catalogs["motion_presets"],
            now_ms=now_ms,
        )
        connection.execute(f"DROP TABLE {temporary_editor_table}")
        connection.execute("DELETE FROM settings_change_sets")
        self._ensure_settings_editor_defaults(connection=connection, now_ms=now_ms)
        effective_settings = _materialize_effective_settings_from_editor(
            default_settings=default_settings,
            editor_state=_decode_settings_editor_state_row(
                connection.execute(
                    """
                    SELECT
                        active_character_preset_id,
                        active_behavior_preset_id,
                        active_conversation_preset_id,
                        active_memory_preset_id,
                        active_motion_preset_id,
                        active_camera_connection_id,
                        system_values_json,
                        revision,
                        updated_at,
                        last_applied_change_set_id
                    FROM settings_editor_state
                    WHERE row_id = 1
                    """
                ).fetchone()
            ),
            character_presets=_decode_settings_preset_rows(
                _fetch_editor_preset_rows(connection=connection, table_name="character_presets")
            ),
            behavior_presets=_decode_settings_preset_rows(
                _fetch_editor_preset_rows(connection=connection, table_name="behavior_presets")
            ),
            conversation_presets=_decode_settings_preset_rows(
                _fetch_editor_preset_rows(connection=connection, table_name="conversation_presets")
            ),
            memory_presets=_decode_settings_preset_rows(
                _fetch_editor_preset_rows(connection=connection, table_name="memory_presets")
            ),
            motion_presets=_decode_settings_preset_rows(
                _fetch_editor_preset_rows(connection=connection, table_name="motion_presets")
            ),
        )
        connection.execute(
            """
            UPDATE runtime_settings
            SET values_json = ?,
                value_updated_at_json = ?,
                updated_at = ?
            WHERE row_id = 1
            """,
            (
                _json_text(effective_settings),
                _json_text({key: now_ms for key in effective_settings}),
                now_ms,
            ),
        )
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?,
                updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (_json_text(8), now_ms),
        )

    # Block: Schema migration 8->9
    def _migrate_schema_8_to_9(self, *, connection: sqlite3.Connection, now_ms: int) -> None:
        legacy_editor_row = connection.execute(
            """
            SELECT *
            FROM settings_editor_state
            WHERE row_id = 1
            """
        ).fetchone()
        if legacy_editor_row is None:
            raise RuntimeError("settings_editor_state row is missing")
        active_camera_connection_id = (
            str(legacy_editor_row["active_camera_connection_id"])
            if legacy_editor_row["active_camera_connection_id"] is not None
            else None
        )
        temporary_editor_table = f"settings_editor_state_v8_{uuid.uuid4().hex}"
        connection.execute(f"ALTER TABLE settings_editor_state RENAME TO {temporary_editor_table}")
        connection.execute(
            """
            CREATE TABLE settings_editor_state (
                row_id INTEGER PRIMARY KEY CHECK (row_id = 1),
                active_character_preset_id TEXT NOT NULL,
                active_behavior_preset_id TEXT NOT NULL,
                active_conversation_preset_id TEXT NOT NULL,
                active_memory_preset_id TEXT NOT NULL,
                active_motion_preset_id TEXT NOT NULL,
                system_values_json TEXT NOT NULL,
                revision INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_applied_change_set_id TEXT
            )
            """
        )
        connection.execute(
            f"""
            INSERT INTO settings_editor_state (
                row_id,
                active_character_preset_id,
                active_behavior_preset_id,
                active_conversation_preset_id,
                active_memory_preset_id,
                active_motion_preset_id,
                system_values_json,
                revision,
                updated_at,
                last_applied_change_set_id
            )
            SELECT
                row_id,
                active_character_preset_id,
                active_behavior_preset_id,
                active_conversation_preset_id,
                active_memory_preset_id,
                active_motion_preset_id,
                system_values_json,
                revision,
                updated_at,
                last_applied_change_set_id
            FROM {temporary_editor_table}
            """
        )
        connection.execute(f"DROP TABLE {temporary_editor_table}")
        temporary_camera_table = f"camera_connections_v8_{uuid.uuid4().hex}"
        connection.execute("DROP INDEX IF EXISTS idx_camera_connections_sort")
        connection.execute(f"ALTER TABLE camera_connections RENAME TO {temporary_camera_table}")
        connection.execute(
            """
            CREATE TABLE camera_connections (
                camera_connection_id TEXT PRIMARY KEY,
                is_enabled INTEGER NOT NULL CHECK (is_enabled IN (0, 1)),
                display_name TEXT NOT NULL,
                host TEXT NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_camera_connections_sort
                ON camera_connections (sort_order ASC, updated_at DESC)
            """
        )
        legacy_camera_rows = connection.execute(
            f"""
            SELECT
                camera_connection_id,
                display_name,
                host,
                username,
                password,
                sort_order,
                created_at,
                updated_at
            FROM {temporary_camera_table}
            ORDER BY sort_order ASC, updated_at DESC
            """
        ).fetchall()
        for legacy_camera_row in legacy_camera_rows:
            has_credentials = (
                bool(str(legacy_camera_row["host"]).strip())
                and bool(str(legacy_camera_row["username"]).strip())
                and bool(str(legacy_camera_row["password"]).strip())
            )
            is_enabled = (
                active_camera_connection_id is not None
                and str(legacy_camera_row["camera_connection_id"]) == active_camera_connection_id
                and has_credentials is True
            )
            connection.execute(
                """
                INSERT INTO camera_connections (
                    camera_connection_id,
                    is_enabled,
                    display_name,
                    host,
                    username,
                    password,
                    sort_order,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(legacy_camera_row["camera_connection_id"]),
                    1 if is_enabled else 0,
                    str(legacy_camera_row["display_name"]),
                    str(legacy_camera_row["host"]),
                    str(legacy_camera_row["username"]),
                    str(legacy_camera_row["password"]),
                    int(legacy_camera_row["sort_order"]),
                    int(legacy_camera_row["created_at"]),
                    int(legacy_camera_row["updated_at"]),
                ),
            )
        connection.execute(f"DROP TABLE {temporary_camera_table}")
        connection.execute("DELETE FROM settings_change_sets")
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?,
                updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (_json_text(9), now_ms),
        )

    # Block: Schema migration 9->10
    def _migrate_schema_9_to_10(self, *, connection: sqlite3.Connection, now_ms: int) -> None:
        idle_task_row = connection.execute(
            """
            SELECT task_id
            FROM task_state
            WHERE task_status = 'idle'
            LIMIT 1
            """
        ).fetchone()
        if idle_task_row is not None:
            raise RuntimeError("task_state rows with task_status=idle are not supported in schema v10")
        temporary_task_table = f"task_state_v10_{uuid.uuid4().hex}"
        connection.execute("DROP INDEX IF EXISTS idx_task_state_status_priority_updated")
        connection.execute(f"ALTER TABLE task_state RENAME TO {temporary_task_table}")
        connection.execute(
            """
            CREATE TABLE task_state (
                task_id TEXT PRIMARY KEY,
                task_kind TEXT NOT NULL,
                task_status TEXT NOT NULL CHECK (
                    task_status IN (
                        'active',
                        'waiting_external',
                        'paused',
                        'completed',
                        'abandoned'
                    )
                ),
                goal_hint TEXT NOT NULL,
                completion_hint_json TEXT NOT NULL,
                resume_condition_json TEXT NOT NULL,
                interruptible INTEGER NOT NULL CHECK (interruptible IN (0, 1)),
                priority INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                title TEXT,
                step_hints_json TEXT,
                deadline_at INTEGER,
                abandon_reason TEXT
            )
            """
        )
        connection.execute(
            f"""
            INSERT INTO task_state (
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
                step_hints_json,
                deadline_at,
                abandon_reason
            )
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
                step_hints_json,
                deadline_at,
                abandon_reason
            FROM {temporary_task_table}
            """
        )
        connection.execute(f"DROP TABLE {temporary_task_table}")
        connection.execute(
            """
            CREATE INDEX idx_task_state_status_priority_updated
                ON task_state (task_status, priority DESC, updated_at DESC)
            """
        )
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?,
                updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (_json_text(10), now_ms),
        )

    # Block: Schema migration 10->11
    def _migrate_schema_10_to_11(self, *, connection: sqlite3.Connection, now_ms: int) -> None:
        legacy_editor_row = connection.execute(
            """
            SELECT *
            FROM settings_editor_state
            WHERE row_id = 1
            """
        ).fetchone()
        if legacy_editor_row is None:
            raise RuntimeError("settings_editor_state row is missing")
        legacy_camera_rows = connection.execute(
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
        active_camera_connection_id = None
        for legacy_camera_row in legacy_camera_rows:
            if bool(legacy_camera_row["is_enabled"]) is True:
                active_camera_connection_id = str(legacy_camera_row["camera_connection_id"])
                break
        temporary_editor_table = f"settings_editor_state_v10_{uuid.uuid4().hex}"
        connection.execute(f"ALTER TABLE settings_editor_state RENAME TO {temporary_editor_table}")
        connection.execute(
            """
            CREATE TABLE settings_editor_state (
                row_id INTEGER PRIMARY KEY CHECK (row_id = 1),
                active_character_preset_id TEXT NOT NULL,
                active_behavior_preset_id TEXT NOT NULL,
                active_conversation_preset_id TEXT NOT NULL,
                active_memory_preset_id TEXT NOT NULL,
                active_motion_preset_id TEXT NOT NULL,
                active_camera_connection_id TEXT,
                system_values_json TEXT NOT NULL,
                revision INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO settings_editor_state (
                row_id,
                active_character_preset_id,
                active_behavior_preset_id,
                active_conversation_preset_id,
                active_memory_preset_id,
                active_motion_preset_id,
                active_camera_connection_id,
                system_values_json,
                revision,
                updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(legacy_editor_row["active_character_preset_id"]),
                str(legacy_editor_row["active_behavior_preset_id"]),
                str(legacy_editor_row["active_conversation_preset_id"]),
                str(legacy_editor_row["active_memory_preset_id"]),
                str(legacy_editor_row["active_motion_preset_id"]),
                active_camera_connection_id,
                str(legacy_editor_row["system_values_json"]),
                int(legacy_editor_row["revision"]),
                int(legacy_editor_row["updated_at"]),
            ),
        )
        connection.execute(f"DROP TABLE {temporary_editor_table}")
        temporary_camera_table = f"camera_connections_v10_{uuid.uuid4().hex}"
        connection.execute("DROP INDEX IF EXISTS idx_camera_connections_sort")
        connection.execute(f"ALTER TABLE camera_connections RENAME TO {temporary_camera_table}")
        connection.execute(
            """
            CREATE TABLE camera_connections (
                camera_connection_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                host TEXT NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_camera_connections_sort
                ON camera_connections (sort_order ASC, updated_at DESC)
            """
        )
        connection.execute(
            f"""
            INSERT INTO camera_connections (
                camera_connection_id,
                display_name,
                host,
                username,
                password,
                sort_order,
                created_at,
                updated_at
            )
            SELECT
                camera_connection_id,
                display_name,
                host,
                username,
                password,
                sort_order,
                created_at,
                updated_at
            FROM {temporary_camera_table}
            """
        )
        connection.execute(f"DROP TABLE {temporary_camera_table}")
        connection.execute("DELETE FROM settings_change_sets")
        runtime_values = _materialize_effective_settings_from_editor(
            default_settings=build_default_settings(),
            editor_state=_decode_settings_editor_state_row(
                connection.execute(
                    """
                    SELECT
                        active_character_preset_id,
                        active_behavior_preset_id,
                        active_conversation_preset_id,
                        active_memory_preset_id,
                        active_motion_preset_id,
                        active_camera_connection_id,
                        system_values_json,
                        revision,
                        updated_at
                    FROM settings_editor_state
                    WHERE row_id = 1
                    """
                ).fetchone()
            ),
            character_presets=_decode_settings_preset_rows(
                _fetch_editor_preset_rows(connection=connection, table_name="character_presets")
            ),
            behavior_presets=_decode_settings_preset_rows(
                _fetch_editor_preset_rows(connection=connection, table_name="behavior_presets")
            ),
            conversation_presets=_decode_settings_preset_rows(
                _fetch_editor_preset_rows(connection=connection, table_name="conversation_presets")
            ),
            memory_presets=_decode_settings_preset_rows(
                _fetch_editor_preset_rows(connection=connection, table_name="memory_presets")
            ),
            motion_presets=_decode_settings_preset_rows(
                _fetch_editor_preset_rows(connection=connection, table_name="motion_presets")
            ),
        )
        connection.execute(
            """
            UPDATE runtime_settings
            SET values_json = ?,
                value_updated_at_json = ?,
                updated_at = ?
            WHERE row_id = 1
            """,
            (
                _json_text(runtime_values),
                _json_text({key: now_ms for key in runtime_values}),
                now_ms,
            ),
        )
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?,
                updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (_json_text(11), now_ms),
        )

    # Block: Schema migration 11->12
    def _migrate_schema_11_to_12(self, *, connection: sqlite3.Connection, now_ms: int) -> None:
        legacy_editor_row = connection.execute(
            """
            SELECT *
            FROM settings_editor_state
            WHERE row_id = 1
            """
        ).fetchone()
        if legacy_editor_row is None:
            raise RuntimeError("settings_editor_state row is missing")
        active_camera_connection_id = (
            str(legacy_editor_row["active_camera_connection_id"])
            if legacy_editor_row["active_camera_connection_id"] is not None
            else None
        )
        temporary_editor_table = f"settings_editor_state_v11_{uuid.uuid4().hex}"
        connection.execute(f"ALTER TABLE settings_editor_state RENAME TO {temporary_editor_table}")
        connection.execute(
            """
            CREATE TABLE settings_editor_state (
                row_id INTEGER PRIMARY KEY CHECK (row_id = 1),
                active_character_preset_id TEXT NOT NULL,
                active_behavior_preset_id TEXT NOT NULL,
                active_conversation_preset_id TEXT NOT NULL,
                active_memory_preset_id TEXT NOT NULL,
                active_motion_preset_id TEXT NOT NULL,
                system_values_json TEXT NOT NULL,
                revision INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            f"""
            INSERT INTO settings_editor_state (
                row_id,
                active_character_preset_id,
                active_behavior_preset_id,
                active_conversation_preset_id,
                active_memory_preset_id,
                active_motion_preset_id,
                system_values_json,
                revision,
                updated_at
            )
            SELECT
                row_id,
                active_character_preset_id,
                active_behavior_preset_id,
                active_conversation_preset_id,
                active_memory_preset_id,
                active_motion_preset_id,
                system_values_json,
                revision,
                updated_at
            FROM {temporary_editor_table}
            """
        )
        connection.execute(f"DROP TABLE {temporary_editor_table}")
        temporary_camera_table = f"camera_connections_v11_{uuid.uuid4().hex}"
        connection.execute("DROP INDEX IF EXISTS idx_camera_connections_sort")
        connection.execute(f"ALTER TABLE camera_connections RENAME TO {temporary_camera_table}")
        connection.execute(
            """
            CREATE TABLE camera_connections (
                camera_connection_id TEXT PRIMARY KEY,
                is_enabled INTEGER NOT NULL CHECK (is_enabled IN (0, 1)),
                display_name TEXT NOT NULL,
                host TEXT NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_camera_connections_sort
                ON camera_connections (sort_order ASC, updated_at DESC)
            """
        )
        legacy_camera_rows = connection.execute(
            f"""
            SELECT
                camera_connection_id,
                display_name,
                host,
                username,
                password,
                sort_order,
                created_at,
                updated_at
            FROM {temporary_camera_table}
            ORDER BY sort_order ASC, updated_at DESC
            """
        ).fetchall()
        for legacy_camera_row in legacy_camera_rows:
            has_credentials = (
                bool(str(legacy_camera_row["host"]).strip())
                and bool(str(legacy_camera_row["username"]).strip())
                and bool(str(legacy_camera_row["password"]).strip())
            )
            is_enabled = (
                active_camera_connection_id is not None
                and str(legacy_camera_row["camera_connection_id"]) == active_camera_connection_id
                and has_credentials is True
            )
            connection.execute(
                """
                INSERT INTO camera_connections (
                    camera_connection_id,
                    is_enabled,
                    display_name,
                    host,
                    username,
                    password,
                    sort_order,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(legacy_camera_row["camera_connection_id"]),
                    1 if is_enabled else 0,
                    str(legacy_camera_row["display_name"]),
                    str(legacy_camera_row["host"]),
                    str(legacy_camera_row["username"]),
                    str(legacy_camera_row["password"]),
                    int(legacy_camera_row["sort_order"]),
                    int(legacy_camera_row["created_at"]),
                    int(legacy_camera_row["updated_at"]),
                ),
            )
        connection.execute(f"DROP TABLE {temporary_camera_table}")
        connection.execute("DELETE FROM settings_change_sets")
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?,
                updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (_json_text(12), now_ms),
        )

    # Block: Schema migration 12->13
    def _migrate_schema_12_to_13(self, *, connection: sqlite3.Connection, now_ms: int) -> None:
        temporary_ui_event_table = f"ui_outbound_events_v12_{uuid.uuid4().hex}"
        connection.execute("DROP INDEX IF EXISTS idx_ui_outbound_events_channel_event")
        connection.execute(f"ALTER TABLE ui_outbound_events RENAME TO {temporary_ui_event_table}")
        connection.execute(
            """
            CREATE TABLE ui_outbound_events (
                ui_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL CHECK (channel = 'browser_chat'),
                event_type TEXT NOT NULL CHECK (
                    event_type IN ('token', 'message', 'message_end', 'status', 'notice', 'error')
                ),
                payload_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                source_cycle_id TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_ui_outbound_events_channel_event
                ON ui_outbound_events (channel, ui_event_id ASC)
            """
        )
        connection.execute(
            f"""
            INSERT INTO ui_outbound_events (
                ui_event_id,
                channel,
                event_type,
                payload_json,
                created_at,
                source_cycle_id
            )
            SELECT
                ui_event_id,
                channel,
                event_type,
                payload_json,
                created_at,
                source_cycle_id
            FROM {temporary_ui_event_table}
            """
        )
        connection.execute(f"DROP TABLE {temporary_ui_event_table}")
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?,
                updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (_json_text(13), now_ms),
        )

    # Block: Schema migration 13->14
    def _migrate_schema_13_to_14(self, *, connection: sqlite3.Connection, now_ms: int) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS event_about_time (
                event_about_time_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                about_start_ts INTEGER,
                about_end_ts INTEGER,
                about_year_start INTEGER,
                about_year_end INTEGER,
                life_stage TEXT,
                confidence REAL NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                CHECK (
                    about_start_ts IS NOT NULL
                    OR about_end_ts IS NOT NULL
                    OR about_year_start IS NOT NULL
                    OR about_year_end IS NOT NULL
                    OR life_stage IS NOT NULL
                ),
                FOREIGN KEY (event_id) REFERENCES events (event_id)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_event_about_time_event
                ON event_about_time (event_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_event_about_time_year
                ON event_about_time (about_year_start, about_year_end)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_event_about_time_life_stage
                ON event_about_time (life_stage)
            """
        )
        event_ids = [
            str(row["event_id"])
            for row in connection.execute(
                """
                SELECT DISTINCT event_id
                FROM event_entities
                WHERE entity_type_norm IN ('about_year', 'life_stage')
                ORDER BY event_id ASC
                """
            ).fetchall()
        ]
        for event_id in event_ids:
            entity_rows = connection.execute(
                """
                SELECT entity_type_norm, entity_name_raw, confidence
                FROM event_entities
                WHERE event_id = ?
                  AND entity_type_norm IN ('about_year', 'life_stage')
                ORDER BY created_at ASC
                """,
                (event_id,),
            ).fetchall()
            about_years: list[int] = []
            life_stage: str | None = None
            confidence = 0.0
            for row in entity_rows:
                entity_type_norm = str(row["entity_type_norm"])
                confidence = max(confidence, float(row["confidence"]))
                if entity_type_norm == "about_year":
                    about_year = int(str(row["entity_name_raw"]))
                    if about_year not in about_years:
                        about_years.append(about_year)
                    continue
                if life_stage is None:
                    life_stage = str(row["entity_name_raw"])
            if not about_years and life_stage is None:
                continue
            connection.execute(
                """
                INSERT INTO event_about_time (
                    event_about_time_id,
                    event_id,
                    about_start_ts,
                    about_end_ts,
                    about_year_start,
                    about_year_end,
                    life_stage,
                    confidence,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _opaque_id("eat"),
                    event_id,
                    min(about_years) if about_years else None,
                    max(about_years) if about_years else None,
                    life_stage,
                    confidence if confidence > 0.0 else 0.5,
                    now_ms,
                    now_ms,
                ),
            )
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?,
                updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (_json_text(14), now_ms),
        )

    # Block: Schema migration 14->15
    def _migrate_schema_14_to_15(self, *, connection: sqlite3.Connection, now_ms: int) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS state_about_time (
                state_about_time_id TEXT PRIMARY KEY,
                memory_state_id TEXT NOT NULL UNIQUE,
                about_start_ts INTEGER,
                about_end_ts INTEGER,
                about_year_start INTEGER,
                about_year_end INTEGER,
                life_stage TEXT,
                confidence REAL NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                CHECK (
                    about_start_ts IS NOT NULL
                    OR about_end_ts IS NOT NULL
                    OR about_year_start IS NOT NULL
                    OR about_year_end IS NOT NULL
                    OR life_stage IS NOT NULL
                ),
                FOREIGN KEY (memory_state_id) REFERENCES memory_states (memory_state_id)
                    ON UPDATE CASCADE
                    ON DELETE RESTRICT
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_state_about_time_state
                ON state_about_time (memory_state_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_state_about_time_year
                ON state_about_time (about_year_start, about_year_end)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_state_about_time_life_stage
                ON state_about_time (life_stage)
            """
        )
        legacy_state_about_time_by_id: dict[str, dict[str, Any]] = {}
        for row in connection.execute(
            """
            SELECT memory_state_id, entity_type_norm, entity_name_raw, confidence
            FROM state_entities
            WHERE entity_type_norm IN ('about_year', 'life_stage')
            ORDER BY created_at ASC
            """
        ).fetchall():
            memory_state_id = str(row["memory_state_id"])
            legacy_entry = legacy_state_about_time_by_id.get(memory_state_id)
            if legacy_entry is None:
                legacy_entry = {
                    "about_start_ts": None,
                    "about_end_ts": None,
                    "about_year_start": None,
                    "about_year_end": None,
                    "life_stage": None,
                    "confidence": 0.0,
                }
                legacy_state_about_time_by_id[memory_state_id] = legacy_entry
            legacy_entry["confidence"] = max(legacy_entry["confidence"], float(row["confidence"]))
            entity_type_norm = str(row["entity_type_norm"])
            if entity_type_norm == "about_year":
                about_year = int(str(row["entity_name_raw"]))
                if legacy_entry["about_year_start"] is None or about_year < legacy_entry["about_year_start"]:
                    legacy_entry["about_year_start"] = about_year
                if legacy_entry["about_year_end"] is None or about_year > legacy_entry["about_year_end"]:
                    legacy_entry["about_year_end"] = about_year
                continue
            if legacy_entry["life_stage"] is None:
                legacy_entry["life_stage"] = str(row["entity_name_raw"])
        for state_row in connection.execute(
            """
            SELECT
                memory_state_id,
                body_text,
                payload_json,
                created_at,
                updated_at
            FROM memory_states
            ORDER BY updated_at DESC
            """
        ).fetchall():
            memory_state_id = str(state_row["memory_state_id"])
            about_time = _state_about_time_from_row(state_row)
            if about_time is None:
                about_time = legacy_state_about_time_by_id.get(memory_state_id)
            if not isinstance(about_time, dict):
                continue
            connection.execute(
                """
                INSERT INTO state_about_time (
                    state_about_time_id,
                    memory_state_id,
                    about_start_ts,
                    about_end_ts,
                    about_year_start,
                    about_year_end,
                    life_stage,
                    confidence,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _opaque_id("sat"),
                    memory_state_id,
                    about_time["about_start_ts"],
                    about_time["about_end_ts"],
                    about_time["about_year_start"],
                    about_time["about_year_end"],
                    about_time["life_stage"],
                    float(about_time["confidence"]),
                    int(state_row["created_at"]),
                    int(state_row["updated_at"]),
                ),
            )
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?,
                updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (_json_text(15), now_ms),
        )

    # Block: Schema migration 15->16
    def _migrate_schema_15_to_16(self, *, connection: sqlite3.Connection, now_ms: int) -> None:
        preference_memory_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(preference_memory)").fetchall()
        }
        if "target_key" not in preference_memory_columns:
            connection.execute("ALTER TABLE preference_memory ADD COLUMN target_key TEXT")
            for row in connection.execute(
                """
                SELECT preference_id, target_entity_ref_json
                FROM preference_memory
                """
            ).fetchall():
                target_entity_ref = _decoded_object_json(row["target_entity_ref_json"])
                connection.execute(
                    """
                    UPDATE preference_memory
                    SET target_key = ?
                    WHERE preference_id = ?
                    """,
                    (
                        _preference_target_key(target_entity_ref=target_entity_ref),
                        str(row["preference_id"]),
                    ),
                )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS stable_preference_projection (
                owner_scope TEXT NOT NULL CHECK (owner_scope IN ('self', 'other_entity')),
                target_entity_ref_json TEXT NOT NULL,
                target_key TEXT NOT NULL,
                domain TEXT NOT NULL,
                polarity TEXT NOT NULL CHECK (polarity IN ('like', 'dislike')),
                preference_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('confirmed', 'revoked')),
                confidence REAL NOT NULL,
                evidence_event_ids_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (owner_scope, domain, target_key, polarity)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_stable_preference_projection_scope_status_updated
                ON stable_preference_projection (owner_scope, status, confidence DESC, updated_at DESC)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_housekeeping_state (
                maintenance_scope TEXT PRIMARY KEY CHECK (
                    maintenance_scope IN ('completed_jobs_gc', 'stale_preview_gc', 'stale_vector_gc')
                ),
                last_enqueued_at INTEGER,
                last_completed_at INTEGER,
                updated_at INTEGER NOT NULL
            )
            """
        )
        self._ensure_runtime_housekeeping_state_defaults(
            connection=connection,
            now_ms=now_ms,
        )
        for row in connection.execute(
            """
            SELECT
                json_extract(memory_job_payloads.payload_json, '$.maintenance_scope') AS maintenance_scope,
                MAX(memory_jobs.created_at) AS last_enqueued_at,
                MAX(memory_jobs.completed_at) AS last_completed_at
            FROM memory_jobs
            INNER JOIN memory_job_payloads
                    ON memory_job_payloads.payload_id = json_extract(memory_jobs.payload_ref_json, '$.payload_id')
            WHERE memory_jobs.job_kind = 'tidy_memory'
            GROUP BY json_extract(memory_job_payloads.payload_json, '$.maintenance_scope')
            """
        ).fetchall():
            maintenance_scope = row["maintenance_scope"]
            if not isinstance(maintenance_scope, str) or maintenance_scope not in TIDY_MEMORY_SCOPES:
                continue
            self._touch_runtime_housekeeping_state(
                connection=connection,
                maintenance_scope=maintenance_scope,
                last_enqueued_at=(
                    int(row["last_enqueued_at"])
                    if isinstance(row["last_enqueued_at"], int)
                    else None
                ),
                last_completed_at=(
                    int(row["last_completed_at"])
                    if isinstance(row["last_completed_at"], int)
                    else None
                ),
                updated_at=now_ms,
            )
        self._rebuild_stable_preference_projection(
            connection=connection,
        )
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?,
                updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (_json_text(16), now_ms),
        )

    # Block: Schema migration 16->17
    def _migrate_schema_16_to_17(self, *, connection: sqlite3.Connection, now_ms: int) -> None:
        connection.execute("ALTER TABLE preference_memory RENAME TO preference_memory_legacy")
        connection.execute("DROP INDEX IF EXISTS idx_preference_memory_scope_status_updated")
        connection.execute("DROP INDEX IF EXISTS idx_preference_memory_domain_polarity_status")
        connection.execute("DROP INDEX IF EXISTS idx_preference_memory_identity_updated")
        connection.execute(
            """
            CREATE TABLE preference_memory (
                preference_id TEXT PRIMARY KEY,
                owner_scope TEXT NOT NULL CHECK (owner_scope IN ('self', 'other_entity')),
                target_entity_ref_json TEXT NOT NULL,
                target_key TEXT NOT NULL,
                domain TEXT NOT NULL,
                polarity TEXT NOT NULL CHECK (polarity IN ('like', 'dislike')),
                status TEXT NOT NULL CHECK (status IN ('candidate', 'confirmed', 'revoked')),
                confidence REAL NOT NULL,
                evidence_event_ids_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_preference_memory_scope_status_updated
                ON preference_memory (owner_scope, status, updated_at DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_preference_memory_domain_polarity_status
                ON preference_memory (domain, polarity, status)
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_preference_memory_identity_updated
                ON preference_memory (owner_scope, domain, target_key, polarity, updated_at DESC)
            """
        )
        for row in connection.execute(
            """
            SELECT
                preference_id,
                owner_scope,
                target_entity_ref_json,
                domain,
                polarity,
                status,
                confidence,
                evidence_event_ids_json,
                created_at,
                updated_at
            FROM preference_memory_legacy
            ORDER BY created_at ASC, preference_id ASC
            """
        ).fetchall():
            target_entity_ref = _decoded_object_json(row["target_entity_ref_json"])
            connection.execute(
                """
                INSERT INTO preference_memory (
                    preference_id,
                    owner_scope,
                    target_entity_ref_json,
                    target_key,
                    domain,
                    polarity,
                    status,
                    confidence,
                    evidence_event_ids_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row["preference_id"]),
                    str(row["owner_scope"]),
                    _normalized_target_entity_ref_json(target_entity_ref),
                    _preference_target_key(target_entity_ref=target_entity_ref),
                    str(row["domain"]),
                    str(row["polarity"]),
                    str(row["status"]),
                    float(row["confidence"]),
                    str(row["evidence_event_ids_json"]),
                    int(row["created_at"]),
                    int(row["updated_at"]),
                ),
            )
        connection.execute("DROP TABLE preference_memory_legacy")
        connection.execute("DROP INDEX IF EXISTS idx_stable_preference_projection_scope_status_updated")
        connection.execute("DROP TABLE IF EXISTS stable_preference_projection")
        connection.execute(
            """
            CREATE TABLE stable_preference_projection (
                owner_scope TEXT NOT NULL CHECK (owner_scope IN ('self', 'other_entity')),
                target_entity_ref_json TEXT NOT NULL,
                target_key TEXT NOT NULL,
                domain TEXT NOT NULL,
                polarity TEXT NOT NULL CHECK (polarity IN ('like', 'dislike')),
                preference_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('confirmed', 'revoked')),
                confidence REAL NOT NULL,
                evidence_event_ids_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (owner_scope, domain, target_key, polarity)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_stable_preference_projection_scope_status_updated
                ON stable_preference_projection (owner_scope, status, confidence DESC, updated_at DESC)
            """
        )
        self._rebuild_stable_preference_projection(
            connection=connection,
        )
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?,
                updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (_json_text(17), now_ms),
        )

    # Block: Schema migration 17->18
    def _migrate_schema_17_to_18(self, *, connection: sqlite3.Connection, now_ms: int) -> None:
        connection.execute("DROP INDEX IF EXISTS idx_skill_registry_enabled_updated")
        connection.execute("DROP TABLE IF EXISTS skill_registry")
        connection.execute("DROP TABLE IF EXISTS recent_event_window_items")
        connection.execute("DROP TABLE IF EXISTS working_memory_items")
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?,
                updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (_json_text(18), now_ms),
        )

    # Block: sqlite-vec schema ensure
    def _ensure_vec_index_schema(self, *, connection: sqlite3.Connection) -> None:
        connection.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_items_index USING vec0(
                embedding float[{EMBEDDING_VECTOR_DIMENSION}]
            )
            """
        )
        rows = connection.execute(
            """
            SELECT rowid, embedding, searchable
            FROM vec_items
            """
        ).fetchall()
        for row in rows:
            vec_row_id = int(row["rowid"])
            if int(row["searchable"]) == 1:
                _replace_vec_index_row(
                    connection=connection,
                    vec_row_id=vec_row_id,
                    embedding_blob=bytes(row["embedding"]),
                )
                continue
            _delete_vec_index_row(
                connection=connection,
                vec_row_id=vec_row_id,
            )

    # Block: Schema version read
    def _read_schema_version(self, connection: sqlite3.Connection) -> int | None:
        row = connection.execute(
            """
            SELECT meta_value_json
            FROM db_meta
            WHERE meta_key = 'schema_version'
            """
        ).fetchone()
        if row is None:
            return None
        return int(json.loads(row["meta_value_json"]))

    # Block: Metadata verification
    def _ensure_db_meta(self, connection: sqlite3.Connection, now_ms: int) -> None:
        expected_meta = {
            "schema_version": SCHEMA_VERSION,
            "schema_name": SCHEMA_NAME,
            "initialized_at": now_ms,
            "initializer_version": self._initializer_version,
        }
        rows = connection.execute(
            """
            SELECT meta_key, meta_value_json
            FROM db_meta
            WHERE meta_key IN ('schema_version', 'schema_name', 'initialized_at', 'initializer_version')
            """
        ).fetchall()
        current_meta = {row["meta_key"]: json.loads(row["meta_value_json"]) for row in rows}
        if "schema_version" in current_meta and current_meta["schema_version"] != SCHEMA_VERSION:
            raise RuntimeError("schema_version does not match expected version")
        if "schema_name" in current_meta and current_meta["schema_name"] != SCHEMA_NAME:
            raise RuntimeError("schema_name does not match expected schema")
        for key, value in expected_meta.items():
            persisted_value = current_meta.get(key, value)
            connection.execute(
                """
                INSERT INTO db_meta (meta_key, meta_value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(meta_key) DO UPDATE SET
                    meta_value_json = excluded.meta_value_json,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    json.dumps(persisted_value, ensure_ascii=True, separators=(",", ":")),
                    now_ms,
                ),
            )
        current_user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current_user_version != SCHEMA_VERSION:
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # Block: Singleton seed
    def _seed_singletons(self, connection: sqlite3.Connection, now_ms: int) -> None:
        # Block: self_state seed
        connection.execute(
            """
            INSERT INTO self_state (
                row_id,
                personality_json,
                current_emotion_json,
                long_term_goals_json,
                relationship_overview_json,
                invariants_json,
                personality_updated_at,
                updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(row_id) DO NOTHING
            """,
            (
                _json_text(_self_state_personality_seed()),
                _json_text(_self_state_current_emotion_seed()),
                _json_text(_self_state_long_term_goals_seed()),
                _json_text(_self_state_relationship_overview_seed()),
                _json_text(_self_state_invariants_seed()),
                now_ms,
                now_ms,
            ),
        )
        # Block: runtime_settings seed
        connection.execute(
            """
            INSERT INTO runtime_settings (
                row_id,
                values_json,
                value_updated_at_json,
                updated_at
            )
            VALUES (1, ?, ?, ?)
            ON CONFLICT(row_id) DO NOTHING
            """,
            (
                _json_text(build_default_settings()),
                _json_text(_runtime_settings_seed_timestamps(now_ms)),
                now_ms,
            ),
        )
        self._ensure_runtime_settings_defaults(
            connection=connection,
            now_ms=now_ms,
        )
        self._ensure_runtime_housekeeping_state_defaults(
            connection=connection,
            now_ms=now_ms,
        )
        self._ensure_settings_editor_defaults(
            connection=connection,
            now_ms=now_ms,
        )
        # Block: attention_state seed
        connection.execute(
            """
            INSERT INTO attention_state (
                row_id,
                primary_focus_json,
                secondary_focuses_json,
                suppressed_items_json,
                revisit_queue_json,
                updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(row_id) DO NOTHING
            """,
            (
                _json_text(_attention_primary_focus_seed()),
                _json_text([]),
                _json_text([]),
                _json_text([]),
                now_ms,
            ),
        )
        self._ensure_attention_state_defaults(
            connection=connection,
            now_ms=now_ms,
        )
        # Block: body_state seed
        connection.execute(
            """
            INSERT INTO body_state (
                row_id,
                posture_json,
                mobility_json,
                sensor_availability_json,
                output_locks_json,
                load_json,
                updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(row_id) DO NOTHING
            """,
            (
                _json_text(_body_state_posture_seed()),
                _json_text(_body_state_mobility_seed()),
                _json_text(_body_state_sensor_availability_seed()),
                _json_text(_body_state_output_locks_seed()),
                _json_text(_body_state_load_seed()),
                now_ms,
            ),
        )
        self._ensure_body_state_defaults(
            connection=connection,
            now_ms=now_ms,
        )
        # Block: world_state seed
        connection.execute(
            """
            INSERT INTO world_state (
                row_id,
                location_json,
                situation_summary,
                surroundings_json,
                affordances_json,
                constraints_json,
                attention_targets_json,
                external_waits_json,
                updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(row_id) DO NOTHING
            """,
            (
                _json_text(_world_state_location_seed()),
                _world_state_situation_summary_seed(),
                _json_text(_world_state_surroundings_seed()),
                _json_text(_world_state_affordances_seed()),
                _json_text(_world_state_constraints_seed()),
                _json_text(_world_state_attention_targets_seed()),
                _json_text(_world_state_external_waits_seed()),
                now_ms,
            ),
        )
        self._ensure_world_state_defaults(
            connection=connection,
            now_ms=now_ms,
        )
        # Block: drive_state seed
        connection.execute(
            """
            INSERT INTO drive_state (
                row_id,
                drive_levels_json,
                priority_effects_json,
                updated_at
            )
            VALUES (1, ?, ?, ?)
            ON CONFLICT(row_id) DO UPDATE SET
                priority_effects_json = excluded.priority_effects_json,
                updated_at = excluded.updated_at
            WHERE json_extract(drive_state.priority_effects_json, '$.task_progress_bias') IS NULL
               OR json_extract(drive_state.priority_effects_json, '$.exploration_bias') IS NULL
               OR json_extract(drive_state.priority_effects_json, '$.maintenance_bias') IS NULL
               OR json_extract(drive_state.priority_effects_json, '$.social_bias') IS NULL
            """,
            (
                _json_text(_drive_state_drive_levels_seed()),
                _json_text(_drive_state_priority_effects_seed()),
                now_ms,
            ),
        )
        self._ensure_drive_state_defaults(
            connection=connection,
            now_ms=now_ms,
        )

    # Block: Attention state defaults
    def _ensure_attention_state_defaults(
        self,
        *,
        connection: sqlite3.Connection,
        now_ms: int,
    ) -> None:
        row = connection.execute(
            """
            SELECT primary_focus_json
            FROM attention_state
            WHERE row_id = 1
            """
        ).fetchone()
        if row is None:
            raise RuntimeError("attention_state row is missing")
        primary_focus = json.loads(row["primary_focus_json"])
        if not isinstance(primary_focus, dict):
            raise RuntimeError("attention_state.primary_focus_json must be object")
        required_keys = {"focus_ref", "focus_kind", "summary", "score_hint", "reason_codes"}
        if required_keys.issubset(primary_focus):
            return
        connection.execute(
            """
            UPDATE attention_state
            SET primary_focus_json = ?,
                secondary_focuses_json = ?,
                suppressed_items_json = ?,
                revisit_queue_json = ?,
                updated_at = ?
            WHERE row_id = 1
            """,
            (
                _json_text(_attention_primary_focus_seed()),
                _json_text([]),
                _json_text([]),
                _json_text([]),
                now_ms,
            ),
        )

    # Block: Body state defaults
    def _ensure_body_state_defaults(
        self,
        *,
        connection: sqlite3.Connection,
        now_ms: int,
    ) -> None:
        row = connection.execute(
            """
            SELECT posture_json, mobility_json, sensor_availability_json, output_locks_json, load_json
            FROM body_state
            WHERE row_id = 1
            """
        ).fetchone()
        if row is None:
            raise RuntimeError("body_state row is missing")
        if _body_state_has_current_shape(row):
            return
        connection.execute(
            """
            UPDATE body_state
            SET posture_json = ?,
                mobility_json = ?,
                sensor_availability_json = ?,
                output_locks_json = ?,
                load_json = ?,
                updated_at = ?
            WHERE row_id = 1
            """,
            (
                _json_text(_body_state_posture_seed()),
                _json_text(_body_state_mobility_seed()),
                _json_text(_body_state_sensor_availability_seed()),
                _json_text(_body_state_output_locks_seed()),
                _json_text(_body_state_load_seed()),
                now_ms,
            ),
        )

    # Block: World state defaults
    def _ensure_world_state_defaults(
        self,
        *,
        connection: sqlite3.Connection,
        now_ms: int,
    ) -> None:
        row = connection.execute(
            """
            SELECT
                location_json,
                situation_summary,
                surroundings_json,
                affordances_json,
                constraints_json,
                attention_targets_json,
                external_waits_json
            FROM world_state
            WHERE row_id = 1
            """
        ).fetchone()
        if row is None:
            raise RuntimeError("world_state row is missing")
        if _world_state_has_current_shape(row):
            return
        connection.execute(
            """
            UPDATE world_state
            SET location_json = ?,
                situation_summary = ?,
                surroundings_json = ?,
                affordances_json = ?,
                constraints_json = ?,
                attention_targets_json = ?,
                external_waits_json = ?,
                updated_at = ?
            WHERE row_id = 1
            """,
            (
                _json_text(_world_state_location_seed()),
                _world_state_situation_summary_seed(),
                _json_text(_world_state_surroundings_seed()),
                _json_text(_world_state_affordances_seed()),
                _json_text(_world_state_constraints_seed()),
                _json_text(_world_state_attention_targets_seed()),
                _json_text(_world_state_external_waits_seed()),
                now_ms,
            ),
        )

    # Block: Drive state defaults
    def _ensure_drive_state_defaults(
        self,
        *,
        connection: sqlite3.Connection,
        now_ms: int,
    ) -> None:
        row = connection.execute(
            """
            SELECT drive_levels_json, priority_effects_json
            FROM drive_state
            WHERE row_id = 1
            """
        ).fetchone()
        if row is None:
            raise RuntimeError("drive_state row is missing")
        if _drive_state_has_current_shape(row):
            return
        connection.execute(
            """
            UPDATE drive_state
            SET drive_levels_json = ?,
                priority_effects_json = ?,
                updated_at = ?
            WHERE row_id = 1
            """,
            (
                _json_text(_drive_state_drive_levels_seed()),
                _json_text(_drive_state_priority_effects_seed()),
                now_ms,
            ),
        )
