"""Deterministic smoke check for current schema migration chain."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from otomekairo import __version__
from otomekairo.infra.sqlite_state_store import SqliteStateStore
from otomekairo.schema.settings import build_default_settings


# Block: Report constants
REPORT_SCHEMA_VERSION = 2


# Block: Public smoke runner
def run_schema_migration_smoke(*, keep_db: bool) -> dict[str, Any]:
    temp_dir = Path(tempfile.mkdtemp(prefix="otomekairo-schema-migration-"))
    db_path = temp_dir / "core.sqlite3"
    try:
        default_settings = build_default_settings()
        store = SqliteStateStore(
            db_path=db_path,
            initializer_version=__version__,
        )
        store.initialize()
        _downgrade_to_schema15_fixture(db_path=db_path)
        migrated_store = SqliteStateStore(
            db_path=db_path,
            initializer_version=__version__,
        )
        migrated_store.initialize()
        cognition_state = migrated_store.read_cognition_state(default_settings)
        owner_state = migrated_store.read_tidy_memory_owner_state(
            completed_jobs_cutoff_at=1_710_000_060_000,
            stale_preview_cutoff_at=1_710_000_060_000,
            stale_vector_cutoff_at=1_710_000_060_000,
        )
        report = _build_report(
            db_path=db_path,
            keep_db=keep_db,
            cognition_state=cognition_state,
            owner_state=owner_state,
        )
        _validate_report(report)
        return report
    finally:
        if not keep_db:
            shutil.rmtree(temp_dir, ignore_errors=True)


# Block: Schema15 fixture downgrade
def _downgrade_to_schema15_fixture(*, db_path: Path) -> None:
    now_ms = 1_710_000_000_000
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE working_memory_items (
                slot_no INTEGER PRIMARY KEY CHECK (slot_no >= 0),
                item_kind TEXT NOT NULL,
                summary_text TEXT NOT NULL,
                source_refs_json TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                confidence REAL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE recent_event_window_items (
                window_pos INTEGER PRIMARY KEY CHECK (window_pos >= 0),
                source_kind TEXT NOT NULL CHECK (source_kind IN ('input_journal', 'event')),
                source_id TEXT NOT NULL,
                summary_text TEXT NOT NULL,
                captured_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE skill_registry (
                skill_id TEXT PRIMARY KEY,
                trigger_pattern_json TEXT NOT NULL,
                preconditions_json TEXT NOT NULL,
                action_pattern_json TEXT NOT NULL,
                success_signature_json TEXT NOT NULL,
                enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                summary_text TEXT,
                last_used_at INTEGER
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_skill_registry_enabled_updated
                ON skill_registry (enabled, updated_at DESC)
            """
        )
        connection.execute("DROP INDEX IF EXISTS idx_stable_preference_projection_scope_status_updated")
        connection.execute("DROP TABLE IF EXISTS stable_preference_projection")
        connection.execute("DROP TABLE IF EXISTS runtime_housekeeping_state")
        connection.execute(
            """
            UPDATE db_meta
            SET meta_value_json = ?, updated_at = ?
            WHERE meta_key = 'schema_version'
            """,
            (json.dumps(15, ensure_ascii=True, separators=(",", ":")), now_ms),
        )
        _insert_preference_row(
            connection=connection,
            preference_id="pref_confirmed_fixture",
            status="confirmed",
            polarity="like",
            target_key="展示",
            updated_at=now_ms - 1_000,
        )
        _insert_preference_row(
            connection=connection,
            preference_id="pref_revoked_fixture",
            status="revoked",
            polarity="dislike",
            target_key="ホラー映画",
            updated_at=now_ms - 500,
        )
        connection.execute(
            """
            INSERT INTO working_memory_items (
                slot_no,
                item_kind,
                summary_text,
                source_refs_json,
                updated_at,
                confidence
            )
            VALUES (0, 'summary', 'fixture', '[]', ?, 0.8)
            """,
            (now_ms - 100,),
        )
        connection.execute(
            """
            INSERT INTO recent_event_window_items (
                window_pos,
                source_kind,
                source_id,
                summary_text,
                captured_at,
                updated_at
            )
            VALUES (0, 'event', 'evt_fixture', 'fixture', ?, ?)
            """,
            (now_ms - 100, now_ms - 100),
        )
        connection.execute(
            """
            INSERT INTO skill_registry (
                skill_id,
                trigger_pattern_json,
                preconditions_json,
                action_pattern_json,
                success_signature_json,
                enabled,
                created_at,
                updated_at,
                summary_text,
                last_used_at
            )
            VALUES (
                'skill_fixture',
                '{"trigger":"fixture"}',
                '[]',
                '[]',
                '[]',
                1,
                ?,
                ?,
                'fixture',
                ?
            )
            """,
            (now_ms - 100, now_ms - 100, now_ms - 100),
        )
        payload_id = "payload_tidy_fixture"
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
            VALUES (?, 'memory_job_payload', 1, 'tidy_memory', ?, ?, ?)
            """,
            (
                payload_id,
                json.dumps(
                    {
                        "job_kind": "tidy_memory",
                        "cycle_id": "cycle_fixture",
                        "source_event_ids": [],
                        "maintenance_scope": "completed_jobs_gc",
                        "retention_cutoff_at": now_ms - 10_000,
                        "idempotency_key": "tidy_memory:fixture",
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                ),
                now_ms - 300,
                "tidy_memory:fixture",
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
                updated_at,
                claimed_at,
                completed_at,
                last_error
            )
            VALUES (?, 'tidy_memory', ?, 'completed', 1, ?, ?, ?, ?, NULL)
            """,
            (
                "job_tidy_fixture",
                json.dumps(
                    {
                        "payload_kind": "memory_job_payload",
                        "payload_id": payload_id,
                        "payload_version": 1,
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                ),
                now_ms - 300,
                now_ms - 300,
                now_ms - 300,
                now_ms - 200,
            ),
        )


