"""SQLite-backed state and control plane access."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
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
    build_default_output_preset_payload,
    build_default_settings,
    build_default_settings_editor_state,
    build_default_settings_presets,
    build_output_preset_setting_keys,
    build_settings_editor_system_keys,
    decode_requested_value,
    normalize_settings_editor_document,
)


# Block: Schema constants
SCHEMA_NAME = "core_schema"
SCHEMA_VERSION = 7
EMBEDDING_VECTOR_DIMENSION = 32
LEGACY_SETTING_KEY_ALIASES = {
    "llm.model": "llm.default_model",
    "speech.tts.aivis_cloud.api_key": "speech.tts.api_key",
    "speech.tts.aivis_cloud.endpoint_url": "speech.tts.endpoint_url",
    "speech.tts.aivis_cloud.model_uuid": "speech.tts.model_uuid",
    "speech.tts.aivis_cloud.speaker_uuid": "speech.tts.speaker_uuid",
    "speech.tts.aivis_cloud.style_id": "speech.tts.style_id",
    "speech.tts.aivis_cloud.language": "speech.tts.language",
    "speech.tts.aivis_cloud.speaking_rate": "speech.tts.speaking_rate",
    "speech.tts.aivis_cloud.emotional_intensity": "speech.tts.emotional_intensity",
    "speech.tts.aivis_cloud.tempo_dynamics": "speech.tts.tempo_dynamics",
    "speech.tts.aivis_cloud.pitch": "speech.tts.pitch",
    "speech.tts.aivis_cloud.volume": "speech.tts.volume",
    "speech.tts.aivis_cloud.output_format": "speech.tts.output_format",
}
LEGACY_OPTIONAL_BASE_URL_DEFAULTS = {
    "llm.base_url": "https://openrouter.ai/api/v1",
    "llm.embedding_base_url": "https://openrouter.ai/api/v1",
}
LEGACY_AIVIS_RUNTIME_KEYS = tuple(
    legacy_key
    for current_key, legacy_key in LEGACY_SETTING_KEY_ALIASES.items()
    if current_key.startswith("speech.tts.aivis_cloud.")
)
OUTPUT_PRESET_SETTING_KEYS = build_output_preset_setting_keys()


# Block: API errors
class StoreConflictError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "conflict") -> None:
        # Block: Structured conflict payload
        super().__init__(message)
        self.message = message
        self.error_code = error_code


class StoreValidationError(ValueError):
    pass


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
            self._ensure_vec_index_schema(connection=connection)
            self._ensure_db_meta(connection, now_ms)
            self._ensure_settings_editor_state_schema_v7(
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
            task_counts_row = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN task_status = 'active' THEN 1 ELSE 0 END) AS active_count,
                    SUM(CASE WHEN task_status = 'waiting_external' THEN 1 ELSE 0 END) AS waiting_count
                FROM task_state
                """
            ).fetchone()
        if self_row is None or attention_row is None:
            raise RuntimeError("singleton state rows are missing")
        runtime_payload: dict[str, Any] = {"is_running": runtime_row is not None}
        if commit_row is not None:
            runtime_payload["last_cycle_id"] = commit_row["cycle_id"]
            runtime_payload["last_commit_id"] = commit_row["commit_id"]
        current_emotion_json = json.loads(self_row["current_emotion_json"])
        primary_focus_json = json.loads(attention_row["primary_focus_json"])
        active_count = int(task_counts_row["active_count"] or 0)
        waiting_count = int(task_counts_row["waiting_count"] or 0)
        return {
            "server_time": now_ms,
            "runtime": runtime_payload,
            "self_state": {"current_emotion": _public_emotion_summary(current_emotion_json)},
            "attention_state": {"primary_focus": _public_primary_focus(primary_focus_json)},
            "task_state": {
                "active_task_count": active_count,
                "waiting_task_count": waiting_count,
            },
        }

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

    # Block: Settings editor snapshot
    def read_settings_editor(self, default_settings: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            editor_row = connection.execute(
                """
                SELECT
                    active_behavior_preset_id,
                    active_llm_preset_id,
                    active_memory_preset_id,
                    active_output_preset_id,
                    active_camera_connection_id,
                    system_values_json,
                    revision,
                    updated_at,
                    last_applied_change_set_id
                FROM settings_editor_state
                WHERE row_id = 1
                """
            ).fetchone()
            preset_rows = connection.execute(
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
            camera_connection_rows = connection.execute(
                """
                SELECT
                    camera_connection_id,
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
        preset_catalogs = _decode_settings_preset_catalog_rows(preset_rows)
        camera_connections = _decode_camera_connection_rows(camera_connection_rows)
        runtime_projection = _materialize_runtime_settings_from_editor(
            default_settings=default_settings,
            editor_state=editor_state,
            preset_catalogs=preset_catalogs,
        )
        return {
            "editor_state": {
                "revision": editor_state["revision"],
                "active_behavior_preset_id": editor_state["active_behavior_preset_id"],
                "active_llm_preset_id": editor_state["active_llm_preset_id"],
                "active_memory_preset_id": editor_state["active_memory_preset_id"],
                "active_output_preset_id": editor_state["active_output_preset_id"],
                "active_camera_connection_id": editor_state["active_camera_connection_id"],
                "system_values": dict(editor_state["system_values"]),
            },
            "preset_catalogs": preset_catalogs,
            "camera_connections": camera_connections,
            "constraints": {
                "editable_system_keys": list(build_settings_editor_system_keys()),
            },
            "runtime_projection": runtime_projection,
        }

    # Block: Active camera connection snapshot
    def read_active_camera_connection(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            editor_row = connection.execute(
                """
                SELECT active_camera_connection_id
                FROM settings_editor_state
                WHERE row_id = 1
                """
            ).fetchone()
            if editor_row is None:
                raise RuntimeError("settings_editor_state row is missing")
            active_camera_connection_id = editor_row["active_camera_connection_id"]
            if active_camera_connection_id is None:
                return None
            camera_connection_row = connection.execute(
                """
                SELECT
                    camera_connection_id,
                    display_name,
                    host,
                    username,
                    password,
                    sort_order,
                    created_at,
                    updated_at
                FROM camera_connections
                WHERE camera_connection_id = ?
                """,
                (str(active_camera_connection_id),),
            ).fetchone()
        if camera_connection_row is None:
            return None
        camera_connections = _decode_camera_connection_rows([camera_connection_row])
        if len(camera_connections) != 1:
            raise RuntimeError("camera connection decode result is invalid")
        return camera_connections[0]

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
                    active_behavior_preset_id,
                    active_llm_preset_id,
                    active_memory_preset_id,
                    active_output_preset_id,
                    active_camera_connection_id,
                    system_values_json,
                    revision,
                    updated_at,
                    last_applied_change_set_id
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
            current_preset_rows = connection.execute(
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
            current_camera_connection_rows = connection.execute(
                """
                SELECT
                    camera_connection_id,
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
            current_preset_catalogs = _decode_settings_preset_catalog_rows(current_preset_rows)
            current_camera_connections = _decode_camera_connection_rows(current_camera_connection_rows)
            if (
                _canonical_editor_state_for_compare(current_editor_state)
                == normalized_document["editor_state"]
                and current_preset_catalogs == normalized_document["preset_catalogs"]
                and current_camera_connections == normalized_document["camera_connections"]
            ):
                return self.read_settings_editor(default_settings)
            saved_editor_state = {
                "active_behavior_preset_id": normalized_document["editor_state"]["active_behavior_preset_id"],
                "active_llm_preset_id": normalized_document["editor_state"]["active_llm_preset_id"],
                "active_memory_preset_id": normalized_document["editor_state"]["active_memory_preset_id"],
                "active_output_preset_id": normalized_document["editor_state"]["active_output_preset_id"],
                "active_camera_connection_id": normalized_document["editor_state"]["active_camera_connection_id"],
                "system_values": dict(normalized_document["editor_state"]["system_values"]),
                "revision": current_revision + 1,
                "updated_at": now_ms,
                "last_applied_change_set_id": current_editor_state.get("last_applied_change_set_id"),
            }
            _persist_settings_editor_state(connection=connection, editor_state=saved_editor_state)
            _replace_settings_presets(
                connection=connection,
                preset_catalogs=normalized_document["preset_catalogs"],
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
                preset_catalogs=normalized_document["preset_catalogs"],
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
            recent_event_rows = connection.execute(
                """
                SELECT
                    event_id,
                    source,
                    kind,
                    observation_summary,
                    action_summary,
                    result_summary,
                    created_at
                FROM events
                WHERE searchable = 1
                ORDER BY created_at DESC
                LIMIT 5
                """
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
                  AND memory_kind IN ('summary', 'fact')
                ORDER BY updated_at DESC
                LIMIT 8
                """
            ).fetchall()
            if observation_hint_text is not None and observation_hint_text.strip():
                similarity_hits = _search_vec_similarity_hits(
                    connection=connection,
                    query_text=observation_hint_text.strip(),
                    embedding_model=embedding_model,
                    limit=8,
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
            ),
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
        return int(commit_row["commit_id"])

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
    ) -> None:
        if final_status not in {"applied", "rejected"}:
            raise StoreValidationError("settings change set final_status is invalid")
        resolved_at = _now_ms()
        with self._connect() as connection:
            if final_status == "applied":
                editor_row = connection.execute(
                    """
                    SELECT
                        active_behavior_preset_id,
                        active_llm_preset_id,
                        active_memory_preset_id,
                        active_output_preset_id,
                        active_camera_connection_id,
                        system_values_json,
                        revision,
                        updated_at,
                        last_applied_change_set_id
                    FROM settings_editor_state
                    WHERE row_id = 1
                    """
                ).fetchone()
                if editor_row is None:
                    raise RuntimeError("settings_editor_state row is missing")
                preset_rows = connection.execute(
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
                camera_connection_rows = connection.execute(
                    """
                    SELECT
                        camera_connection_id,
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
                editor_state = _decode_settings_editor_state_row(editor_row)
                if int(editor_state["revision"]) != change_set.editor_revision:
                    final_status = "rejected"
                    reject_reason = "stale_settings_change_set"
                else:
                    preset_catalogs = _decode_settings_preset_catalog_rows(preset_rows)
                    camera_connections = _decode_camera_connection_rows(camera_connection_rows)
                    runtime_values = _materialize_runtime_settings_from_editor(
                        default_settings=default_settings,
                        editor_state=editor_state,
                        preset_catalogs=preset_catalogs,
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
                    connection.execute(
                        """
                        UPDATE settings_editor_state
                        SET last_applied_change_set_id = ?
                        WHERE row_id = 1
                        """,
                        (change_set.change_set_id,),
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
        payload: dict[str, Any] = {"input_kind": "chat_message"}
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
        )

    # Block: Camera observation write
    def enqueue_camera_observation(
        self,
        *,
        capture_id: str,
        image_path: str,
        image_url: str,
        captured_at: int,
    ) -> dict[str, Any]:
        if not isinstance(capture_id, str) or not capture_id:
            raise StoreValidationError("capture_id must be non-empty string")
        if not isinstance(image_path, str) or not image_path:
            raise StoreValidationError("image_path must be non-empty string")
        if not isinstance(image_url, str) or not image_url:
            raise StoreValidationError("image_url must be non-empty string")
        if isinstance(captured_at, bool) or not isinstance(captured_at, int):
            raise StoreValidationError("captured_at must be integer")
        payload = {
            "input_kind": "camera_observation",
            "attachments": [
                {
                    "attachment_kind": "camera_still_image",
                    "media_kind": "image",
                    "capture_id": capture_id,
                    "mime_type": "image/jpeg",
                    "storage_path": image_path,
                    "content_url": image_url,
                    "captured_at": captured_at,
                }
            ],
        }
        enqueue_result = self._enqueue_pending_input(
            source="self_initiated",
            client_message_id=None,
            payload=payload,
            priority=80,
        )
        return {
            **enqueue_result,
            "capture_id": capture_id,
            "image_path": image_path,
            "image_url": image_url,
            "captured_at": captured_at,
        }

    # Block: Pending input write
    def _enqueue_pending_input(
        self,
        *,
        source: str,
        client_message_id: str | None,
        payload: dict[str, Any],
        priority: int,
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
        payload: dict[str, Any] = {"input_kind": "cancel"}
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
        if maintenance_scope not in {"completed_jobs_gc", "stale_preview_gc", "stale_vector_gc"}:
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
        if memory_job.job_kind != "write_memory":
            raise StoreValidationError("memory_job.job_kind must be write_memory")
        source_event_ids = memory_job.payload["source_event_ids"]
        if not isinstance(source_event_ids, list) or not source_event_ids:
            raise StoreValidationError("write_memory source_event_ids must not be empty")
        now_ms = _now_ms()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_claimed_memory_job(
                connection=connection,
                job_id=memory_job.job_id,
            )
            event_rows = _fetch_events_for_ids(
                connection=connection,
                event_ids=source_event_ids,
            )
            summary_text = _build_write_memory_summary_text(
                primary_event_id=str(memory_job.payload["primary_event_id"]),
                event_rows=event_rows,
            )
            memory_state_targets = [
                self._insert_memory_state_with_revision(
                    connection=connection,
                    memory_kind="summary",
                    body_text=summary_text,
                    payload_json={
                        "source_job_id": memory_job.job_id,
                        "job_kind": memory_job.job_kind,
                        "source_cycle_id": memory_job.payload["cycle_id"],
                        "primary_event_id": memory_job.payload["primary_event_id"],
                        "source_event_ids": source_event_ids,
                        "summary_kind": "minimal_write_memory",
                    },
                    confidence=0.50,
                    importance=0.50,
                    memory_strength=0.50,
                    last_confirmed_at=now_ms,
                    evidence_event_ids=source_event_ids,
                    created_at=now_ms,
                    revision_reason="write_memory created summary",
                )
            ]
            memory_state_targets.extend(
                self._insert_external_fact_memory_states(
                    connection=connection,
                    cycle_id=str(memory_job.payload["cycle_id"]),
                    source_job_id=memory_job.job_id,
                    source_event_ids=source_event_ids,
                    created_at=now_ms,
                )
            )
            self._enqueue_refresh_preview_jobs(
                connection=connection,
                cycle_id=str(memory_job.payload["cycle_id"]),
                event_rows=event_rows,
                created_at=now_ms,
            )
            self._enqueue_embedding_sync_jobs(
                connection=connection,
                cycle_id=str(memory_job.payload["cycle_id"]),
                source_event_ids=source_event_ids,
                targets=memory_state_targets,
                embedding_model=self._require_runtime_setting_string(
                    connection=connection,
                    key="llm.embedding_model",
                ),
                created_at=now_ms,
            )
            self._mark_memory_job_completed(
                connection=connection,
                job_id=memory_job.job_id,
                completed_at=now_ms,
            )
        return str(memory_state_targets[0]["entity_id"])

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
            VALUES (?, 'memory_states', ?, ?, ?, ?, ?, ?)
            """,
            (
                _opaque_id("rev"),
                memory_state_id,
                _json_text({}),
                _json_text(
                    {
                        "memory_kind": memory_kind,
                        "body_text": body_text,
                        **payload_json,
                    }
                ),
                revision_reason,
                _json_text(evidence_event_ids),
                created_at,
            ),
        )
        return {
            "entity_type": "memory_state",
            "entity_id": memory_state_id,
            "source_updated_at": created_at,
            "current_searchable": True,
        }

    # Block: External fact memory insert
    def _insert_external_fact_memory_states(
        self,
        *,
        connection: sqlite3.Connection,
        cycle_id: str,
        source_job_id: str,
        source_event_ids: list[str],
        created_at: int,
    ) -> list[dict[str, Any]]:
        action_rows = _fetch_action_history_for_cycle(
            connection=connection,
            cycle_id=cycle_id,
            action_type="complete_browse_task",
        )
        memory_state_targets: list[dict[str, Any]] = []
        for action_row in action_rows:
            command_json = json.loads(action_row["command_json"])
            observed_effects_json = json.loads(action_row["observed_effects_json"])
            query = _browse_query_from_action_history(command_json)
            summary_text = _browse_summary_from_action_history(observed_effects_json)
            related_task_id = _browse_task_id_from_action_history(command_json)
            memory_state_targets.append(
                self._insert_memory_state_with_revision(
                    connection=connection,
                    memory_kind="fact",
                    body_text=f"外部確認: {query} => {summary_text}",
                    payload_json={
                        "source_job_id": source_job_id,
                        "job_kind": "write_memory",
                        "source_cycle_id": cycle_id,
                        "source_event_ids": source_event_ids,
                        "fact_kind": "external_search_result",
                        "query": query,
                        "summary_text": summary_text,
                        "source_task_id": related_task_id,
                    },
                    confidence=0.85,
                    importance=0.75,
                    memory_strength=0.75,
                    last_confirmed_at=created_at,
                    evidence_event_ids=source_event_ids,
                    created_at=created_at,
                    revision_reason="write_memory created external fact",
                )
            )
        return memory_state_targets

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
            preview_text = _build_event_preview_text(event_row)
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
                active_behavior_preset_id,
                active_llm_preset_id,
                active_memory_preset_id,
                active_output_preset_id,
                active_camera_connection_id,
                system_values_json,
                revision,
                updated_at,
                last_applied_change_set_id
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(row_id) DO NOTHING
            """,
            (
                editor_seed["active_behavior_preset_id"],
                editor_seed["active_llm_preset_id"],
                editor_seed["active_memory_preset_id"],
                editor_seed["active_output_preset_id"],
                editor_seed["active_camera_connection_id"],
                _json_text(editor_seed["system_values_json"]),
                int(editor_seed["revision"]),
                now_ms,
            ),
        )
        preset_seeds = build_default_settings_presets(default_settings)
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
        camera_connection_seeds = build_default_camera_connections()
        for index, camera_connection_seed in enumerate(camera_connection_seeds):
            connection.execute(
                """
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(camera_connection_id) DO NOTHING
                """,
                (
                    camera_connection_seed["camera_connection_id"],
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
        # Block: Behavior preset migration
        behavior_preset_rows = connection.execute(
            """
            SELECT preset_id, payload_json
            FROM settings_presets
            WHERE preset_kind = 'behavior'
            """
        ).fetchall()
        for behavior_preset_row in behavior_preset_rows:
            raw_payload = json.loads(behavior_preset_row["payload_json"])
            if not isinstance(raw_payload, dict):
                raise RuntimeError("settings_presets.behavior payload_json must be object")
            normalized_payload = _normalize_legacy_behavior_preset_payload(
                preset_kind="behavior",
                payload=raw_payload,
                default_settings=default_settings,
            )
            if normalized_payload == raw_payload:
                continue
            connection.execute(
                """
                UPDATE settings_presets
                SET payload_json = ?,
                    updated_at = ?
                WHERE preset_id = ?
                """,
                (
                    _json_text(normalized_payload),
                    now_ms,
                    str(behavior_preset_row["preset_id"]),
                ),
            )
        # Block: Output preset migration
        output_preset_rows = connection.execute(
            """
            SELECT preset_id, payload_json
            FROM settings_presets
            WHERE preset_kind = 'output'
            """
        ).fetchall()
        for output_preset_row in output_preset_rows:
            raw_payload = json.loads(output_preset_row["payload_json"])
            if not isinstance(raw_payload, dict):
                raise RuntimeError("settings_presets.output payload_json must be object")
            normalized_payload = _normalize_legacy_output_preset_payload(
                preset_kind="output",
                payload=raw_payload,
            )
            if normalized_payload == raw_payload:
                continue
            connection.execute(
                """
                UPDATE settings_presets
                SET payload_json = ?,
                    updated_at = ?
                WHERE preset_id = ?
                """,
                (
                    _json_text(normalized_payload),
                    now_ms,
                    str(output_preset_row["preset_id"]),
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
        preset_seeds = build_default_settings_presets(default_settings)
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
    def _ensure_settings_editor_state_schema_v7(
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
            return
        column_names = {str(row["name"]) for row in column_rows}
        expected_column_names = {
            "row_id",
            "active_behavior_preset_id",
            "active_llm_preset_id",
            "active_memory_preset_id",
            "active_output_preset_id",
            "active_camera_connection_id",
            "system_values_json",
            "revision",
            "updated_at",
            "last_applied_change_set_id",
        }
        if column_names == expected_column_names:
            return
        default_settings = build_default_settings()
        editor_seed = build_default_settings_editor_state(default_settings)
        existing_row = connection.execute(
            """
            SELECT *
            FROM settings_editor_state
            WHERE row_id = 1
            """
        ).fetchone()
        migrated_row: dict[str, Any] | None = None
        if existing_row is not None:
            system_values = dict(editor_seed["system_values_json"])
            if "system_values_json" in column_names:
                raw_system_values = json.loads(existing_row["system_values_json"])
                if isinstance(raw_system_values, dict):
                    for key in build_settings_editor_system_keys():
                        if key in raw_system_values:
                            system_values[key] = raw_system_values[key]
            if "direct_values_json" in column_names:
                raw_direct_values = json.loads(existing_row["direct_values_json"])
                if isinstance(raw_direct_values, dict):
                    for key in build_settings_editor_system_keys():
                        if key in raw_direct_values:
                            system_values[key] = raw_direct_values[key]
            migrated_row = {
                "active_behavior_preset_id": str(existing_row["active_behavior_preset_id"]),
                "active_llm_preset_id": str(existing_row["active_llm_preset_id"]),
                "active_memory_preset_id": str(existing_row["active_memory_preset_id"]),
                "active_output_preset_id": str(existing_row["active_output_preset_id"]),
                "active_camera_connection_id": (
                    str(existing_row["active_camera_connection_id"])
                    if "active_camera_connection_id" in column_names
                    and existing_row["active_camera_connection_id"] is not None
                    else None
                ),
                "system_values_json": system_values,
                "revision": int(existing_row["revision"]),
                "updated_at": int(existing_row["updated_at"]),
                "last_applied_change_set_id": (
                    str(existing_row["last_applied_change_set_id"])
                    if existing_row["last_applied_change_set_id"] is not None
                    else None
                ),
            }
        temporary_table_name = f"settings_editor_state_legacy_{uuid.uuid4().hex}"
        connection.execute(
            f"""
            ALTER TABLE settings_editor_state
            RENAME TO {temporary_table_name}
            """
        )
        connection.execute(
            """
            CREATE TABLE settings_editor_state (
                row_id INTEGER PRIMARY KEY CHECK (row_id = 1),
                active_behavior_preset_id TEXT NOT NULL,
                active_llm_preset_id TEXT NOT NULL,
                active_memory_preset_id TEXT NOT NULL,
                active_output_preset_id TEXT NOT NULL,
                active_camera_connection_id TEXT,
                system_values_json TEXT NOT NULL,
                revision INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_applied_change_set_id TEXT
            )
            """
        )
        if migrated_row is not None:
            connection.execute(
                """
                INSERT INTO settings_editor_state (
                    row_id,
                    active_behavior_preset_id,
                    active_llm_preset_id,
                    active_memory_preset_id,
                    active_output_preset_id,
                    active_camera_connection_id,
                    system_values_json,
                    revision,
                    updated_at,
                    last_applied_change_set_id
                )
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    migrated_row["active_behavior_preset_id"],
                    migrated_row["active_llm_preset_id"],
                    migrated_row["active_memory_preset_id"],
                    migrated_row["active_output_preset_id"],
                    migrated_row["active_camera_connection_id"],
                    _json_text(migrated_row["system_values_json"]),
                    migrated_row["revision"],
                    migrated_row["updated_at"],
                    migrated_row["last_applied_change_set_id"],
                ),
            )
        connection.execute(f"DROP TABLE {temporary_table_name}")

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
            source=pending_input.source,
            kind=str(pending_input.payload["input_kind"]),
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
        ui_events: list[dict[str, Any]],
        commit_payload: dict[str, Any],
        discard_reason: str | None = None,
    ) -> int:
        if resolution_status not in {"consumed", "discarded"}:
            raise StoreValidationError("resolution_status is invalid")
        resolved_at = _now_ms()
        with self._connect() as connection:
            self._apply_task_state_mutations(
                connection=connection,
                task_mutations=task_mutations,
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
        return int(commit_id["commit_id"])

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
        return int(commit_id["commit_id"])

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
            "event_snapshot_refs": [
                {
                    "event_id": event_id,
                    "event_updated_at": created_at,
                }
                for event_id in event_ids
            ],
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
        if maintenance_scope not in {"completed_jobs_gc", "stale_preview_gc", "stale_vector_gc"}:
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
        return [
            self._insert_memory_job(
                connection=connection,
                job_kind="tidy_memory",
                payload_json=payload_json,
                idempotency_key=idempotency_key,
                created_at=created_at,
            )
        ]

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
                source=pending_input.source,
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
        if current_version not in {2, 3, 4, 5, 6}:
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
        editor_seed = build_default_settings_editor_state(default_settings)
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
        preset_seeds = build_default_settings_presets(
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
        self._ensure_settings_editor_state_schema_v7(
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
                _json_text({"kind": "idle"}),
                _json_text([]),
                _json_text([]),
                _json_text([]),
                now_ms,
            ),
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
                _json_text({"mode": "idle"}),
                _json_text({}),
                _json_text({}),
                _json_text({}),
                _json_text({}),
                now_ms,
            ),
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
                _json_text({"state": "unknown"}),
                "unknown",
                _json_text({}),
                _json_text({}),
                _json_text({}),
                _json_text({}),
                _json_text({}),
                now_ms,
            ),
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
                _json_text({}),
                _json_text(_drive_state_priority_effects_seed()),
                now_ms,
            ),
        )


# Block: Seed JSON helpers
def _self_state_personality_seed() -> dict[str, Any]:
    return {
        "trait_values": {
            "sociability": 0.0,
            "caution": 0.0,
            "curiosity": 0.0,
            "persistence": 0.0,
            "warmth": 0.0,
            "assertiveness": 0.0,
            "novelty_preference": 0.0,
        },
        "preferred_interaction_style": {
            "speech_tone": "neutral",
            "distance_style": "balanced",
            "confirmation_style": "balanced",
            "response_pace": "balanced",
        },
        "learned_preferences": [],
        "learned_aversions": [],
        "habit_biases": {
            "preferred_action_types": [],
            "preferred_observation_kinds": [],
            "avoided_action_styles": [],
        },
    }


def _self_state_current_emotion_seed() -> dict[str, Any]:
    return {
        "primary_label": "calm",
        "valence": 0.0,
        "arousal": 0.0,
        "dominance": 0.0,
        "stability": 1.0,
        "active_biases": {
            "caution_bias": 0.0,
            "approach_bias": 0.0,
            "avoidance_bias": 0.0,
            "speech_intensity_bias": 0.0,
        },
    }


def _self_state_long_term_goals_seed() -> dict[str, Any]:
    return {"goals": []}


def _self_state_relationship_overview_seed() -> dict[str, Any]:
    return {"relationships": []}


def _self_state_invariants_seed() -> dict[str, Any]:
    return {
        "forbidden_action_types": [],
        "forbidden_action_styles": [],
        "required_confirmation_for": [],
        "protected_targets": [],
    }


def _drive_state_priority_effects_seed() -> dict[str, Any]:
    return {
        "task_progress_bias": 0.0,
        "exploration_bias": 0.0,
        "maintenance_bias": 0.0,
        "social_bias": 0.0,
    }


# Block: Public response helpers
def _public_emotion_summary(current_emotion_json: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(current_emotion_json, dict):
        raise RuntimeError("self_state.current_emotion_json must be an object")
    if "primary_label" not in current_emotion_json:
        raise RuntimeError("self_state.current_emotion_json.primary_label is required")
    return {
        "v": float(current_emotion_json["valence"]),
        "a": float(current_emotion_json["arousal"]),
        "d": float(current_emotion_json["dominance"]),
        "labels": [str(current_emotion_json["primary_label"])],
    }


def _public_primary_focus(primary_focus_json: dict[str, Any]) -> str:
    if not isinstance(primary_focus_json, dict):
        raise RuntimeError("attention_state.primary_focus_json must be an object")
    focus_kind = primary_focus_json.get("kind")
    if not isinstance(focus_kind, str) or not focus_kind:
        raise RuntimeError("attention_state.primary_focus_json.kind is required")
    return focus_kind


# Block: Journal helpers
def _pending_input_receipt_summary(pending_input: PendingInputRecord) -> str:
    input_kind = str(pending_input.payload["input_kind"])
    if input_kind == "chat_message":
        text = pending_input.payload.get("text")
        if isinstance(text, str) and text:
            return f"chat_message:{text[:60]}"
        attachments = pending_input.payload.get("attachments")
        if isinstance(attachments, list) and attachments:
            return f"chat_message:camera_images:{len(attachments)}"
        return "chat_message"
    if input_kind == "camera_observation":
        attachments = pending_input.payload.get("attachments")
        if isinstance(attachments, list) and attachments:
            return f"camera_observation:camera_images:{len(attachments)}"
        return "camera_observation"
    if input_kind == "network_result":
        query = str(pending_input.payload["query"])
        summary_text = str(pending_input.payload["summary_text"])
        return f"network_result:{query}:{summary_text[:40]}"
    if input_kind == "cancel":
        return "cancel request"
    return f"input:{input_kind}"


def _runtime_response_summary(ui_events: list[dict[str, Any]]) -> str | None:
    for ui_event in ui_events:
        payload = ui_event["payload"]
        event_type = ui_event["event_type"]
        if event_type == "message":
            return str(payload["text"])
        if event_type == "notice":
            return str(payload["text"])
        if event_type == "error":
            return str(payload["message"])
    return None


# Block: Action summary helpers
def _action_command_summary(action_result: ActionHistoryRecord) -> str:
    target_channel = action_result.command.get("target_channel")
    if isinstance(target_channel, str) and target_channel:
        return f"{action_result.action_type} -> {target_channel}"
    target = action_result.command.get("target")
    if isinstance(target, dict):
        target_channel = target.get("channel")
        if isinstance(target_channel, str) and target_channel:
            return f"{action_result.action_type} -> {target_channel}"
        target_queue = target.get("queue")
        if isinstance(target_queue, str) and target_queue:
            return f"{action_result.action_type} -> {target_queue}"
    return action_result.action_type


def _action_result_summary(action_result: ActionHistoryRecord) -> str:
    if action_result.failure_mode:
        return f"{action_result.action_type} {action_result.status}: {action_result.failure_mode}"
    return f"{action_result.action_type} {action_result.status}"


# Block: Memory job helpers
def _write_memory_job_idempotency_key(*, cycle_id: str, event_ids: list[str]) -> str:
    return "write_memory:" + cycle_id + ":" + ":".join(event_ids)


# Block: Memory job payload ref resolution
def _resolve_memory_job_payload_ref(payload_ref_json: Any) -> dict[str, Any]:
    if not isinstance(payload_ref_json, str) or not payload_ref_json:
        raise RuntimeError("memory_jobs.payload_ref_json must be non-empty string")
    try:
        payload_ref = json.loads(payload_ref_json)
    except json.JSONDecodeError as error:
        raise RuntimeError("memory_jobs.payload_ref_json must be valid JSON") from error
    if not isinstance(payload_ref, dict):
        raise RuntimeError("memory_jobs.payload_ref_json must be object")
    if payload_ref.get("payload_kind") != "memory_job_payload":
        raise RuntimeError("memory_jobs.payload_ref_json.payload_kind must be memory_job_payload")
    payload_id = payload_ref.get("payload_id")
    if not isinstance(payload_id, str) or not payload_id:
        raise RuntimeError("memory_jobs.payload_ref_json.payload_id must be non-empty string")
    payload_version = payload_ref.get("payload_version")
    if isinstance(payload_version, bool) or not isinstance(payload_version, int):
        raise RuntimeError("memory_jobs.payload_ref_json.payload_version must be integer")
    if payload_version < 1:
        raise RuntimeError("memory_jobs.payload_ref_json.payload_version must be >= 1")
    return {
        "payload_id": payload_id,
        "payload_version": payload_version,
    }


def _normalize_quarantine_targets(raw_targets: Any) -> list[dict[str, str]]:
    if not isinstance(raw_targets, list) or not raw_targets:
        raise StoreValidationError("quarantine_memory targets must not be empty")
    normalized_targets: list[dict[str, str]] = []
    for raw_target in raw_targets:
        if not isinstance(raw_target, dict):
            raise StoreValidationError("quarantine_memory target must be object")
        entity_type = raw_target.get("entity_type")
        entity_id = raw_target.get("entity_id")
        if not isinstance(entity_type, str) or not entity_type:
            raise StoreValidationError("quarantine_memory target.entity_type must be non-empty string")
        if entity_type == "event_affect":
            raise StoreValidationError("event_affect quarantine is not implemented yet")
        if entity_type not in {"event", "memory_state"}:
            raise StoreValidationError("quarantine_memory target.entity_type is invalid")
        if not isinstance(entity_id, str) or not entity_id:
            raise StoreValidationError("quarantine_memory target.entity_id must be non-empty string")
        normalized_targets.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
            }
        )
    return normalized_targets


# Block: Tidy memory target normalization
def _normalize_tidy_target_refs(raw_target_refs: Any) -> list[dict[str, str]]:
    if not isinstance(raw_target_refs, list) or not raw_target_refs:
        raise StoreValidationError("tidy_memory target_refs must not be empty")
    normalized_refs: list[dict[str, str]] = []
    for raw_target_ref in raw_target_refs:
        if not isinstance(raw_target_ref, dict):
            raise StoreValidationError("tidy_memory target_ref must be object")
        entity_type = raw_target_ref.get("entity_type")
        entity_id = raw_target_ref.get("entity_id")
        if not isinstance(entity_type, str) or not entity_type:
            raise StoreValidationError("tidy_memory target_ref.entity_type must be non-empty string")
        if not isinstance(entity_id, str) or not entity_id:
            raise StoreValidationError("tidy_memory target_ref.entity_id must be non-empty string")
        normalized_refs.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
            }
        )
    return normalized_refs


def _refresh_preview_job_idempotency_key(
    *,
    cycle_id: str,
    event_id: str,
    event_updated_at: int,
) -> str:
    return f"refresh_preview:{cycle_id}:{event_id}:{event_updated_at}"


def _embedding_sync_job_idempotency_key(
    *,
    cycle_id: str,
    embedding_model: str,
    targets: list[dict[str, Any]],
) -> str:
    target_tokens = [
        (
            f"{target['entity_type']}:{target['entity_id']}:"
            f"{int(target['source_updated_at'])}:{int(bool(target['current_searchable']))}"
        )
        for target in targets
    ]
    return "embedding_sync:" + cycle_id + ":" + embedding_model + ":" + ":".join(target_tokens)


def _memory_job_error_text(error: Exception) -> str:
    error_message = str(error).strip()
    if not error_message:
        return type(error).__name__
    return f"{type(error).__name__}: {error_message}"[:500]


def _quarantine_memory_job_idempotency_key(
    *,
    cycle_id: str,
    reason_code: str,
    targets: list[dict[str, Any]],
) -> str:
    target_tokens = [
        f"{target['entity_type']}:{target['entity_id']}"
        for target in targets
    ]
    return "quarantine_memory:" + cycle_id + ":" + reason_code + ":" + ":".join(target_tokens)


# Block: Tidy memory job idempotency
def _tidy_memory_job_idempotency_key(
    *,
    cycle_id: str,
    maintenance_scope: str,
    retention_cutoff_at: int,
    target_refs: list[dict[str, str]] | None,
) -> str:
    target_tokens: list[str] = []
    if target_refs:
        target_tokens = [
            f"{target_ref['entity_type']}:{target_ref['entity_id']}"
            for target_ref in target_refs
        ]
    suffix = ":".join(target_tokens)
    if suffix:
        suffix = ":" + suffix
    return f"tidy_memory:{cycle_id}:{maintenance_scope}:{int(retention_cutoff_at)}{suffix}"


def _fetch_events_for_ids(
    *,
    connection: sqlite3.Connection,
    event_ids: list[str],
) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in event_ids)
    rows = connection.execute(
        f"""
        SELECT
            event_id,
            kind,
            searchable,
            observation_summary,
            action_summary,
            result_summary,
            created_at,
            COALESCE(updated_at, created_at) AS source_updated_at
        FROM events
        WHERE event_id IN ({placeholders})
        """,
        tuple(event_ids),
    ).fetchall()
    rows_by_id = {str(row["event_id"]): row for row in rows}
    ordered_rows: list[sqlite3.Row] = []
    for event_id in event_ids:
        row = rows_by_id.get(event_id)
        if row is None:
            raise RuntimeError("source event for write_memory is missing")
        ordered_rows.append(row)
    return ordered_rows


def _fetch_action_history_for_cycle(
    *,
    connection: sqlite3.Connection,
    cycle_id: str,
    action_type: str,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT command_json, observed_effects_json
        FROM action_history
        WHERE cycle_id = ?
          AND action_type = ?
          AND status = 'succeeded'
        ORDER BY started_at ASC
        """,
        (cycle_id, action_type),
    ).fetchall()


def _browse_query_from_action_history(command_json: dict[str, Any]) -> str:
    parameters = command_json.get("parameters")
    if not isinstance(parameters, dict):
        raise RuntimeError("browse action_history.command_json.parameters must be object")
    query = parameters.get("query")
    if not isinstance(query, str) or not query:
        raise RuntimeError("browse action_history.command_json.parameters.query must be non-empty string")
    return query


def _browse_summary_from_action_history(observed_effects_json: dict[str, Any]) -> str:
    summary_text = observed_effects_json.get("summary_text")
    if not isinstance(summary_text, str) or not summary_text:
        raise RuntimeError("browse action_history.observed_effects_json.summary_text must be non-empty string")
    return summary_text


def _browse_task_id_from_action_history(command_json: dict[str, Any]) -> str:
    related_task_id = command_json.get("related_task_id")
    if not isinstance(related_task_id, str) or not related_task_id:
        raise RuntimeError("browse action_history.command_json.related_task_id must be non-empty string")
    return related_task_id


def _build_write_memory_summary_text(
    *,
    primary_event_id: str,
    event_rows: list[sqlite3.Row],
) -> str:
    summary_parts: list[str] = []
    for row in event_rows:
        event_id = str(row["event_id"])
        body = _event_summary_text(row)
        if event_id == primary_event_id:
            summary_parts.append(f"中心:{body}")
            continue
        summary_parts.append(body)
    combined_text = " / ".join(part for part in summary_parts if part)
    if combined_text:
        return combined_text[:1000]
    return "短周期で確定した出来事を要約した記憶"


def _build_event_preview_text(row: sqlite3.Row) -> str:
    preview_text = _event_summary_text(row).strip()
    if preview_text:
        return preview_text[:240]
    return "イベントのプレビューを生成できませんでした"


# Block: vec_items upsert
def _upsert_vec_item_row(
    *,
    connection: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    embedding_model: str,
    embedding_scope: str,
    source_updated_at: int,
    embedding_blob: bytes,
) -> int:
    connection.execute(
        """
        INSERT INTO vec_items (
            vec_item_id,
            entity_type,
            entity_id,
            embedding_model,
            embedding_scope,
            searchable,
            source_updated_at,
            embedding
        )
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(entity_type, entity_id, embedding_model, embedding_scope)
        DO UPDATE SET
            searchable = 1,
            source_updated_at = excluded.source_updated_at,
            embedding = excluded.embedding
        """,
        (
            _opaque_id("vec"),
            entity_type,
            entity_id,
            embedding_model,
            embedding_scope,
            source_updated_at,
            embedding_blob,
        ),
    )
    row = connection.execute(
        """
        SELECT rowid
        FROM vec_items
        WHERE entity_type = ?
          AND entity_id = ?
          AND embedding_model = ?
          AND embedding_scope = ?
        """,
        (entity_type, entity_id, embedding_model, embedding_scope),
    ).fetchone()
    if row is None:
        raise RuntimeError("vec_item row is missing after upsert")
    return int(row["rowid"])


# Block: vec_items unsearchable mark
def _mark_vec_item_unsearchable(
    *,
    connection: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    embedding_model: str,
    embedding_scope: str,
    source_updated_at: int,
) -> int | None:
    row = connection.execute(
        """
        SELECT rowid
        FROM vec_items
        WHERE entity_type = ?
          AND entity_id = ?
          AND embedding_model = ?
          AND embedding_scope = ?
        """,
        (entity_type, entity_id, embedding_model, embedding_scope),
    ).fetchone()
    if row is None:
        return None
    vec_row_id = int(row["rowid"])
    connection.execute(
        """
        UPDATE vec_items
        SET searchable = 0,
            source_updated_at = ?
        WHERE rowid = ?
        """,
        (source_updated_at, vec_row_id),
    )
    return vec_row_id


# Block: vec index replace
def _replace_vec_index_row(
    *,
    connection: sqlite3.Connection,
    vec_row_id: int,
    embedding_blob: bytes,
) -> None:
    connection.execute(
        """
        DELETE FROM vec_items_index
        WHERE rowid = ?
        """,
        (vec_row_id,),
    )
    connection.execute(
        """
        INSERT INTO vec_items_index (rowid, embedding)
        VALUES (?, ?)
        """,
        (vec_row_id, embedding_blob),
    )


# Block: vec index delete
def _delete_vec_index_row(
    *,
    connection: sqlite3.Connection,
    vec_row_id: int,
) -> None:
    connection.execute(
        """
        DELETE FROM vec_items_index
        WHERE rowid = ?
        """,
        (vec_row_id,),
    )


# Block: vec similarity search
def _search_vec_similarity_hits(
    *,
    connection: sqlite3.Connection,
    query_text: str,
    embedding_model: str,
    limit: int,
) -> list[dict[str, Any]]:
    query_blob = _build_embedding_blob(
        source_text=query_text,
        embedding_model=embedding_model,
        embedding_scope="global",
    )
    raw_rows = connection.execute(
        """
        SELECT rowid, distance
        FROM vec_items_index
        WHERE embedding MATCH ?
          AND k = ?
        """,
        (query_blob, limit),
    ).fetchall()
    hits: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for raw_row in raw_rows:
        metadata_row = connection.execute(
            """
            SELECT entity_type, entity_id, searchable
            FROM vec_items
            WHERE rowid = ?
            """,
            (int(raw_row["rowid"]),),
        ).fetchone()
        if metadata_row is None or int(metadata_row["searchable"]) != 1:
            continue
        entity_type = str(metadata_row["entity_type"])
        entity_id = str(metadata_row["entity_id"])
        pair = (entity_type, entity_id)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        hits.append(
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "distance": float(raw_row["distance"]),
            }
        )
    return hits


# Block: Ranked event merge
def _merge_ranked_event_rows(
    *,
    connection: sqlite3.Connection,
    ranked_hits: list[dict[str, Any]],
    fallback_rows: list[sqlite3.Row],
) -> list[sqlite3.Row]:
    ranked_ids = [hit["entity_id"] for hit in ranked_hits if hit["entity_type"] == "event"]
    ranked_rows = _fetch_event_rows_by_ids(
        connection=connection,
        event_ids=ranked_ids,
    )
    merged_rows: list[sqlite3.Row] = []
    seen_ids: set[str] = set()
    for row in ranked_rows + fallback_rows:
        event_id = str(row["event_id"])
        if event_id in seen_ids:
            continue
        seen_ids.add(event_id)
        merged_rows.append(row)
        if len(merged_rows) >= 5:
            break
    return merged_rows


# Block: Ranked memory merge
def _merge_ranked_memory_rows(
    *,
    connection: sqlite3.Connection,
    ranked_hits: list[dict[str, Any]],
    fallback_rows: list[sqlite3.Row],
) -> list[sqlite3.Row]:
    ranked_ids = [hit["entity_id"] for hit in ranked_hits if hit["entity_type"] == "memory_state"]
    ranked_rows = _fetch_memory_rows_by_ids(
        connection=connection,
        memory_state_ids=ranked_ids,
    )
    merged_rows: list[sqlite3.Row] = []
    seen_ids: set[str] = set()
    for row in ranked_rows + fallback_rows:
        memory_state_id = str(row["memory_state_id"])
        if memory_state_id in seen_ids:
            continue
        seen_ids.add(memory_state_id)
        merged_rows.append(row)
        if len(merged_rows) >= 8:
            break
    return merged_rows


# Block: Ranked event fetch
def _fetch_event_rows_by_ids(
    *,
    connection: sqlite3.Connection,
    event_ids: list[str],
) -> list[sqlite3.Row]:
    if not event_ids:
        return []
    placeholder_sql = ",".join("?" for _ in event_ids)
    rows = connection.execute(
        f"""
        SELECT
            event_id,
            source,
            kind,
            observation_summary,
            action_summary,
            result_summary,
            created_at
        FROM events
        WHERE searchable = 1
          AND event_id IN ({placeholder_sql})
        """,
        tuple(event_ids),
    ).fetchall()
    row_map = {str(row["event_id"]): row for row in rows}
    return [row_map[event_id] for event_id in event_ids if event_id in row_map]


# Block: Ranked memory fetch
def _fetch_memory_rows_by_ids(
    *,
    connection: sqlite3.Connection,
    memory_state_ids: list[str],
) -> list[sqlite3.Row]:
    if not memory_state_ids:
        return []
    placeholder_sql = ",".join("?" for _ in memory_state_ids)
    rows = connection.execute(
        f"""
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
          AND memory_kind IN ('summary', 'fact')
          AND memory_state_id IN ({placeholder_sql})
        """,
        tuple(memory_state_ids),
    ).fetchall()
    row_map = {str(row["memory_state_id"]): row for row in rows}
    return [row_map[memory_state_id] for memory_state_id in memory_state_ids if memory_state_id in row_map]


# Block: Settings editor row decode
def _decode_settings_editor_state_row(row: sqlite3.Row) -> dict[str, Any]:
    raw_system_values = json.loads(row["system_values_json"])
    return {
        "active_behavior_preset_id": str(row["active_behavior_preset_id"]),
        "active_llm_preset_id": str(row["active_llm_preset_id"]),
        "active_memory_preset_id": str(row["active_memory_preset_id"]),
        "active_output_preset_id": str(row["active_output_preset_id"]),
        "active_camera_connection_id": (
            str(row["active_camera_connection_id"])
            if row["active_camera_connection_id"] is not None
            else None
        ),
        "system_values": _normalize_settings_editor_system_values(raw_system_values),
        "revision": int(row["revision"]),
        "updated_at": int(row["updated_at"]),
        "last_applied_change_set_id": (
            str(row["last_applied_change_set_id"])
            if row["last_applied_change_set_id"] is not None
            else None
        ),
    }


# Block: Settings editor system values normalization
def _normalize_settings_editor_system_values(raw_system_values: Any) -> dict[str, Any]:
    system_values = {
        key: default_value
        for key, default_value in build_default_settings_editor_state(
            build_default_settings()
        )["system_values_json"].items()
    }
    if not isinstance(raw_system_values, dict):
        return system_values
    for key in build_settings_editor_system_keys():
        if key in raw_system_values:
            system_values[key] = raw_system_values[key]
    return system_values


# Block: Settings preset rows decode
def _decode_settings_preset_catalog_rows(rows: list[sqlite3.Row]) -> dict[str, list[dict[str, Any]]]:
    preset_catalogs = {
        "behavior": [],
        "llm": [],
        "memory": [],
        "output": [],
    }
    for row in rows:
        preset_kind = str(row["preset_kind"])
        payload = json.loads(row["payload_json"])
        if isinstance(payload, dict):
            payload = _normalize_legacy_optional_base_urls(payload)
        preset_catalogs[preset_kind].append(
            {
                "preset_id": str(row["preset_id"]),
                "preset_name": str(row["preset_name"]),
                "archived": bool(row["archived"]),
                "sort_order": int(row["sort_order"]),
                "updated_at": int(row["updated_at"]),
                "payload": payload,
            }
        )
    return preset_catalogs


# Block: Camera connection rows decode
def _decode_camera_connection_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    camera_connections: list[dict[str, Any]] = []
    for row in rows:
        camera_connections.append(
            {
                "camera_connection_id": str(row["camera_connection_id"]),
                "display_name": str(row["display_name"]),
                "host": str(row["host"]),
                "username": str(row["username"]),
                "password": str(row["password"]),
                "sort_order": int(row["sort_order"]),
                "updated_at": int(row["updated_at"]),
            }
        )
    return camera_connections


# Block: Settings editor compare helper
def _canonical_editor_state_for_compare(editor_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "revision": int(editor_state["revision"]),
        "active_behavior_preset_id": str(editor_state["active_behavior_preset_id"]),
        "active_llm_preset_id": str(editor_state["active_llm_preset_id"]),
        "active_memory_preset_id": str(editor_state["active_memory_preset_id"]),
        "active_output_preset_id": str(editor_state["active_output_preset_id"]),
        "active_camera_connection_id": (
            str(editor_state["active_camera_connection_id"])
            if editor_state["active_camera_connection_id"] is not None
            else None
        ),
        "system_values": dict(editor_state["system_values"]),
    }


# Block: Settings editor persistence
def _persist_settings_editor_state(
    *,
    connection: sqlite3.Connection,
    editor_state: dict[str, Any],
) -> None:
    connection.execute(
        """
        UPDATE settings_editor_state
        SET active_behavior_preset_id = ?,
            active_llm_preset_id = ?,
            active_memory_preset_id = ?,
            active_output_preset_id = ?,
            active_camera_connection_id = ?,
            system_values_json = ?,
            revision = ?,
            updated_at = ?,
            last_applied_change_set_id = ?
        WHERE row_id = 1
        """,
        (
            editor_state["active_behavior_preset_id"],
            editor_state["active_llm_preset_id"],
            editor_state["active_memory_preset_id"],
            editor_state["active_output_preset_id"],
            editor_state["active_camera_connection_id"],
            _json_text(editor_state["system_values"]),
            int(editor_state["revision"]),
            int(editor_state["updated_at"]),
            editor_state["last_applied_change_set_id"],
        ),
    )


# Block: Settings preset replace
def _replace_settings_presets(
    *,
    connection: sqlite3.Connection,
    preset_catalogs: dict[str, list[dict[str, Any]]],
    now_ms: int,
) -> None:
    expected_ids = [
        str(preset_entry["preset_id"])
        for preset_kind in ("behavior", "llm", "memory", "output")
        for preset_entry in preset_catalogs[preset_kind]
    ]
    if expected_ids:
        placeholder_sql = ",".join("?" for _ in expected_ids)
        connection.execute(
            f"DELETE FROM settings_presets WHERE preset_id NOT IN ({placeholder_sql})",
            tuple(expected_ids),
        )
    else:
        connection.execute("DELETE FROM settings_presets")
    for preset_kind in ("behavior", "llm", "memory", "output"):
        for preset_entry in preset_catalogs[preset_kind]:
            created_at = int(preset_entry["updated_at"])
            existing_row = connection.execute(
                """
                SELECT created_at
                FROM settings_presets
                WHERE preset_id = ?
                """,
                (preset_entry["preset_id"],),
            ).fetchone()
            if existing_row is not None:
                created_at = int(existing_row["created_at"])
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(preset_id) DO UPDATE SET
                    preset_kind = excluded.preset_kind,
                    preset_name = excluded.preset_name,
                    payload_json = excluded.payload_json,
                    archived = excluded.archived,
                    sort_order = excluded.sort_order,
                    updated_at = excluded.updated_at
                """,
                (
                    preset_entry["preset_id"],
                    preset_kind,
                    preset_entry["preset_name"],
                    _json_text(preset_entry["payload"]),
                    1 if bool(preset_entry["archived"]) else 0,
                    int(preset_entry["sort_order"]),
                    created_at,
                    now_ms,
                ),
            )


# Block: Camera connection replace
def _replace_camera_connections(
    *,
    connection: sqlite3.Connection,
    camera_connections: list[dict[str, Any]],
    now_ms: int,
) -> None:
    expected_ids = [
        str(camera_connection["camera_connection_id"])
        for camera_connection in camera_connections
    ]
    if expected_ids:
        placeholder_sql = ",".join("?" for _ in expected_ids)
        connection.execute(
            f"DELETE FROM camera_connections WHERE camera_connection_id NOT IN ({placeholder_sql})",
            tuple(expected_ids),
        )
    else:
        connection.execute("DELETE FROM camera_connections")
    for camera_connection in camera_connections:
        created_at = int(camera_connection["updated_at"])
        existing_row = connection.execute(
            """
            SELECT created_at
            FROM camera_connections
            WHERE camera_connection_id = ?
            """,
            (camera_connection["camera_connection_id"],),
        ).fetchone()
        if existing_row is not None:
            created_at = int(existing_row["created_at"])
        connection.execute(
            """
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(camera_connection_id) DO UPDATE SET
                display_name = excluded.display_name,
                host = excluded.host,
                username = excluded.username,
                password = excluded.password,
                sort_order = excluded.sort_order,
                updated_at = excluded.updated_at
            """,
            (
                camera_connection["camera_connection_id"],
                camera_connection["display_name"],
                camera_connection["host"],
                camera_connection["username"],
                camera_connection["password"],
                int(camera_connection["sort_order"]),
                created_at,
                now_ms,
            ),
        )


# Block: Settings change set insert
def _insert_settings_change_set(
    *,
    connection: sqlite3.Connection,
    change_set_id: str,
    editor_state: dict[str, Any],
    preset_catalogs: dict[str, list[dict[str, Any]]],
    now_ms: int,
) -> None:
    payload = {
        "editor_revision": int(editor_state["revision"]),
        "active_behavior_preset_id": editor_state["active_behavior_preset_id"],
        "active_llm_preset_id": editor_state["active_llm_preset_id"],
        "active_memory_preset_id": editor_state["active_memory_preset_id"],
        "active_output_preset_id": editor_state["active_output_preset_id"],
        "active_camera_connection_id": editor_state["active_camera_connection_id"],
        "system_values": dict(editor_state["system_values"]),
        "preset_versions": {
            preset_kind: _active_preset_updated_at(
                preset_entries=preset_catalogs[preset_kind],
                preset_id=str(editor_state[f"active_{preset_kind}_preset_id"]),
            )
            for preset_kind in ("behavior", "llm", "memory", "output")
        },
    }
    connection.execute(
        """
        INSERT INTO settings_change_sets (
            change_set_id,
            editor_revision,
            payload_json,
            created_at,
            status
        )
        VALUES (?, ?, ?, ?, 'queued')
        """,
        (
            change_set_id,
            int(editor_state["revision"]),
            _json_text(payload),
            now_ms,
        ),
    )


# Block: Active preset updated_at
def _active_preset_updated_at(
    *,
    preset_entries: list[dict[str, Any]],
    preset_id: str,
) -> int:
    for preset_entry in preset_entries:
        if str(preset_entry["preset_id"]) == preset_id:
            return int(preset_entry["updated_at"])
    raise RuntimeError("active preset id is missing from preset_catalogs")


# Block: Runtime settings from editor
def _materialize_runtime_settings_from_editor(
    *,
    default_settings: dict[str, Any],
    editor_state: dict[str, Any],
    preset_catalogs: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    materialized = dict(default_settings)
    behavior_preset = _active_preset_payload(
        preset_entries=preset_catalogs["behavior"],
        preset_id=str(editor_state["active_behavior_preset_id"]),
    )
    llm_preset = _active_preset_payload(
        preset_entries=preset_catalogs["llm"],
        preset_id=str(editor_state["active_llm_preset_id"]),
    )
    memory_preset = _active_preset_payload(
        preset_entries=preset_catalogs["memory"],
        preset_id=str(editor_state["active_memory_preset_id"]),
    )
    output_preset = _active_preset_payload(
        preset_entries=preset_catalogs["output"],
        preset_id=str(editor_state["active_output_preset_id"]),
    )
    for payload in (behavior_preset, llm_preset, memory_preset, output_preset):
        for key, value in payload.items():
            if key in materialized:
                materialized[key] = value
    for key, value in dict(editor_state["system_values"]).items():
        materialized[key] = value
    return materialized


# Block: Active preset payload helper
def _active_preset_payload(
    *,
    preset_entries: list[dict[str, Any]],
    preset_id: str,
) -> dict[str, Any]:
    for preset_entry in preset_entries:
        if str(preset_entry["preset_id"]) == preset_id:
            return dict(preset_entry["payload"])
    raise RuntimeError("active preset payload is missing")


def _normalize_embedding_scopes(requested_scopes: list[Any]) -> list[str]:
    normalized_scopes: list[str] = []
    for raw_scope in requested_scopes:
        if not isinstance(raw_scope, str):
            raise StoreValidationError("embedding_sync scope must be string")
        if raw_scope not in {"recent", "global"}:
            raise StoreValidationError("embedding_sync scope is invalid")
        if raw_scope not in normalized_scopes:
            normalized_scopes.append(raw_scope)
    if not normalized_scopes:
        raise StoreValidationError("embedding_sync scopes must not be empty")
    return normalized_scopes


def _resolve_embedding_source_text(
    *,
    connection: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
) -> str:
    if entity_type == "event":
        row = connection.execute(
            """
            SELECT preview_text
            FROM event_preview_cache
            WHERE event_id = ?
            """,
            (entity_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("event preview is missing for embedding_sync")
        return str(row["preview_text"])
    if entity_type == "memory_state":
        row = connection.execute(
            """
            SELECT body_text
            FROM memory_states
            WHERE memory_state_id = ?
            """,
            (entity_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("memory_state is missing for embedding_sync")
        return str(row["body_text"])
    if entity_type == "event_affect":
        row = connection.execute(
            """
            SELECT moment_affect_text
            FROM event_affects
            WHERE event_affect_id = ?
            """,
            (entity_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("event_affect is missing for embedding_sync")
        return str(row["moment_affect_text"])
    raise StoreValidationError("embedding_sync target entity_type is invalid")


def _build_embedding_blob(
    *,
    source_text: str,
    embedding_model: str,
    embedding_scope: str,
) -> bytes:
    return sqlite_vec.serialize_float32(
        _build_embedding_vector(
            source_text=source_text,
            embedding_model=embedding_model,
            embedding_scope=embedding_scope,
        )
    )


# Block: Embedding vector build
def _build_embedding_vector(
    *,
    source_text: str,
    embedding_model: str,
    embedding_scope: str,
) -> list[float]:
    del embedding_scope
    tokens = _embedding_source_tokens(source_text)
    vector = [0.0] * EMBEDDING_VECTOR_DIMENSION
    for token in tokens:
        digest = hashlib.sha256(f"{embedding_model}\n{token}".encode("utf-8")).digest()
        for index in range(EMBEDDING_VECTOR_DIMENSION):
            vector[index] += (digest[index] / 127.5) - 1.0
    magnitude = sum(component * component for component in vector) ** 0.5
    if magnitude == 0.0:
        raise RuntimeError("embedding vector magnitude must not be zero")
    return [component / magnitude for component in vector]


# Block: Embedding tokenization
def _embedding_source_tokens(source_text: str) -> list[str]:
    normalized_text = source_text.strip().lower()
    if not normalized_text:
        raise RuntimeError("embedding source text must be non-empty")
    raw_tokens = normalized_text.replace("\n", " ").split(" ")
    tokens = [token for token in raw_tokens if token]
    if not tokens:
        raise RuntimeError("embedding source tokens must not be empty")
    return tokens


def _build_task_snapshot_rows(
    *,
    active_task_rows: list[sqlite3.Row],
    waiting_task_rows: list[sqlite3.Row],
) -> dict[str, Any]:
    return {
        "active_tasks": [
            _task_snapshot_entry(row)
            for row in active_task_rows
        ],
        "waiting_external_tasks": [
            _task_snapshot_entry(row)
            for row in waiting_task_rows
        ],
    }


def _task_snapshot_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "task_id": str(row["task_id"]),
        "task_kind": str(row["task_kind"]),
        "task_status": str(row["task_status"]),
        "goal_hint": str(row["goal_hint"]),
        "completion_hint": json.loads(row["completion_hint_json"]),
        "resume_condition": json.loads(row["resume_condition_json"]),
        "interruptible": bool(row["interruptible"]),
        "priority": int(row["priority"]),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
        "title": str(row["title"]) if row["title"] is not None else None,
        "step_hints": (
            json.loads(row["step_hints_json"])
            if isinstance(row["step_hints_json"], str) and row["step_hints_json"]
            else []
        ),
    }


def _build_memory_snapshot_rows(
    *,
    recent_event_rows: list[sqlite3.Row],
    memory_rows: list[sqlite3.Row],
) -> dict[str, Any]:
    working_memory_items: list[dict[str, Any]] = []
    semantic_items: list[dict[str, Any]] = []
    for row in memory_rows:
        entry = _memory_snapshot_entry(row)
        if str(row["memory_kind"]) == "summary":
            if len(working_memory_items) < 3:
                working_memory_items.append(entry)
            continue
        if str(row["memory_kind"]) == "fact":
            if len(semantic_items) < 3:
                semantic_items.append(entry)
    return {
        "working_memory_items": working_memory_items,
        "episodic_items": [],
        "semantic_items": semantic_items,
        "affective_items": [],
        "relationship_items": [],
        "reflection_items": [],
        "recent_event_window": [
            _recent_event_entry(row)
            for row in recent_event_rows
        ],
    }


def _memory_snapshot_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "memory_state_id": str(row["memory_state_id"]),
        "memory_kind": str(row["memory_kind"]),
        "body_text": str(row["body_text"]),
        "payload": json.loads(row["payload_json"]),
        "confidence": float(row["confidence"]),
        "importance": float(row["importance"]),
        "memory_strength": float(row["memory_strength"]),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
        "last_confirmed_at": int(row["last_confirmed_at"]),
    }


def _recent_event_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": str(row["event_id"]),
        "source": str(row["source"]),
        "kind": str(row["kind"]),
        "summary_text": _event_summary_text(row),
        "created_at": int(row["created_at"]),
    }


def _event_summary_text(row: sqlite3.Row) -> str:
    for key in ("result_summary", "observation_summary", "action_summary"):
        value = row[key]
        if isinstance(value, str) and value:
            return value
    return str(row["kind"])


# Block: Runtime settings helpers
def _merge_runtime_settings(default_settings: dict[str, Any], runtime_values: dict[str, Any]) -> dict[str, Any]:
    merged_settings = dict(default_settings)
    for key, value in runtime_values.items():
        if key in merged_settings:
            merged_settings[key] = value
    return merged_settings


# Block: Runtime settings value normalization
def _normalize_runtime_settings_values(
    *,
    default_settings: dict[str, Any],
    runtime_values: dict[str, Any],
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in default_settings:
        if key in runtime_values:
            normalized[key] = _normalize_legacy_optional_base_url_value(
                key=key,
                value=runtime_values[key],
            )
            continue
        legacy_key = LEGACY_SETTING_KEY_ALIASES.get(key)
        if legacy_key is not None and legacy_key in runtime_values:
            normalized[key] = _normalize_legacy_optional_base_url_value(
                key=key,
                value=runtime_values[legacy_key],
            )
    if "speech.tts.provider" not in normalized:
        if any(legacy_key in runtime_values for legacy_key in LEGACY_AIVIS_RUNTIME_KEYS):
            normalized["speech.tts.provider"] = "aivis-cloud"
    return normalized


# Block: Runtime settings timestamp normalization
def _normalize_runtime_settings_updated_at(
    *,
    default_settings: dict[str, Any],
    current_updated_at: dict[str, Any],
    now_ms: int,
) -> dict[str, int]:
    normalized = _runtime_settings_seed_timestamps(now_ms)
    for key in default_settings:
        if key in current_updated_at:
            timestamp = current_updated_at[key]
        else:
            legacy_key = LEGACY_SETTING_KEY_ALIASES.get(key)
            timestamp = current_updated_at.get(legacy_key) if legacy_key is not None else None
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            continue
        normalized[key] = timestamp
    if "speech.tts.provider" not in current_updated_at:
        for legacy_key in LEGACY_AIVIS_RUNTIME_KEYS:
            timestamp = current_updated_at.get(legacy_key)
            if isinstance(timestamp, bool) or not isinstance(timestamp, int):
                continue
            normalized["speech.tts.provider"] = timestamp
            break
    return normalized


# Block: Legacy optional base URL normalization
def _normalize_legacy_optional_base_urls(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for key in LEGACY_OPTIONAL_BASE_URL_DEFAULTS:
        if key in normalized:
            normalized[key] = _normalize_legacy_optional_base_url_value(
                key=key,
                value=normalized[key],
            )
    return normalized


def _normalize_legacy_optional_base_url_value(*, key: str, value: Any) -> Any:
    legacy_default_value = LEGACY_OPTIONAL_BASE_URL_DEFAULTS.get(key)
    if (
        legacy_default_value is not None
        and isinstance(value, str)
        and value == legacy_default_value
    ):
        return ""
    return value


# Block: Behavior preset migration helper
def _normalize_legacy_behavior_preset_payload(
    *,
    preset_kind: str,
    payload: dict[str, Any],
    default_settings: dict[str, Any],
) -> dict[str, Any]:
    if preset_kind != "behavior":
        return payload
    normalized = {
        "behavior.second_person_label": str(default_settings["behavior.second_person_label"]),
        "behavior.system_prompt": str(default_settings["behavior.system_prompt"]),
        "behavior.addon_prompt": str(default_settings["behavior.addon_prompt"]),
        "behavior.response_pace": str(default_settings["behavior.response_pace"]),
        "behavior.proactivity_level": str(default_settings["behavior.proactivity_level"]),
        "behavior.browse_preference": str(default_settings["behavior.browse_preference"]),
        "behavior.notify_preference": str(default_settings["behavior.notify_preference"]),
        "behavior.speech_style": str(default_settings["behavior.speech_style"]),
        "behavior.verbosity_bias": str(default_settings["behavior.verbosity_bias"]),
    }
    if set(payload) == set(normalized):
        return payload
    current_key_map = {
        "behavior.second_person_label": "behavior.second_person_label",
        "behavior.system_prompt": "behavior.system_prompt",
        "behavior.addon_prompt": "behavior.addon_prompt",
        "behavior.response_pace": "behavior.response_pace",
        "behavior.proactivity_level": "behavior.proactivity_level",
        "behavior.browse_preference": "behavior.browse_preference",
        "behavior.notify_preference": "behavior.notify_preference",
        "behavior.speech_style": "behavior.speech_style",
        "behavior.verbosity_bias": "behavior.verbosity_bias",
        "response_pace": "behavior.response_pace",
        "proactivity_level": "behavior.proactivity_level",
        "browse_preference": "behavior.browse_preference",
        "notify_preference": "behavior.notify_preference",
        "speech_style": "behavior.speech_style",
        "verbosity_bias": "behavior.verbosity_bias",
        "second_person_label": "behavior.second_person_label",
        "system_prompt": "behavior.system_prompt",
        "addon_prompt": "behavior.addon_prompt",
    }
    for source_key, target_key in current_key_map.items():
        if source_key in payload:
            normalized[target_key] = payload[source_key]
    response_pace_map = {
        "calm": "careful",
        "normal": "balanced",
    }
    speech_style_map = {
        "soft": "gentle",
        "formal": "firm",
    }
    response_pace = normalized["behavior.response_pace"]
    if response_pace in response_pace_map:
        normalized["behavior.response_pace"] = response_pace_map[response_pace]
    speech_style = normalized["behavior.speech_style"]
    if speech_style in speech_style_map:
        normalized["behavior.speech_style"] = speech_style_map[speech_style]
    return normalized


# Block: Output preset migration helper
def _normalize_legacy_output_preset_payload(
    *,
    preset_kind: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if preset_kind != "output":
        return payload
    if set(payload) == set(OUTPUT_PRESET_SETTING_KEYS):
        return payload
    default_settings = build_default_settings()
    normalized = build_default_output_preset_payload(default_settings)
    for key in OUTPUT_PRESET_SETTING_KEYS:
        value = payload.get(key)
        if value is not None:
            normalized[key] = value
    legacy_aivis_key_map = {
        "speech.tts.api_key": "speech.tts.aivis_cloud.api_key",
        "speech.tts.endpoint_url": "speech.tts.aivis_cloud.endpoint_url",
        "speech.tts.model_uuid": "speech.tts.aivis_cloud.model_uuid",
        "speech.tts.speaker_uuid": "speech.tts.aivis_cloud.speaker_uuid",
        "speech.tts.style_id": "speech.tts.aivis_cloud.style_id",
        "speech.tts.language": "speech.tts.aivis_cloud.language",
        "speech.tts.speaking_rate": "speech.tts.aivis_cloud.speaking_rate",
        "speech.tts.emotional_intensity": "speech.tts.aivis_cloud.emotional_intensity",
        "speech.tts.tempo_dynamics": "speech.tts.aivis_cloud.tempo_dynamics",
        "speech.tts.pitch": "speech.tts.aivis_cloud.pitch",
        "speech.tts.volume": "speech.tts.aivis_cloud.volume",
        "speech.tts.output_format": "speech.tts.aivis_cloud.output_format",
    }
    saw_legacy_aivis_key = False
    for legacy_key, normalized_key in legacy_aivis_key_map.items():
        if legacy_key in payload:
            normalized[normalized_key] = payload[legacy_key]
            saw_legacy_aivis_key = True
    if "speech.tts.enabled" in payload:
        normalized["speech.tts.enabled"] = payload["speech.tts.enabled"]
    if saw_legacy_aivis_key:
        normalized["speech.tts.provider"] = "aivis-cloud"
    required_tts_keys = (
        "speech.tts.aivis_cloud.api_key",
        "speech.tts.aivis_cloud.endpoint_url",
        "speech.tts.aivis_cloud.model_uuid",
        "speech.tts.aivis_cloud.speaker_uuid",
    )
    legacy_output_mode = payload.get("output.mode")
    if legacy_output_mode == "ui_only":
        normalized["speech.tts.enabled"] = False
    elif legacy_output_mode == "ui_and_tts":
        normalized["speech.tts.enabled"] = all(
            isinstance(normalized[key], str) and normalized[key].strip()
            for key in required_tts_keys
        )
        normalized["speech.tts.provider"] = "aivis-cloud"
    legacy_notify_route = payload.get("integrations.notify_route")
    if legacy_notify_route in {"ui_only", "discord"}:
        normalized["integrations.notify_route"] = legacy_notify_route
    legacy_discord_token = payload.get("integrations.discord.bot_token")
    if isinstance(legacy_discord_token, str):
        normalized["integrations.discord.bot_token"] = legacy_discord_token
    legacy_discord_channel = payload.get("integrations.discord.channel_id")
    if isinstance(legacy_discord_channel, str):
        normalized["integrations.discord.channel_id"] = legacy_discord_channel
    if normalized["speech.tts.enabled"] is True:
        if not all(
            isinstance(normalized[key], str) and normalized[key].strip()
            for key in required_tts_keys
        ):
            normalized["speech.tts.enabled"] = False
    return normalized


def _runtime_settings_seed_timestamps(now_ms: int) -> dict[str, int]:
    return {key: now_ms for key in build_default_settings()}


def _upsert_runtime_setting_value(
    *,
    connection: sqlite3.Connection,
    key: str,
    value: Any,
    applied_at: int,
) -> None:
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
    values[key] = value
    value_updated_at[key] = applied_at
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
            applied_at,
        ),
    )


# Block: Generic helpers
def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _opaque_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _now_ms() -> int:
    return int(time.time() * 1000)
