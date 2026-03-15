"""SQLite の write_memory イベント感情影響更新処理。"""

from __future__ import annotations

import sqlite3
from typing import Any

from otomekairo.infra.sqlite_store_legacy_runtime import _json_text, _opaque_id


# Block: イベント感情影響反映
def apply_event_affect_updates(
    *,
    connection: sqlite3.Connection,
    event_affect_updates: list[dict[str, Any]],
    created_at: int,
) -> list[dict[str, Any]]:
    embedding_targets: list[dict[str, Any]] = []
    for event_affect_update in event_affect_updates:
        embedding_targets.append(
            upsert_event_affect_with_revision(
                connection=connection,
                event_affect_update=event_affect_update,
                created_at=created_at,
            )
        )
    return embedding_targets


# Block: イベント感情影響 upsert
def upsert_event_affect_with_revision(
    *,
    connection: sqlite3.Connection,
    event_affect_update: dict[str, Any],
    created_at: int,
) -> dict[str, Any]:
    existing_row = connection.execute(
        """
        SELECT event_affect_id
        FROM event_affects
        WHERE event_id = ?
        """,
        (str(event_affect_update["event_id"]),),
    ).fetchone()
    if existing_row is None:
        event_affect_id = _opaque_id("eaf")
        connection.execute(
            """
            INSERT INTO event_affects (
                event_affect_id,
                event_id,
                moment_affect_text,
                moment_affect_labels_json,
                vad_json,
                confidence,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_affect_id,
                str(event_affect_update["event_id"]),
                str(event_affect_update["moment_affect_text"]),
                _json_text(list(event_affect_update["moment_affect_labels"])),
                _json_text(dict(event_affect_update["vad"])),
                float(event_affect_update["confidence"]),
                created_at,
            ),
        )
        return {
            "entity_type": "event_affect",
            "entity_id": event_affect_id,
            "source_updated_at": created_at,
            "current_searchable": True,
        }
    event_affect_id = str(existing_row["event_affect_id"])
    connection.execute(
        """
        UPDATE event_affects
        SET moment_affect_text = ?,
            moment_affect_labels_json = ?,
            vad_json = ?,
            confidence = ?
        WHERE event_affect_id = ?
        """,
        (
            str(event_affect_update["moment_affect_text"]),
            _json_text(list(event_affect_update["moment_affect_labels"])),
            _json_text(dict(event_affect_update["vad"])),
            float(event_affect_update["confidence"]),
            event_affect_id,
        ),
    )
    return {
        "entity_type": "event_affect",
        "entity_id": event_affect_id,
        "source_updated_at": created_at,
        "current_searchable": True,
    }
