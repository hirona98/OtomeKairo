"""SQLite の settings editor 保存処理。"""

from __future__ import annotations

from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite.runtime_query_impl import read_settings_editor
from otomekairo.infra.sqlite_store_legacy_runtime import _now_ms, _opaque_id
from otomekairo.infra.sqlite_store_settings_editor import (
    _canonical_editor_state_for_compare,
    _decode_camera_connection_rows,
    _decode_settings_editor_state_row,
    _decode_settings_preset_rows,
    _fetch_editor_preset_rows,
    _insert_settings_change_set,
    _persist_settings_editor_state,
    _replace_camera_connections,
    _replace_editor_preset_rows,
)
from otomekairo.schema.settings import normalize_settings_editor_document
from otomekairo.schema.store_errors import StoreConflictError


# Block: settings editor 保存
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
