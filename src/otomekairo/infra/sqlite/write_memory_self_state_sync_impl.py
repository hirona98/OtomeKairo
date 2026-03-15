"""SQLite の write_memory 自己状態同期処理。"""

from __future__ import annotations

import sqlite3
from typing import Any

from otomekairo.infra.sqlite_store_legacy_runtime import _json_text
from otomekairo.infra.sqlite_store_memory_helpers import _current_emotion_json_from_long_mood_payload
from otomekairo.infra.sqlite_store_snapshots import _decoded_object_json
from otomekairo.infra.sqlite.write_memory_state_update_impl import fetch_memory_state_row_for_update


# Block: 現在感情同期
def sync_current_emotion_from_long_mood_state(
    *,
    connection: sqlite3.Connection,
    state_updates: list[dict[str, Any]],
    state_id_by_ref: dict[str, str],
    created_at: int,
) -> None:
    long_mood_state_update = next(
        (
            state_update
            for state_update in state_updates
            if str(state_update["operation"]) == "upsert"
            and str(state_update["memory_kind"]) == "long_mood_state"
        ),
        None,
    )
    if long_mood_state_update is None:
        return
    target_state_id = state_id_by_ref.get(str(long_mood_state_update["state_ref"]))
    if target_state_id is None:
        raise RuntimeError("long_mood_state state_ref must resolve to memory_state_id")
    state_row = fetch_memory_state_row_for_update(
        connection=connection,
        memory_state_id=target_state_id,
    )
    mood_payload = _decoded_object_json(state_row["payload_json"])
    next_current_emotion = _current_emotion_json_from_long_mood_payload(
        payload=mood_payload,
    )
    update_self_state_current_emotion(
        connection=connection,
        current_emotion_json=next_current_emotion,
        revision_reason="write_memory が long_mood_state から current_emotion を同期した",
        evidence_event_ids=list(long_mood_state_update["evidence_event_ids"]),
        created_at=created_at,
    )


# Block: 現在感情更新
def update_self_state_current_emotion(
    *,
    connection: sqlite3.Connection,
    current_emotion_json: dict[str, Any],
    revision_reason: str,
    evidence_event_ids: list[str],
    created_at: int,
) -> None:
    del revision_reason, evidence_event_ids
    self_state_row = connection.execute(
        """
        SELECT current_emotion_json
        FROM self_state
        WHERE row_id = 1
        """
    ).fetchone()
    if self_state_row is None:
        raise RuntimeError("self_state row is missing")
    before_json = _decoded_object_json(self_state_row["current_emotion_json"])
    if before_json == current_emotion_json:
        return
    connection.execute(
        """
        UPDATE self_state
        SET current_emotion_json = ?,
            updated_at = ?
        WHERE row_id = 1
        """,
        (
            _json_text(current_emotion_json),
            created_at,
        ),
    )