# Block: Preference insert helper
def _insert_preference_row(
    *,
    connection: sqlite3.Connection,
    preference_id: str,
    status: str,
    polarity: str,
    target_key: str,
    updated_at: int,
) -> None:
    connection.execute(
        """
        INSERT INTO preference_memory (
            preference_id,
            owner_scope,
            target_entity_ref_json,
            target_key,
            domain,
            polarity,
            status,
            confidence,
            evidence_event_ids_json,
            created_at,
            updated_at
        )
        VALUES (?, 'self', ?, ?, 'topic_keyword', ?, ?, ?, ?, ?, ?)
        """,
        (
            preference_id,
            json.dumps(
                {
                    "target_key": target_key,
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
            target_key,
            polarity,
            status,
            0.9,
            json.dumps(["evt_fixture"], ensure_ascii=True, separators=(",", ":")),
            updated_at,
            updated_at,
        ),
    )


# Block: Report build
def _build_report(
    *,
    db_path: Path,
    keep_db: bool,
    cognition_state: Any,
    owner_state: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    stable_keys = sorted(
        f"{item['payload']['status']}:{item['payload']['polarity']}:{item['payload']['target_entity_ref']['target_key']}"
        for item in cognition_state.stable_preference_items
    )
    with sqlite3.connect(db_path) as connection:
        remaining_ghost_tables = sorted(
            row[0]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name IN (
                      'working_memory_items',
                      'recent_event_window_items',
                      'skill_registry'
                  )
                ORDER BY name ASC
                """
            ).fetchall()
        )
    checks = {
        "schema18_projection_backfilled": stable_keys == [
            "confirmed:like:展示",
            "revoked:dislike:ホラー映画",
        ],
        "schema18_housekeeping_backfilled": (
            isinstance(owner_state["completed_jobs_gc"]["last_enqueued_at"], int)
            and int(owner_state["completed_jobs_gc"]["last_enqueued_at"]) > 0
        ),
        "schema18_ghost_tables_dropped": remaining_ghost_tables == [],
    }
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "checks": checks,
        "stable_keys": stable_keys,
        "owner_state": owner_state,
        "remaining_ghost_tables": remaining_ghost_tables,
    }
    if keep_db:
        report["db_path"] = str(db_path)
    return report


# Block: Report validation
def _validate_report(report: dict[str, Any]) -> None:
    failed_checks = [
        check_name
        for check_name, passed in report["checks"].items()
        if bool(passed) is False
    ]
    if failed_checks:
        raise RuntimeError(
            "schema_migration_smoke failed: " + ", ".join(sorted(failed_checks))
        )
