"""SQLite の settings editor query 実装。"""

from __future__ import annotations

from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite_store_settings_editor import (
    _decode_camera_connection_rows,
    _decode_settings_editor_state_row,
    _decode_settings_preset_rows,
    _fetch_editor_preset_rows,
)
from otomekairo.schema.settings import build_settings_editor_system_keys


# Block: 設定 editor 読み出し
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


# Block: 有効カメラ接続読み出し
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
