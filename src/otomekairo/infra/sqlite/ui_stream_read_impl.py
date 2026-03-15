"""SQLite の UI stream 読み取りと retention 処理。"""

from __future__ import annotations

import json

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite_store_legacy_runtime import _now_ms
from otomekairo.schema.store_errors import StoreValidationError


# Block: stream window 読み取り
def read_stream_window(
    backend: SqliteBackend,
    *,
    channel: str,
) -> tuple[int | None, int | None]:
    with backend._connect() as connection:
        row = connection.execute(
            """
            SELECT MIN(ui_event_id) AS min_id, MAX(ui_event_id) AS max_id
            FROM ui_outbound_events
            WHERE channel = ?
            """,
            (channel,),
        ).fetchone()
    if row is None:
        return (None, None)
    return (row["min_id"], row["max_id"])


# Block: UI event 読み取り
def read_ui_events(
    backend: SqliteBackend,
    *,
    channel: str,
    after_event_id: int,
    limit: int = 100,
) -> list[dict[str, object]]:
    with backend._connect() as connection:
        rows = connection.execute(
            """
            SELECT ui_event_id, event_type, payload_json
            FROM ui_outbound_events
            WHERE channel = ?
              AND ui_event_id > ?
            ORDER BY ui_event_id ASC
            LIMIT ?
            """,
            (channel, after_event_id, limit),
        ).fetchall()
    return [
        {
            "ui_event_id": row["ui_event_id"],
            "event_type": row["event_type"],
            "payload": json.loads(row["payload_json"]),
        }
        for row in rows
    ]


# Block: UI retention 削除
def prune_ui_outbound_events(
    backend: SqliteBackend,
    *,
    channel: str,
    retention_window_ms: int,
    retain_minimum_count: int,
) -> int:
    if not isinstance(channel, str) or not channel:
        raise StoreValidationError("channel must be non-empty string")
    if retention_window_ms <= 0:
        raise StoreValidationError("retention_window_ms must be positive")
    if retain_minimum_count <= 0:
        raise StoreValidationError("retain_minimum_count must be positive")
    now_ms = _now_ms()
    created_cutoff_at = now_ms - retention_window_ms
    with backend._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        latest_row = connection.execute(
            """
            SELECT MAX(ui_event_id) AS latest_ui_event_id
            FROM ui_outbound_events
            WHERE channel = ?
            """,
            (channel,),
        ).fetchone()
        if latest_row is None or latest_row["latest_ui_event_id"] is None:
            return 0
        latest_ui_event_id = int(latest_row["latest_ui_event_id"])
        id_cutoff = latest_ui_event_id - retain_minimum_count
        if id_cutoff <= 0:
            return 0
        deleted_row_count = connection.execute(
            """
            DELETE FROM ui_outbound_events
            WHERE channel = ?
              AND created_at < ?
              AND ui_event_id < ?
            """,
            (
                channel,
                created_cutoff_at,
                id_cutoff,
            ),
        ).rowcount
    return int(deleted_row_count)
