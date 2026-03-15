"""SQLite の settings override 処理。"""

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
from otomekairo.infra.sqlite.runtime_live_state_impl import sync_runtime_live_state
from otomekairo.infra.sqlite.runtime_query_impl import read_effective_settings
from otomekairo.infra.sqlite_store_legacy_runtime import (
    _json_text,
    _now_ms,
    _opaque_id,
    _upsert_runtime_setting_value,
)
from otomekairo.schema.runtime_types import SettingsOverrideRecord
from otomekairo.schema.settings import decode_requested_value
from otomekairo.schema.store_errors import StoreConflictError, StoreValidationError


# Block: settings 一覧参照
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


# Block: settings override enqueue
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


# Block: settings override claim
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


# Block: settings override journal 追記
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


# Block: settings override 確定
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
