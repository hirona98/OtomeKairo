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

from otomekairo.schema.runtime_types import (
    ActionHistoryRecord,
    CognitionStateSnapshot,
    MemoryJobRecord,
    PendingInputRecord,
    SettingsOverrideRecord,
)
from otomekairo.schema.settings import build_default_settings, decode_requested_value


# Block: Schema constants
SCHEMA_NAME = "core_schema"
SCHEMA_VERSION = 3


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
            self._ensure_db_meta(connection, now_ms)
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

    # Block: Cognition snapshot
    def read_cognition_state(self, default_settings: dict[str, Any]) -> CognitionStateSnapshot:
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
        if (
            self_row is None
            or attention_row is None
            or body_row is None
            or world_row is None
            or drive_row is None
            or runtime_settings_row is None
        ):
            raise RuntimeError("singleton state rows are missing")
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
            effective_settings=_merge_runtime_settings(
                default_settings,
                json.loads(runtime_settings_row["values_json"]),
            ),
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
    def enqueue_chat_message(self, *, text: str, client_message_id: str | None) -> dict[str, Any]:
        stripped_text = text.strip()
        if not stripped_text:
            raise StoreValidationError("text must not be blank")
        if len(stripped_text) > 4000:
            raise StoreValidationError("text is too long")
        input_id = _opaque_id("inp")
        now_ms = _now_ms()
        payload = {"input_kind": "chat_message", "text": stripped_text}
        if client_message_id:
            payload["client_message_id"] = client_message_id
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
                    VALUES (?, 'web_input', 'browser_chat', ?, ?, ?, ?, 'queued')
                    """,
                    (
                        input_id,
                        client_message_id,
                        json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
                        now_ms,
                        100,
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
                    memory_job_payloads.payload_json
                FROM memory_jobs
                JOIN memory_job_payloads
                  ON json_extract(memory_jobs.payload_ref_json, '$.payload_id') = memory_job_payloads.payload_id
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
        return MemoryJobRecord(
            job_id=row["job_id"],
            job_kind=row["job_kind"],
            created_at=int(row["created_at"]),
            payload=json.loads(row["payload_json"]),
        )

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
            memory_state_id = _opaque_id("mem")
            summary_text = _build_write_memory_summary_text(
                primary_event_id=str(memory_job.payload["primary_event_id"]),
                event_rows=event_rows,
            )
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
                VALUES (?, 'summary', ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    memory_state_id,
                    summary_text,
                    _json_text(
                        {
                            "source_job_id": memory_job.job_id,
                            "job_kind": memory_job.job_kind,
                            "source_cycle_id": memory_job.payload["cycle_id"],
                            "primary_event_id": memory_job.payload["primary_event_id"],
                            "source_event_ids": source_event_ids,
                            "summary_kind": "minimal_write_memory",
                        }
                    ),
                    0.50,
                    0.50,
                    0.50,
                    now_ms,
                    _json_text(source_event_ids),
                    now_ms,
                    now_ms,
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
                            "memory_kind": "summary",
                            "body_text": summary_text,
                            "source_job_id": memory_job.job_id,
                            "source_event_ids": source_event_ids,
                        }
                    ),
                    "write_memory created summary",
                    _json_text(source_event_ids),
                    now_ms,
                ),
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
                targets=[
                    {
                        "entity_type": "memory_state",
                        "entity_id": memory_state_id,
                        "source_updated_at": now_ms,
                        "current_searchable": True,
                    }
                ],
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
        return memory_state_id

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
                    else:
                        connection.execute(
                            """
                            UPDATE vec_items
                            SET searchable = 0,
                                source_updated_at = ?
                            WHERE entity_type = ?
                              AND entity_id = ?
                              AND embedding_model = ?
                              AND embedding_scope = ?
                            """,
                            (
                                source_updated_at,
                                entity_type,
                                entity_id,
                                embedding_model,
                                embedding_scope,
                            ),
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
        merged_values = dict(default_values)
        merged_values.update(current_values)
        merged_updated_at = _runtime_settings_seed_timestamps(now_ms)
        merged_updated_at.update(current_updated_at)
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
        ui_events: list[dict[str, Any]],
        commit_payload: dict[str, Any],
        discard_reason: str | None = None,
    ) -> int:
        if resolution_status not in {"consumed", "discarded"}:
            raise StoreValidationError("resolution_status is invalid")
        resolved_at = _now_ms()
        with self._connect() as connection:
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

    def _insert_memory_job(
        self,
        *,
        connection: sqlite3.Connection,
        job_kind: str,
        payload_json: dict[str, Any],
        idempotency_key: str,
        created_at: int,
    ) -> str:
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
        input_journal_refs_json: str,
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
        if current_version != 2:
            raise RuntimeError("unsupported schema_version for migration")
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
            (_json_text(SCHEMA_VERSION), now_ms),
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
            ON CONFLICT(row_id) DO NOTHING
            """,
            (
                _json_text({}),
                _json_text({}),
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
        text = str(pending_input.payload["text"])
        trimmed_text = text[:60]
        return f"chat_message:{trimmed_text}"
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
    return action_result.action_type


def _action_result_summary(action_result: ActionHistoryRecord) -> str:
    if action_result.failure_mode:
        return f"{action_result.action_type} {action_result.status}: {action_result.failure_mode}"
    return f"{action_result.action_type} {action_result.status}"


# Block: Memory job helpers
def _write_memory_job_idempotency_key(*, cycle_id: str, event_ids: list[str]) -> str:
    return "write_memory:" + cycle_id + ":" + ":".join(event_ids)


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
    payload = f"{embedding_model}\n{embedding_scope}\n{source_text}".encode("utf-8")
    return hashlib.sha256(payload).digest()


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
