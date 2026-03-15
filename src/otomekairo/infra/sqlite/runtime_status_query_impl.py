"""SQLite の runtime 状態 query 実装。"""

from __future__ import annotations

import json
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite_store_legacy_runtime import _merge_runtime_settings, _now_ms
from otomekairo.infra.sqlite_store_runtime_view import (
    _public_body_state_summary,
    _public_drive_state_summary,
    _public_emotion_summary,
    _public_primary_focus,
    _public_world_state_summary,
)


# Block: ヘルス読み出し
def read_health() -> dict[str, Any]:
    return {"status": "ok", "server_time": _now_ms()}


# Block: 状態読み出し
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


# Block: 実効設定読み出し
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


# Block: ランタイム作業状態読み出し
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
