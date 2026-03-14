"""Memory-job and embedding helper functions for the SQLite state store."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from otomekairo.infra.sqlite_store_errors import StoreValidationError


# Block: Write-memory idempotency
def _write_memory_job_idempotency_key(*, cycle_id: str, event_ids: list[str]) -> str:
    return "write_memory:" + cycle_id + ":" + ":".join(event_ids)


# Block: Memory job payload ref decode
def _resolve_memory_job_payload_ref(payload_ref_json: Any) -> dict[str, Any]:
    if not isinstance(payload_ref_json, str) or not payload_ref_json:
        raise RuntimeError("memory_jobs.payload_ref_json must be non-empty string")
    try:
        payload_ref = json.loads(payload_ref_json)
    except json.JSONDecodeError as error:
        raise RuntimeError("memory_jobs.payload_ref_json must be valid JSON") from error
    if not isinstance(payload_ref, dict):
        raise RuntimeError("memory_jobs.payload_ref_json must be object")
    if payload_ref.get("payload_kind") != "memory_job_payload":
        raise RuntimeError("memory_jobs.payload_ref_json.payload_kind must be memory_job_payload")
    payload_id = payload_ref.get("payload_id")
    if not isinstance(payload_id, str) or not payload_id:
        raise RuntimeError("memory_jobs.payload_ref_json.payload_id must be non-empty string")
    payload_version = payload_ref.get("payload_version")
    if isinstance(payload_version, bool) or not isinstance(payload_version, int):
        raise RuntimeError("memory_jobs.payload_ref_json.payload_version must be integer")
    if payload_version < 1:
        raise RuntimeError("memory_jobs.payload_ref_json.payload_version must be >= 1")
    return {
        "payload_id": payload_id,
        "payload_version": payload_version,
    }


# Block: Embedding idempotency
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


# Block: Memory job error text
def _memory_job_error_text(error: Exception) -> str:
    error_message = str(error).strip()
    if not error_message:
        return type(error).__name__
    return f"{type(error).__name__}: {error_message}"[:500]


# Block: Embedding scope normalization
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


# Block: Embedding source resolve
def _resolve_embedding_source_text(
    *,
    connection: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
) -> str:
    if entity_type == "event":
        row = connection.execute(
            """
            SELECT observation_summary, action_summary, result_summary
            FROM events
            WHERE event_id = ?
            """,
            (entity_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("event is missing for embedding_sync")
        for field_name in ("observation_summary", "action_summary", "result_summary"):
            value = row[field_name]
            if isinstance(value, str) and value:
                return value
        raise RuntimeError("event summary is missing for embedding_sync")
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
