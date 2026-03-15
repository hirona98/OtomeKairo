"""SQLite の write_memory 関係更新処理。"""

from __future__ import annotations

import sqlite3
from typing import Any

from otomekairo.infra.sqlite_store_legacy_runtime import (
    _json_text,
    _merged_unique_strings,
    _opaque_id,
)
from otomekairo.infra.sqlite_store_snapshots import _decoded_string_array_json


# Block: コンテキスト関係反映
def apply_context_updates(
    *,
    connection: sqlite3.Connection,
    context_updates: dict[str, Any],
    state_id_by_ref: dict[str, str],
    created_at: int,
) -> None:
    for event_link_update in list(context_updates["event_links"]):
        upsert_event_link_with_revision(
            connection=connection,
            event_link_update=event_link_update,
            created_at=created_at,
        )
    for event_thread_update in list(context_updates["event_threads"]):
        upsert_event_thread_with_revision(
            connection=connection,
            event_thread_update=event_thread_update,
            created_at=created_at,
        )
    for state_link_update in list(context_updates["state_links"]):
        from_state_id = state_id_by_ref.get(str(state_link_update["from_state_ref"]))
        to_state_id = state_id_by_ref.get(str(state_link_update["to_state_ref"]))
        if from_state_id is None or to_state_id is None:
            raise RuntimeError("write_memory state_links must resolve to inserted state refs")
        upsert_state_link_with_revision(
            connection=connection,
            from_state_id=from_state_id,
            to_state_id=to_state_id,
            state_link_update=state_link_update,
            created_at=created_at,
        )


# Block: イベントリンク upsert
def upsert_event_link_with_revision(
    *,
    connection: sqlite3.Connection,
    event_link_update: dict[str, Any],
    created_at: int,
) -> None:
    existing_row = connection.execute(
        """
        SELECT event_link_id,
               evidence_event_ids_json
        FROM event_links
        WHERE from_event_id = ?
          AND to_event_id = ?
          AND label = ?
        """,
        (
            str(event_link_update["from_event_id"]),
            str(event_link_update["to_event_id"]),
            str(event_link_update["label"]),
        ),
    ).fetchone()
    merged_evidence_event_ids = _merged_unique_strings(
        _decoded_string_array_json(
            existing_row["evidence_event_ids_json"] if existing_row is not None else None
        ),
        list(event_link_update["evidence_event_ids"]),
    )
    if existing_row is None:
        connection.execute(
            """
            INSERT INTO event_links (
                event_link_id,
                from_event_id,
                to_event_id,
                label,
                confidence,
                evidence_event_ids_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _opaque_id("eln"),
                str(event_link_update["from_event_id"]),
                str(event_link_update["to_event_id"]),
                str(event_link_update["label"]),
                float(event_link_update["confidence"]),
                _json_text(merged_evidence_event_ids),
                created_at,
                created_at,
            ),
        )
        return
    connection.execute(
        """
        UPDATE event_links
        SET confidence = ?,
            evidence_event_ids_json = ?,
            updated_at = ?
        WHERE event_link_id = ?
        """,
        (
            float(event_link_update["confidence"]),
            _json_text(merged_evidence_event_ids),
            created_at,
            str(existing_row["event_link_id"]),
        ),
    )


# Block: イベントスレッド upsert
def upsert_event_thread_with_revision(
    *,
    connection: sqlite3.Connection,
    event_thread_update: dict[str, Any],
    created_at: int,
) -> None:
    existing_row = connection.execute(
        """
        SELECT event_thread_id
        FROM event_threads
        WHERE event_id = ?
          AND thread_key = ?
        """,
        (
            str(event_thread_update["event_id"]),
            str(event_thread_update["thread_key"]),
        ),
    ).fetchone()
    if existing_row is None:
        connection.execute(
            """
            INSERT INTO event_threads (
                event_thread_id,
                event_id,
                thread_key,
                confidence,
                created_at,
                updated_at,
                thread_role
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _opaque_id("eth"),
                str(event_thread_update["event_id"]),
                str(event_thread_update["thread_key"]),
                float(event_thread_update["confidence"]),
                created_at,
                created_at,
                event_thread_update.get("thread_role"),
            ),
        )
        return
    connection.execute(
        """
        UPDATE event_threads
        SET confidence = ?,
            updated_at = ?,
            thread_role = ?
        WHERE event_thread_id = ?
        """,
        (
            float(event_thread_update["confidence"]),
            created_at,
            event_thread_update.get("thread_role"),
            str(existing_row["event_thread_id"]),
        ),
    )


# Block: 状態リンク upsert
def upsert_state_link_with_revision(
    *,
    connection: sqlite3.Connection,
    from_state_id: str,
    to_state_id: str,
    state_link_update: dict[str, Any],
    created_at: int,
) -> None:
    existing_row = connection.execute(
        """
        SELECT state_link_id,
               evidence_event_ids_json
        FROM state_links
        WHERE from_state_id = ?
          AND to_state_id = ?
          AND label = ?
        """,
        (
            from_state_id,
            to_state_id,
            str(state_link_update["label"]),
        ),
    ).fetchone()
    merged_evidence_event_ids = _merged_unique_strings(
        _decoded_string_array_json(
            existing_row["evidence_event_ids_json"] if existing_row is not None else None
        ),
        list(state_link_update["evidence_event_ids"]),
    )
    if existing_row is None:
        connection.execute(
            """
            INSERT INTO state_links (
                state_link_id,
                from_state_id,
                to_state_id,
                label,
                confidence,
                evidence_event_ids_json,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _opaque_id("sln"),
                from_state_id,
                to_state_id,
                str(state_link_update["label"]),
                float(state_link_update["confidence"]),
                _json_text(merged_evidence_event_ids),
                created_at,
                created_at,
            ),
        )
        return
    connection.execute(
        """
        UPDATE state_links
        SET confidence = ?,
            evidence_event_ids_json = ?,
            updated_at = ?
        WHERE state_link_id = ?
        """,
        (
            float(state_link_update["confidence"]),
            _json_text(merged_evidence_event_ids),
            created_at,
            str(existing_row["state_link_id"]),
        ),
    )
