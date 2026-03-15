"""SQLite の runtime live state 置換処理。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from otomekairo.infra.sqlite_store_legacy_runtime import _json_text
from otomekairo.infra.sqlite_store_runtime_view import (
    _decode_body_state_row,
    _decode_world_state_row,
)
from otomekairo.infra.sqlite_store_snapshots import _task_snapshot_entry
from otomekairo.schema.store_errors import StoreValidationError
from otomekairo.usecase.runtime_live_state import build_runtime_live_state


# Block: 注意状態置換
def replace_attention_state(
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


# Block: 身体状態置換
def replace_body_state(
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


# Block: 世界状態置換
def replace_world_state(
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


# Block: 駆動状態置換
def replace_drive_state(
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


# Block: runtime live state 同期
def sync_runtime_live_state(
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
    replace_body_state(connection=connection, body_state=live_state["body_state"])
    replace_world_state(connection=connection, world_state=live_state["world_state"])
    replace_drive_state(connection=connection, drive_state=live_state["drive_state"])
