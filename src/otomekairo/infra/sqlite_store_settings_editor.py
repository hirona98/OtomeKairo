"""Settings editor helper functions for the SQLite state store."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from otomekairo.schema.settings import (
    build_default_settings,
    build_default_settings_editor_state,
    build_settings_editor_system_keys,
    normalize_retrieval_profile,
)


# Block: Settings editor row decode
def _decode_settings_editor_state_row(row: sqlite3.Row) -> dict[str, Any]:
    raw_system_values = json.loads(row["system_values_json"])
    return {
        "active_character_preset_id": str(row["active_character_preset_id"]),
        "active_behavior_preset_id": str(row["active_behavior_preset_id"]),
        "active_conversation_preset_id": str(row["active_conversation_preset_id"]),
        "active_memory_preset_id": str(row["active_memory_preset_id"]),
        "active_motion_preset_id": str(row["active_motion_preset_id"]),
        "system_values": _normalize_settings_editor_system_values(raw_system_values),
        "revision": int(row["revision"]),
        "updated_at": int(row["updated_at"]),
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


# Block: Settings preset rows fetch
def _fetch_editor_preset_rows(
    *,
    connection: sqlite3.Connection,
    table_name: str,
) -> list[sqlite3.Row]:
    return connection.execute(
        f"""
        SELECT
            preset_id,
            preset_name,
            payload_json,
            archived,
            sort_order,
            created_at,
            updated_at
        FROM {table_name}
        ORDER BY sort_order ASC, updated_at DESC
        """
    ).fetchall()


# Block: Settings preset rows decode
def _decode_settings_preset_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    preset_entries: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        preset_entries.append(
            {
                "preset_id": str(row["preset_id"]),
                "preset_name": str(row["preset_name"]),
                "archived": bool(row["archived"]),
                "sort_order": int(row["sort_order"]),
                "updated_at": int(row["updated_at"]),
                "payload": payload,
            }
        )
    return preset_entries


# Block: Active retrieval profile read
def _read_active_retrieval_profile(*, connection: sqlite3.Connection) -> dict[str, Any]:
    editor_row = connection.execute(
        """
        SELECT active_memory_preset_id
        FROM settings_editor_state
        WHERE row_id = 1
        """
    ).fetchone()
    if editor_row is None:
        raise RuntimeError("settings_editor_state row is missing")
    memory_row = connection.execute(
        """
        SELECT payload_json
        FROM memory_presets
        WHERE preset_id = ?
        """,
        (str(editor_row["active_memory_preset_id"]),),
    ).fetchone()
    if memory_row is None:
        raise RuntimeError("active memory preset is missing")
    payload = json.loads(memory_row["payload_json"])
    if not isinstance(payload, dict):
        raise RuntimeError("memory_presets.payload_json must be object")
    return normalize_retrieval_profile(payload.get("retrieval_profile"))


# Block: Camera connection rows decode
def _decode_camera_connection_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    camera_connections: list[dict[str, Any]] = []
    for row in rows:
        camera_connections.append(
            {
                "camera_connection_id": str(row["camera_connection_id"]),
                "is_enabled": bool(row["is_enabled"]),
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
        "active_character_preset_id": str(editor_state["active_character_preset_id"]),
        "active_behavior_preset_id": str(editor_state["active_behavior_preset_id"]),
        "active_conversation_preset_id": str(editor_state["active_conversation_preset_id"]),
        "active_memory_preset_id": str(editor_state["active_memory_preset_id"]),
        "active_motion_preset_id": str(editor_state["active_motion_preset_id"]),
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
        SET active_character_preset_id = ?,
            active_behavior_preset_id = ?,
            active_conversation_preset_id = ?,
            active_memory_preset_id = ?,
            active_motion_preset_id = ?,
            system_values_json = ?,
            revision = ?,
            updated_at = ?
        WHERE row_id = 1
        """,
        (
            editor_state["active_character_preset_id"],
            editor_state["active_behavior_preset_id"],
            editor_state["active_conversation_preset_id"],
            editor_state["active_memory_preset_id"],
            editor_state["active_motion_preset_id"],
            _json_text(editor_state["system_values"]),
            int(editor_state["revision"]),
            int(editor_state["updated_at"]),
        ),
    )


