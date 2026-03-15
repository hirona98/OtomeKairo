"""SQLite の UI event 書き込み処理。"""

from __future__ import annotations

import sqlite3
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite_store_legacy_runtime import _json_text, _now_ms


# Block: UI event 追記
def append_ui_outbound_event(
    backend: SqliteBackend,
    *,
    channel: str,
    event_type: str,
    payload: dict[str, Any],
    source_cycle_id: str,
) -> int:
    created_at = _now_ms()
    with backend._connect() as connection:
        return insert_ui_outbound_event_in_transaction(
            connection=connection,
            channel=channel,
            event_type=event_type,
            payload=payload,
            source_cycle_id=source_cycle_id,
            created_at=created_at,
        )


# Block: transaction 内 UI event 追記
def insert_ui_outbound_event_in_transaction(
    *,
    connection: sqlite3.Connection,
    channel: str,
    event_type: str,
    payload: dict[str, Any],
    source_cycle_id: str | None,
    created_at: int,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO ui_outbound_events (
            channel,
            event_type,
            payload_json,
            created_at,
            source_cycle_id
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            channel,
            event_type,
            _json_text(payload),
            created_at,
            source_cycle_id,
        ),
    )
    return int(cursor.lastrowid)
