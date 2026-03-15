"""SQLite memory job implementations."""

from __future__ import annotations

import json
import sqlite3

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite_store_job_helpers import (
    _memory_job_error_text,
    _resolve_memory_job_payload_ref,
)
from otomekairo.infra.sqlite_store_legacy_runtime import _now_ms
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
