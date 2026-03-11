"""Deterministic smoke check for runtime-owned tidy_memory scheduling."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from otomekairo import __version__
from otomekairo.infra.sqlite_state_store import SqliteStateStore
from otomekairo.runtime.main_loop import RuntimeLoop
from otomekairo.schema.settings import build_default_settings


# Block: Report constants
REPORT_SCHEMA_VERSION = 1


# Block: Public smoke runner
def run_tidy_memory_owner_smoke(*, keep_db: bool) -> dict[str, Any]:
    temp_dir = Path(tempfile.mkdtemp(prefix="otomekairo-tidy-memory-owner-"))
    db_path = temp_dir / "core.sqlite3"
    try:
        default_settings = build_default_settings()
        store = SqliteStateStore(
            db_path=db_path,
            initializer_version=__version__,
        )
        store.initialize()
        now_ms = _seed_smoke_state(
            db_path=db_path,
            default_settings=default_settings,
        )
        runtime = RuntimeLoop(
            store=store,
            owner_token="smoke-owner",
            default_settings=default_settings,
            cognition_client=object(),
            search_client=object(),
            camera_controller=object(),
            camera_sensor=object(),
            speech_synthesizer=object(),
        )
        enqueued_count = runtime._enqueue_due_tidy_memory_jobs()
        processed_scopes = _drain_tidy_jobs(
            store=store,
            runtime=runtime,
        )
        owner_state_after = store.read_tidy_memory_owner_state(
            completed_jobs_cutoff_at=now_ms - 60_000,
            stale_preview_cutoff_at=now_ms - 60_000,
            stale_vector_cutoff_at=now_ms - 60_000,
        )
        second_enqueued_count = runtime._enqueue_due_tidy_memory_jobs()
        report = _build_report(
            db_path=db_path,
            keep_db=keep_db,
            enqueued_count=enqueued_count,
            processed_scopes=processed_scopes,
            owner_state_after=owner_state_after,
            second_enqueued_count=second_enqueued_count,
        )
        _validate_report(report)
        return report
    finally:
        if not keep_db:
            shutil.rmtree(temp_dir, ignore_errors=True)


# Block: Smoke seed
def _seed_smoke_state(
    *,
    db_path: Path,
    default_settings: dict[str, Any],
) -> int:
    now_ms = 1_710_000_000_000
    old_ms = now_ms - 120_000
    with sqlite3.connect(db_path) as connection:
        _write_smoke_runtime_settings(
            connection=connection,
            default_settings=default_settings,
            updated_at=now_ms,
        )
        connection.execute(
            """
            INSERT INTO events (
                event_id,
                cycle_id,
                created_at,
                source,
                kind,
                searchable,
                updated_at,
                observation_summary,
                action_summary,
                result_summary,
                payload_ref_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evt_stale_preview",
                "cycle_smoke",
                old_ms,
                "system",
                "observation",
                0,
                old_ms,
                "stale preview source",
                None,
                None,
                None,
            ),
        )
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
                "preview_smoke",
                "evt_stale_preview",
                "stale preview",
                old_ms,
                old_ms,
                old_ms,
            ),
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "vec_smoke",
                "event",
                "evt_stale_preview",
                str(default_settings["llm.embedding_model"]),
                "recent",
                0,
                old_ms,
                sqlite3.Binary(bytes([0] * 16)),
            ),
        )
        payload_ref_json = json.dumps(
            {
                "payload_kind": "memory_job_payload",
                "payload_id": "payload_old_write",
                "payload_version": 1,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
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
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "payload_old_write",
                "memory_job_payload",
                1,
                "write_memory",
                json.dumps(
                    {
                        "job_kind": "write_memory",
                        "cycle_id": "cycle_smoke",
                        "source_event_ids": ["evt_stale_preview"],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                old_ms,
                "write_memory:smoke-old",
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job_old_write",
                "write_memory",
                payload_ref_json,
                "completed",
                1,
                old_ms,
                old_ms,
                old_ms,
                old_ms,
                None,
            ),
        )
    return now_ms


# Block: Runtime settings override
def _write_smoke_runtime_settings(
    *,
    connection: sqlite3.Connection,
    default_settings: dict[str, Any],
    updated_at: int,
) -> None:
    runtime_values = dict(default_settings)
    runtime_values.update(
        {
            "memory.tidy_min_interval_ms": 60_000,
            "memory.tidy_completed_jobs_retention_ms": 60_000,
            "memory.tidy_completed_jobs_trigger_count": 1,
            "memory.tidy_preview_retention_ms": 60_000,
            "memory.tidy_preview_trigger_count": 1,
            "memory.tidy_vector_retention_ms": 60_000,
            "memory.tidy_vector_trigger_count": 1,
        }
    )
    value_updated_at = {
        key: updated_at
        for key in runtime_values
    }
    connection.execute(
        """
        UPDATE runtime_settings
        SET values_json = ?,
            value_updated_at_json = ?,
            updated_at = ?
        WHERE row_id = 1
        """,
        (
            json.dumps(runtime_values, ensure_ascii=False, separators=(",", ":")),
            json.dumps(value_updated_at, ensure_ascii=False, separators=(",", ":")),
            updated_at,
        ),
    )


# Block: Tidy job drain
def _drain_tidy_jobs(
    *,
    store: SqliteStateStore,
    runtime: RuntimeLoop,
) -> list[str]:
    processed_scopes: list[str] = []
    while True:
        memory_job = store.claim_next_memory_job()
        if memory_job is None:
            return processed_scopes
        if memory_job.job_kind != "tidy_memory":
            raise RuntimeError("tidy_memory_owner_smoke claimed unexpected job kind")
        maintenance_scope = memory_job.payload.get("maintenance_scope")
        if not isinstance(maintenance_scope, str) or not maintenance_scope:
            raise RuntimeError("tidy_memory_owner_smoke maintenance_scope must be non-empty string")
        processed_scopes.append(maintenance_scope)
        runtime._memory_job_handler(memory_job.job_kind)(memory_job)


# Block: Report build
def _build_report(
    *,
    db_path: Path,
    keep_db: bool,
    enqueued_count: int,
    processed_scopes: list[str],
    owner_state_after: dict[str, dict[str, Any]],
    second_enqueued_count: int,
) -> dict[str, Any]:
    checks = {
        "tidy_jobs_enqueued": enqueued_count == 3,
        "all_scopes_processed": sorted(processed_scopes) == [
            "completed_jobs_gc",
            "stale_preview_gc",
            "stale_vector_gc",
        ],
        "backlog_cleared": all(
            int(scope_state["stale_count"]) == 0
            for scope_state in owner_state_after.values()
        ),
        "no_active_tidy_jobs": all(
            bool(scope_state["has_active_job"]) is False
            for scope_state in owner_state_after.values()
        ),
        "no_immediate_reenqueue": second_enqueued_count == 0,
    }
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "checks": checks,
        "enqueued_count": enqueued_count,
        "processed_scopes": list(processed_scopes),
        "owner_state_after": owner_state_after,
        "second_enqueued_count": second_enqueued_count,
    }
    if keep_db:
        report["db_path"] = str(db_path)
    return report


# Block: Report validation
def _validate_report(report: dict[str, Any]) -> None:
    checks = report.get("checks")
    if not isinstance(checks, dict):
        raise RuntimeError("tidy_memory_owner_smoke.checks must be an object")
    failed_checks = [
        check_name
        for check_name, passed in checks.items()
        if bool(passed) is False
    ]
    if failed_checks:
        raise RuntimeError("tidy_memory_owner_smoke failed: " + ", ".join(failed_checks))

