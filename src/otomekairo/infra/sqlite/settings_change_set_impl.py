"""SQLite の settings change set と起動時 materialize 処理。"""

from __future__ import annotations

import json
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite.runtime_live_state_impl import sync_runtime_live_state
from otomekairo.infra.sqlite_store_legacy_runtime import _json_text, _now_ms
from otomekairo.infra.sqlite_store_settings_editor import (
    _decode_settings_editor_state_row,
    _decode_settings_preset_rows,
    _fetch_editor_preset_rows,
    _materialize_effective_settings_from_editor,
)
from otomekairo.schema.runtime_types import SettingsChangeSetRecord
from otomekairo.schema.settings import decode_requested_value
from otomekairo.schema.store_errors import StoreConflictError, StoreValidationError


# Block: settings change set claim
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


# Block: settings change set 確定
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


# Block: 次回起動 settings 反映
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
