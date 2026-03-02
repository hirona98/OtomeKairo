"""SQLite-backed state and control plane access."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from otomekairo.schema.settings import decode_requested_value


# Block: Schema constants
SCHEMA_NAME = "core_schema"
SCHEMA_VERSION = 3


# Block: API errors
class StoreConflictError(RuntimeError):
    pass


class StoreValidationError(ValueError):
    pass


# Block: Bootstrap result
@dataclass(frozen=True, slots=True)
class BootstrapResult:
    db_path: Path
    initialized_at: int


# Block: Pending input record
@dataclass(frozen=True, slots=True)
class PendingInputRecord:
    input_id: str
    source: str
    channel: str
    created_at: int
    payload: dict[str, Any]


# Block: Settings override record
@dataclass(frozen=True, slots=True)
class SettingsOverrideRecord:
    override_id: str
    key: str
    requested_value_json: dict[str, Any]
    apply_scope: str
    created_at: int


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
            raise StoreConflictError("duplicate client_message_id") from error
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
        input_id: str,
        cycle_id: str,
        resolution_status: str,
        ui_events: list[dict[str, Any]],
        commit_payload: dict[str, Any],
        discard_reason: str | None = None,
    ) -> int:
        if resolution_status not in {"consumed", "discarded"}:
            raise StoreValidationError("resolution_status is invalid")
        resolved_at = _now_ms()
        with self._connect() as connection:
            for ui_event in ui_events:
                connection.execute(
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
                        ui_event["channel"],
                        ui_event["event_type"],
                        _json_text(ui_event["payload"]),
                        resolved_at,
                        cycle_id,
                    ),
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
                (resolution_status, resolved_at, discard_reason, input_id),
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
                (cycle_id, resolved_at, _json_text(commit_payload)),
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
                _json_text({}),
                _json_text({}),
                now_ms,
            ),
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


# Block: Runtime settings helpers
def _merge_runtime_settings(default_settings: dict[str, Any], runtime_values: dict[str, Any]) -> dict[str, Any]:
    merged_settings = dict(default_settings)
    for key, value in runtime_values.items():
        if key in merged_settings:
            merged_settings[key] = value
    return merged_settings


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
