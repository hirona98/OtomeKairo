"""SQLite の write_memory 新規状態作成処理。"""

from __future__ import annotations

import sqlite3
from typing import Any

from otomekairo.infra.sqlite_store_legacy_runtime import (
    _json_text,
    _merged_unique_strings,
    _opaque_id,
)
from otomekairo.infra.sqlite_store_snapshots import (
    _decoded_string_array_json,
    _memory_state_revision_json,
    _memory_state_revision_json_from_row,
    _memory_state_target,
)


# Block: 記憶状態挿入
def insert_memory_state_with_revision(
    *,
    connection: sqlite3.Connection,
    memory_kind: str,
    body_text: str,
    payload_json: dict[str, Any],
    confidence: float,
    importance: float,
    memory_strength: float,
    last_confirmed_at: int,
    evidence_event_ids: list[str],
    created_at: int,
    revision_reason: str,
) -> dict[str, Any]:
    del revision_reason
    memory_state_id = _opaque_id("mem")
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
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
        """,
        (
            memory_state_id,
            memory_kind,
            body_text,
            _json_text(payload_json),
            confidence,
            importance,
            memory_strength,
            last_confirmed_at,
            _json_text(evidence_event_ids),
            created_at,
            created_at,
        ),
    )
    return _memory_state_target(
        entity_id=memory_state_id,
        source_updated_at=created_at,
        current_searchable=True,
    )


# Block: 長期気分状態 upsert
def upsert_long_mood_state_with_revision(
    *,
    connection: sqlite3.Connection,
    state_update: dict[str, Any],
    created_at: int,
) -> dict[str, Any]:
    existing_row = connection.execute(
        """
        SELECT
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
            updated_at,
            valid_from_ts,
            valid_to_ts,
            last_accessed_at
        FROM memory_states
        WHERE memory_kind = 'long_mood_state'
        ORDER BY searchable DESC, updated_at DESC, created_at DESC, memory_state_id DESC
        LIMIT 1
        """
    ).fetchone()
    if existing_row is None:
        return insert_memory_state_with_revision(
            connection=connection,
            memory_kind=str(state_update["memory_kind"]),
            body_text=str(state_update["body_text"]),
            payload_json=dict(state_update["payload"]),
            confidence=float(state_update["confidence"]),
            importance=float(state_update["importance"]),
            memory_strength=float(state_update["memory_strength"]),
            last_confirmed_at=int(state_update["last_confirmed_at"]),
            evidence_event_ids=list(state_update["evidence_event_ids"]),
            created_at=created_at,
            revision_reason=str(state_update["revision_reason"]),
        )
    before_json = _memory_state_revision_json_from_row(existing_row)
    after_json = _memory_state_revision_json(
        memory_kind="long_mood_state",
        body_text=str(state_update["body_text"]),
        payload_json=dict(state_update["payload"]),
        confidence=float(state_update["confidence"]),
        importance=float(state_update["importance"]),
        memory_strength=float(state_update["memory_strength"]),
        searchable=True,
        last_confirmed_at=int(state_update["last_confirmed_at"]),
        evidence_event_ids=_merged_unique_strings(
            _decoded_string_array_json(existing_row["evidence_event_ids_json"]),
            list(state_update["evidence_event_ids"]),
        ),
        created_at=int(existing_row["created_at"]),
        updated_at=created_at,
        valid_from_ts=existing_row["valid_from_ts"],
        valid_to_ts=None,
        last_accessed_at=existing_row["last_accessed_at"],
    )
    if after_json != before_json:
        connection.execute(
            """
            UPDATE memory_states
            SET body_text = ?,
                payload_json = ?,
                confidence = ?,
                importance = ?,
                memory_strength = ?,
                searchable = 1,
                last_confirmed_at = ?,
                evidence_event_ids_json = ?,
                updated_at = ?,
                valid_to_ts = NULL
            WHERE memory_state_id = ?
            """,
            (
                str(state_update["body_text"]),
                _json_text(dict(state_update["payload"])),
                float(state_update["confidence"]),
                float(state_update["importance"]),
                float(state_update["memory_strength"]),
                int(state_update["last_confirmed_at"]),
                _json_text(list(after_json["evidence_event_ids"])),
                created_at,
                str(existing_row["memory_state_id"]),
            ),
        )
    return _memory_state_target(
        entity_id=str(existing_row["memory_state_id"]),
        source_updated_at=created_at if after_json != before_json else int(existing_row["updated_at"]),
        current_searchable=True,
    )