# Block: Settings preset replace
def _replace_editor_preset_rows(
    *,
    connection: sqlite3.Connection,
    table_name: str,
    preset_entries: list[dict[str, Any]],
    now_ms: int,
) -> None:
    expected_ids = [
        str(preset_entry["preset_id"])
        for preset_entry in preset_entries
    ]
    if expected_ids:
        placeholder_sql = ",".join("?" for _ in expected_ids)
        connection.execute(
            f"DELETE FROM {table_name} WHERE preset_id NOT IN ({placeholder_sql})",
            tuple(expected_ids),
        )
    else:
        connection.execute(f"DELETE FROM {table_name}")
    for preset_entry in preset_entries:
        created_at = int(preset_entry["updated_at"])
        existing_row = connection.execute(
            f"""
            SELECT created_at
            FROM {table_name}
            WHERE preset_id = ?
            """,
            (preset_entry["preset_id"],),
        ).fetchone()
        if existing_row is not None:
            created_at = int(existing_row["created_at"])
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
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(preset_id) DO UPDATE SET
                preset_name = excluded.preset_name,
                payload_json = excluded.payload_json,
                archived = excluded.archived,
                sort_order = excluded.sort_order,
                updated_at = excluded.updated_at
            """,
            (
                preset_entry["preset_id"],
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
            ON CONFLICT(camera_connection_id) DO UPDATE SET
                is_enabled = excluded.is_enabled,
                display_name = excluded.display_name,
                host = excluded.host,
                username = excluded.username,
                password = excluded.password,
                sort_order = excluded.sort_order,
                updated_at = excluded.updated_at
            """,
            (
                camera_connection["camera_connection_id"],
                1 if bool(camera_connection["is_enabled"]) else 0,
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
    character_presets: list[dict[str, Any]],
    behavior_presets: list[dict[str, Any]],
    conversation_presets: list[dict[str, Any]],
    memory_presets: list[dict[str, Any]],
    motion_presets: list[dict[str, Any]],
    now_ms: int,
) -> None:
    payload = {
        "editor_revision": int(editor_state["revision"]),
        "active_character_preset_id": editor_state["active_character_preset_id"],
        "active_behavior_preset_id": editor_state["active_behavior_preset_id"],
        "active_conversation_preset_id": editor_state["active_conversation_preset_id"],
        "active_memory_preset_id": editor_state["active_memory_preset_id"],
        "active_motion_preset_id": editor_state["active_motion_preset_id"],
        "system_values": dict(editor_state["system_values"]),
        "preset_versions": {
            "character": _active_preset_updated_at(
                preset_entries=character_presets,
                preset_id=str(editor_state["active_character_preset_id"]),
            ),
            "behavior": _active_preset_updated_at(
                preset_entries=behavior_presets,
                preset_id=str(editor_state["active_behavior_preset_id"]),
            ),
            "conversation": _active_preset_updated_at(
                preset_entries=conversation_presets,
                preset_id=str(editor_state["active_conversation_preset_id"]),
            ),
            "memory": _active_preset_updated_at(
                preset_entries=memory_presets,
                preset_id=str(editor_state["active_memory_preset_id"]),
            ),
            "motion": _active_preset_updated_at(
                preset_entries=motion_presets,
                preset_id=str(editor_state["active_motion_preset_id"]),
            ),
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
    raise RuntimeError("active preset id is missing from preset entries")


# Block: Runtime settings from editor
def _materialize_effective_settings_from_editor(
    *,
    default_settings: dict[str, Any],
    editor_state: dict[str, Any],
    character_presets: list[dict[str, Any]],
    behavior_presets: list[dict[str, Any]],
    conversation_presets: list[dict[str, Any]],
    memory_presets: list[dict[str, Any]],
    motion_presets: list[dict[str, Any]],
) -> dict[str, Any]:
    materialized = dict(default_settings)

    # Block: Resolve active preset payloads
    character_preset = _active_preset_payload(
        preset_entries=character_presets,
        preset_id=str(editor_state["active_character_preset_id"]),
    )
    behavior_preset = _active_preset_payload(
        preset_entries=behavior_presets,
        preset_id=str(editor_state["active_behavior_preset_id"]),
    )
    conversation_preset = _active_preset_payload(
        preset_entries=conversation_presets,
        preset_id=str(editor_state["active_conversation_preset_id"]),
    )
    memory_preset = _active_preset_payload(
        preset_entries=memory_presets,
        preset_id=str(editor_state["active_memory_preset_id"]),
    )
    motion_preset = _active_preset_payload(
        preset_entries=motion_presets,
        preset_id=str(editor_state["active_motion_preset_id"]),
    )

    # Block: Materialize scalar settings
    for payload in (
        character_preset,
        behavior_preset,
        conversation_preset,
        memory_preset,
        motion_preset,
    ):
        for key, value in payload.items():
            if key in materialized:
                materialized[key] = value

    # Block: Apply system overrides
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


# Block: JSON helper
def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
