"""SQLite の commit log 同期処理。"""

from __future__ import annotations

import json
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite_store_legacy_runtime import _json_text
from otomekairo.infra.sqlite_store_memory_helpers import _fetch_events_for_ids
from otomekairo.infra.sqlite_store_runtime_view import (
    _commit_log_sync_error_text,
    _event_log_entry,
)
from otomekairo.schema.store_errors import StoreValidationError


# Block: pending commit log 同期
def sync_pending_commit_logs(
    backend: SqliteBackend,
    *,
    max_commits: int = 8,
) -> int:
    if isinstance(max_commits, bool) or not isinstance(max_commits, int):
        raise StoreValidationError("max_commits must be integer")
    if max_commits <= 0:
        raise StoreValidationError("max_commits must be positive")
    with backend._connect() as connection:
        rows = connection.execute(
            """
            SELECT commit_id
            FROM commit_records
            WHERE log_sync_status IN ('pending', 'needs_replay')
            ORDER BY committed_at ASC, commit_id ASC
            LIMIT ?
            """,
            (max_commits,),
        ).fetchall()
    synced_count = 0
    for row in rows:
        if sync_commit_log(backend, commit_id=int(row["commit_id"])):
            synced_count += 1
    return synced_count


# Block: commit log 同期
def sync_commit_log(
    backend: SqliteBackend,
    *,
    commit_id: int,
) -> bool:
    if isinstance(commit_id, bool) or not isinstance(commit_id, int):
        raise StoreValidationError("commit_id must be integer")
    if commit_id <= 0:
        raise StoreValidationError("commit_id must be positive")
    try:
        if _events_log_contains_commit_id(backend, commit_id=commit_id):
            _update_commit_log_sync_status(
                backend,
                commit_id=commit_id,
                status="synced",
                last_log_sync_error=None,
            )
            return True
        commit_log_entry = _build_commit_log_entry(backend, commit_id=commit_id)
        _append_commit_log_entry(backend, commit_log_entry=commit_log_entry)
        _update_commit_log_sync_status(
            backend,
            commit_id=commit_id,
            status="synced",
            last_log_sync_error=None,
        )
        return True
    except Exception as error:
        _update_commit_log_sync_status(
            backend,
            commit_id=commit_id,
            status="needs_replay",
            last_log_sync_error=_commit_log_sync_error_text(error),
        )
        return False


# Block: commit log entry 構築
def _build_commit_log_entry(
    backend: SqliteBackend,
    *,
    commit_id: int,
) -> dict[str, Any]:
    with backend._connect() as connection:
        commit_row = connection.execute(
            """
            SELECT commit_id, cycle_id, committed_at, commit_payload_json
            FROM commit_records
            WHERE commit_id = ?
            """,
            (commit_id,),
        ).fetchone()
        if commit_row is None:
            raise RuntimeError("commit_record is missing")
        commit_payload = json.loads(commit_row["commit_payload_json"])
        event_ids = commit_payload.get("event_ids", [])
        if not isinstance(event_ids, list):
            raise RuntimeError("commit_payload_json.event_ids must be a list")
        event_rows = (
            _fetch_events_for_ids(connection=connection, event_ids=[str(event_id) for event_id in event_ids])
            if event_ids
            else []
        )
    return {
        "commit_id": int(commit_row["commit_id"]),
        "cycle_id": str(commit_row["cycle_id"]),
        "committed_at": int(commit_row["committed_at"]),
        "commit_payload": commit_payload,
        "events": [_event_log_entry(row) for row in event_rows],
    }


# Block: commit log 追記
def _append_commit_log_entry(
    backend: SqliteBackend,
    *,
    commit_log_entry: dict[str, Any],
) -> None:
    events_log_path = _events_log_path(backend)
    events_log_path.parent.mkdir(parents=True, exist_ok=True)
    with events_log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(_json_text(commit_log_entry))
        log_file.write("\n")


# Block: events.jsonl 既存確認
def _events_log_contains_commit_id(
    backend: SqliteBackend,
    *,
    commit_id: int,
) -> bool:
    events_log_path = _events_log_path(backend)
    if not events_log_path.exists():
        return False
    with events_log_path.open("r", encoding="utf-8") as log_file:
        for line in log_file:
            stripped_line = line.strip()
            if not stripped_line:
                continue
            log_entry = json.loads(stripped_line)
            if not isinstance(log_entry, dict):
                raise RuntimeError("events.jsonl entry must be an object")
            logged_commit_id = log_entry.get("commit_id")
            if isinstance(logged_commit_id, int) and logged_commit_id == commit_id:
                return True
    return False


# Block: commit log 状態更新
def _update_commit_log_sync_status(
    backend: SqliteBackend,
    *,
    commit_id: int,
    status: str,
    last_log_sync_error: str | None,
) -> None:
    if status not in {"synced", "needs_replay"}:
        raise StoreValidationError("status is invalid")
    with backend._connect() as connection:
        updated_row_count = connection.execute(
            """
            UPDATE commit_records
            SET log_sync_status = ?,
                last_log_sync_error = ?
            WHERE commit_id = ?
            """,
            (status, last_log_sync_error, commit_id),
        ).rowcount
    if updated_row_count != 1:
        raise RuntimeError("commit_record must exist before log sync update")


# Block: events.jsonl path
def _events_log_path(backend: SqliteBackend):
    return backend._db_path.parent / "events.jsonl"
