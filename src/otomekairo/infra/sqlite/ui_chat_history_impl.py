"""SQLite の UI chat history 読み取り処理。"""

from __future__ import annotations

from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite_store_runtime_view import (
    _decode_optional_json_text,
    _decode_required_json_text,
    _history_assistant_message,
    _history_user_message,
)
from otomekairo.schema.store_errors import StoreValidationError


# Block: chat history 読み取り
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
