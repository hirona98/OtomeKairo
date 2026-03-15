"""SQLite settings and settings editor implementations."""

from __future__ import annotations

import json
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite.event_writer_impl import (
    append_input_journal,
    insert_settings_override_events,
)
from otomekairo.infra.sqlite.memory_job_impl import enqueue_write_memory_jobs
from otomekairo.infra.sqlite.runtime_lease_impl import sync_commit_log
from otomekairo.infra.sqlite.runtime_query_impl import (
    read_effective_settings,
    read_settings_editor,
)
from otomekairo.infra.sqlite.runtime_live_state_impl import sync_runtime_live_state
from otomekairo.infra.sqlite_store_legacy_runtime import (
    _json_text,
    _now_ms,
    _opaque_id,
    _upsert_runtime_setting_value,
)
from otomekairo.infra.sqlite_store_settings_editor import (
    _canonical_editor_state_for_compare,
    _decode_camera_connection_rows,
    _decode_settings_editor_state_row,
    _decode_settings_preset_rows,
    _fetch_editor_preset_rows,
    _insert_settings_change_set,
    _materialize_effective_settings_from_editor,
    _persist_settings_editor_state,
    _replace_camera_connections,
    _replace_editor_preset_rows,
)
from otomekairo.schema.runtime_types import SettingsChangeSetRecord, SettingsOverrideRecord
from otomekairo.schema.settings import decode_requested_value, normalize_settings_editor_document
from otomekairo.schema.store_errors import StoreConflictError, StoreValidationError


# Block: Settings snapshot read
def read_settings(
    backend: SqliteBackend,
    default_settings: dict[str, Any],
) -> dict[str, Any]:
    with backend._connect() as connection:
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
        "effective_settings": read_effective_settings(backend, default_settings),
        "pending_overrides": pending_overrides,
    }


# Block: Settings editor save
def save_settings_editor(
    backend: SqliteBackend,
    *,
    document: dict[str, Any],
) -> dict[str, Any]:
    normalized_document = normalize_settings_editor_document(document)
    now_ms = _now_ms()
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
            return read_settings_editor(backend)
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
        _insert_settings_change_set(
            connection=connection,
            change_set_id=_opaque_id("setchg"),
            editor_state=saved_editor_state,
            character_presets=normalized_document["character_presets"],
            behavior_presets=normalized_document["behavior_presets"],
            conversation_presets=normalized_document["conversation_presets"],
            memory_presets=normalized_document["memory_presets"],
            motion_presets=normalized_document["motion_presets"],
            now_ms=now_ms,
        )
    return read_settings_editor(backend)


# Block: Settings override claim
def claim_next_settings_override(backend: SqliteBackend) -> SettingsOverrideRecord | None:
    now_ms = _now_ms()
    with backend._connect() as connection:
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


# Block: Settings override journal append
def append_input_journal_for_settings_override(
    backend: SqliteBackend,
    *,
    settings_override: SettingsOverrideRecord,
    cycle_id: str,
) -> None:
    append_input_journal(
        backend,
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


# Block: Settings override finalize
def finalize_settings_override(
    backend: SqliteBackend,
    *,
    override_id: str,
    key: str,
    requested_value_json: dict[str, Any],
    apply_scope: str,
    cycle_id: str,
    final_status: str,
    reject_reason: str | None,
    camera_available: bool,
) -> int:
    if final_status not in {"applied", "rejected"}:
        raise StoreValidationError("final_status is invalid")
    resolved_at = _now_ms()
    with backend._connect() as connection:
        if final_status == "applied" and apply_scope == "runtime":
            applied_value = decode_requested_value(key, requested_value_json)
            _upsert_runtime_setting_value(
                connection=connection,
                key=key,
                value=applied_value,
                applied_at=resolved_at,
            )
            sync_runtime_live_state(
                connection=connection,
                camera_available=camera_available,
                updated_at=resolved_at,
                cycle_context=None,
            )
        event_ids = insert_settings_override_events(
            connection=connection,
            override_id=override_id,
            cycle_id=cycle_id,
            key=key,
            apply_scope=apply_scope,
            final_status=final_status,
            reject_reason=reject_reason,
            resolved_at=resolved_at,
        )
        enqueued_memory_job_ids = enqueue_write_memory_jobs(
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
    sync_commit_log(backend, commit_id=commit_id)
    return commit_id


# Block: Settings change set claim
def claim_next_settings_change_set(backend: SqliteBackend) -> SettingsChangeSetRecord | None:
    now_ms = _now_ms()
    with backend._connect() as connection:
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
    backend: SqliteBackend,
    *,
    change_set: SettingsChangeSetRecord,
    default_settings: dict[str, Any],
    final_status: str,
    reject_reason: str | None,
    camera_available: bool,
) -> None:
    if final_status not in {"applied", "rejected"}:
        raise StoreValidationError("settings change set final_status is invalid")
    resolved_at = _now_ms()
    with backend._connect() as connection:
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
                sync_runtime_live_state(
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
def materialize_next_boot_settings(backend: SqliteBackend) -> None:
    with backend._connect() as connection:
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


# Block: Settings override enqueue
def enqueue_settings_override(
    backend: SqliteBackend,
    *,
    key: str,
    requested_value_json: dict[str, Any],
    apply_scope: str,
) -> dict[str, Any]:
    override_id = _opaque_id("ovr")
    now_ms = _now_ms()
    with backend._connect() as connection:
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
