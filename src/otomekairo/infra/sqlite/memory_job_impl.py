"""SQLite memory job implementations."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite_store_job_helpers import (
    _embedding_sync_job_idempotency_key,
    _memory_job_error_text,
    _resolve_memory_job_payload_ref,
    _write_memory_job_idempotency_key,
)
from otomekairo.infra.sqlite_store_legacy_runtime import _json_text, _now_ms, _opaque_id
from otomekairo.infra.sqlite_store_memory_helpers import _event_snapshot_refs_for_write_memory_job
from otomekairo.schema.runtime_types import MemoryJobRecord
from otomekairo.schema.store_errors import StoreConflictError, StoreValidationError


# Block: Memory job claim
def claim_next_memory_job(backend: SqliteBackend) -> MemoryJobRecord | None:
    now_ms = _now_ms()
    with backend._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT
                memory_jobs.job_id,
                memory_jobs.job_kind,
                memory_jobs.tries,
                memory_jobs.created_at,
                memory_jobs.payload_ref_json
            FROM memory_jobs
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
        job_id = str(row["job_id"])
        job_kind = str(row["job_kind"])
        try:
            payload_ref = _resolve_memory_job_payload_ref(row["payload_ref_json"])
            payload_row = connection.execute(
                """
                SELECT job_kind, payload_json
                FROM memory_job_payloads
                WHERE payload_id = ?
                """,
                (payload_ref["payload_id"],),
            ).fetchone()
            if payload_row is None:
                _dead_letter_claimed_memory_job(
                    connection=connection,
                    job_id=job_id,
                    dead_lettered_at=now_ms,
                    last_error=f"missing memory_job_payloads row for payload_id={payload_ref['payload_id']}",
                )
                return None
            payload = json.loads(payload_row["payload_json"])
            if not isinstance(payload, dict):
                raise RuntimeError("memory_job_payloads.payload_json must be object")
            if str(payload_row["job_kind"]) != job_kind:
                raise RuntimeError("memory_job_payloads.job_kind must match memory_jobs.job_kind")
            if str(payload.get("job_kind")) != job_kind:
                raise RuntimeError("memory_job_payloads.payload_json.job_kind must match memory_jobs.job_kind")
            return MemoryJobRecord(
                job_id=job_id,
                job_kind=job_kind,
                tries=int(row["tries"]),
                created_at=int(row["created_at"]),
                payload=payload,
            )
        except Exception as error:
            _dead_letter_claimed_memory_job(
                connection=connection,
                job_id=job_id,
                dead_lettered_at=now_ms,
                last_error=_memory_job_error_text(error),
            )
            return None


# Block: Memory job failure
def fail_claimed_memory_job(
    backend: SqliteBackend,
    *,
    memory_job: MemoryJobRecord,
    error: Exception,
    max_tries: int,
) -> str:
    if max_tries <= 0:
        raise StoreValidationError("max_tries must be positive")
    failed_at = _now_ms()
    error_text = _memory_job_error_text(error)
    with backend._connect() as connection:
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


# Block: Dead letter handling
def _dead_letter_claimed_memory_job(
    *,
    connection: sqlite3.Connection,
    job_id: str,
    dead_lettered_at: int,
    last_error: str,
) -> None:
    updated_row_count = connection.execute(
        """
        UPDATE memory_jobs
        SET status = 'dead_letter',
            updated_at = ?,
            completed_at = ?,
            last_error = ?
        WHERE job_id = ?
          AND status = 'claimed'
        """,
        (
            dead_lettered_at,
            dead_lettered_at,
            last_error,
            job_id,
        ),
    ).rowcount
    if updated_row_count != 1:
        raise StoreConflictError("memory job must be claimed before dead letter handling")


# Block: Claimed memory job ensure
def ensure_claimed_memory_job(
    *,
    connection: sqlite3.Connection,
    job_id: str,
) -> None:
    row = connection.execute(
        """
        SELECT status
        FROM memory_jobs
        WHERE job_id = ?
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("memory job is missing")
    if row["status"] != "claimed":
        raise StoreConflictError("memory job must be claimed")


# Block: Memory job completed mark
def mark_memory_job_completed(
    *,
    connection: sqlite3.Connection,
    job_id: str,
    completed_at: int,
) -> None:
    updated_row_count = connection.execute(
        """
        UPDATE memory_jobs
        SET status = 'completed',
            updated_at = ?,
            completed_at = ?
        WHERE job_id = ?
          AND status = 'claimed'
        """,
        (completed_at, completed_at, job_id),
    ).rowcount
    if updated_row_count != 1:
        raise StoreConflictError("memory job must be claimed before completion")


# Block: Write memory jobs enqueue
def enqueue_write_memory_jobs(
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
    event_snapshot_refs = _event_snapshot_refs_for_write_memory_job(
        connection=connection,
        event_ids=event_ids,
    )
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
        "event_snapshot_refs": event_snapshot_refs,
    }
    return [
        insert_memory_job(
            connection=connection,
            job_kind="write_memory",
            payload_json=payload_json,
            idempotency_key=idempotency_key,
            created_at=created_at,
        )
    ]


# Block: Embedding sync jobs enqueue
def enqueue_embedding_sync_jobs(
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
        insert_memory_job(
            connection=connection,
            job_kind="embedding_sync",
            payload_json=payload_json,
            idempotency_key=idempotency_key,
            created_at=created_at,
        )
    ]


# Block: Memory job insert
def insert_memory_job(
    *,
    connection: sqlite3.Connection,
    job_kind: str,
    payload_json: dict[str, Any],
    idempotency_key: str,
    created_at: int,
) -> str:
    existing_job_id = find_memory_job_id_by_idempotency_key(
        connection=connection,
        idempotency_key=idempotency_key,
    )
    if existing_job_id is not None:
        return existing_job_id
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


# Block: Memory job idempotency lookup
def find_memory_job_id_by_idempotency_key(
    *,
    connection: sqlite3.Connection,
    idempotency_key: str,
) -> str | None:
    payload_row = connection.execute(
        """
        SELECT payload_id
        FROM memory_job_payloads
        WHERE idempotency_key = ?
        """,
        (idempotency_key,),
    ).fetchone()
    if payload_row is None:
        return None
    job_row = connection.execute(
        """
        SELECT job_id
        FROM memory_jobs
        WHERE json_extract(payload_ref_json, '$.payload_id') = ?
        ORDER BY created_at ASC, rowid ASC
        LIMIT 1
        """,
        (payload_row["payload_id"],),
    ).fetchone()
    if job_row is None:
        raise RuntimeError("memory_job_payload exists without memory_job")
    return str(job_row["job_id"])
