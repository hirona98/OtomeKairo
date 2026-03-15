"""SQLite の memory job enqueue / idempotency 処理。"""

from __future__ import annotations

import sqlite3
from typing import Any

from otomekairo.infra.sqlite_store_job_helpers import (
    _embedding_sync_job_idempotency_key,
    _write_memory_job_idempotency_key,
)
from otomekairo.infra.sqlite_store_legacy_runtime import _json_text, _opaque_id
from otomekairo.infra.sqlite_store_memory_helpers import _event_snapshot_refs_for_write_memory_job


# Block: write_memory job enqueue
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


# Block: embedding_sync job enqueue
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


# Block: memory job 追記
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


# Block: idempotency key 既存 job 検索
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
