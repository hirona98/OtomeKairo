"""SQLite UI event implementations."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite_store_legacy_runtime import _json_text, _now_ms
from otomekairo.infra.sqlite_store_runtime_view import (
    _decode_optional_json_text,
    _decode_required_json_text,
    _history_assistant_message,
    _history_user_message,
)
from otomekairo.schema.store_errors import StoreValidationError


# Block: Stream window read
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


# Block: Chat history read
def read_chat_history(
    backend: SqliteBackend,
    *,
    channel: str,
    limit: int = 200,
) -> dict[str, Any]:
    if not isinstance(channel, str) or not channel:
        raise StoreValidationError("channel must be non-empty string")
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise StoreValidationError("limit must be integer")
    if limit <= 0 or limit > 500:
        raise StoreValidationError("limit must be within 1..500")
    with backend._connect() as connection:
        user_rows = connection.execute(
            """
            SELECT input_id, created_at, payload_json
            FROM pending_inputs
            WHERE channel = ?
              AND json_extract(payload_json, '$.input_kind') IN ('chat_message', 'microphone_message')
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (channel, limit),
        ).fetchall()
        assistant_rows = connection.execute(
            """
            SELECT result_id, finished_at, command_json, observed_effects_json
            FROM action_history
            WHERE json_extract(observed_effects_json, '$.final_message_emitted') = 1
              AND json_type(command_json, '$.parameters.text') = 'text'
            ORDER BY finished_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        stream_window_row = connection.execute(
            """
            SELECT MAX(ui_event_id) AS max_id
            FROM ui_outbound_events
            WHERE channel = ?
            """,
            (channel,),
        ).fetchone()
    messages: list[dict[str, Any]] = []
    for row in user_rows:
        payload = _decode_required_json_text(
            raw_value=row["payload_json"],
            field_name="pending_inputs.payload_json",
        )
        messages.append(
            _history_user_message(
                input_id=str(row["input_id"]),
                created_at=int(row["created_at"]),
                payload=payload,
            )
        )
    for row in assistant_rows:
        command_json = _decode_required_json_text(
            raw_value=row["command_json"],
            field_name="action_history.command_json",
        )
        observed_effects_json = _decode_optional_json_text(
            raw_value=row["observed_effects_json"],
            field_name="action_history.observed_effects_json",
        )
        history_message = _history_assistant_message(
            finished_at=int(row["finished_at"]),
            command_json=command_json,
            observed_effects_json=observed_effects_json,
        )
        if history_message is not None:
            messages.append(history_message)
    messages.sort(key=lambda item: (int(item["created_at"]), str(item["message_id"])))
    if len(messages) > limit:
        messages = messages[-limit:]
    stream_cursor = None
    if stream_window_row is not None and stream_window_row["max_id"] is not None:
        stream_cursor = int(stream_window_row["max_id"])
    return {
        "channel": channel,
        "messages": messages,
        "stream_cursor": stream_cursor,
    }


# Block: UI retention prune
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


# Block: UI events read
def read_ui_events(
    backend: SqliteBackend,
    *,
    channel: str,
    after_event_id: int,
    limit: int = 100,
) -> list[dict[str, Any]]:
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


# Block: UI event append
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


# Block: UI event insert
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
