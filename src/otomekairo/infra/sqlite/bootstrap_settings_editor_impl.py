"""SQLite bootstrap 設定 editor 初期化処理。"""

from __future__ import annotations

import json
import sqlite3

from otomekairo.infra.sqlite.bootstrap_connection_impl import table_column_names
from otomekairo.infra.sqlite_store_legacy_runtime import (
    _json_text,
    _merge_runtime_settings,
    _normalize_runtime_settings_updated_at,
    _normalize_runtime_settings_values,
)
from otomekairo.infra.sqlite_store_settings_editor import _normalize_settings_editor_system_values
from otomekairo.schema.settings import (
    build_default_camera_connections,
    build_default_settings,
    build_default_settings_editor_presets,
    build_default_settings_editor_state,
)


# Block: プリセット対象テーブル
SETTINGS_EDITOR_PRESET_TABLE_NAMES = (
    "character_presets",
    "behavior_presets",
    "conversation_presets",
    "memory_presets",
    "motion_presets",
)


# Block: runtime_settings 既定値補完
def ensure_runtime_settings_defaults(
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
    if merged_values == current_values and merged_updated_at == current_updated_at:
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


# Block: settings editor 既定値補完
def ensure_settings_editor_defaults(
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
    # Block: system 値正規化
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


# Block: settings editor スキーマ検証
def verify_settings_editor_schema(
    *,
    connection: sqlite3.Connection,
) -> None:
    settings_editor_columns = table_column_names(
        connection=connection,
        table_name="settings_editor_state",
    )
    expected_settings_editor_columns = {
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
    if settings_editor_columns != expected_settings_editor_columns:
        raise RuntimeError("settings_editor_state schema does not match current core_schema")
    camera_column_names = table_column_names(
        connection=connection,
        table_name="camera_connections",
    )
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
        raise RuntimeError("camera_connections schema does not match current core_schema")
    for table_name in SETTINGS_EDITOR_PRESET_TABLE_NAMES:
        preset_column_names = table_column_names(
            connection=connection,
            table_name=table_name,
        )
        if preset_column_names != {
            "preset_id",
            "preset_name",
            "payload_json",
            "archived",
            "sort_order",
            "created_at",
            "updated_at",
        }:
            raise RuntimeError(f"{table_name} schema does not match current core_schema")
