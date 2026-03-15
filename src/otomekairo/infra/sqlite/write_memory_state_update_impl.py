"""SQLite の write_memory 状態更新集約。"""

from __future__ import annotations

import sqlite3
from typing import Any

from otomekairo.infra.sqlite.write_memory_state_existing_update_impl import (
    apply_existing_memory_state_update,
    fetch_memory_state_row_for_update,
)
from otomekairo.infra.sqlite.write_memory_state_insert_impl import (
    insert_memory_state_with_revision,
    upsert_long_mood_state_with_revision,
)


# Block: 状態更新適用
def apply_state_updates(
    *,
    connection: sqlite3.Connection,
    state_updates: list[dict[str, Any]],
    created_at: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    memory_state_targets: list[dict[str, Any]] = []
    embedding_targets: list[dict[str, Any]] = []
    state_id_by_ref: dict[str, str] = {}
    for state_update in state_updates:
        operation = str(state_update["operation"])
        if operation == "upsert":
            if str(state_update["memory_kind"]) == "long_mood_state":
                memory_state_target = upsert_long_mood_state_with_revision(
                    connection=connection,
                    state_update=state_update,
                    created_at=created_at,
                )
            else:
                memory_state_target = insert_memory_state_with_revision(
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
            embedding_targets.append(dict(memory_state_target))
        else:
            memory_state_target, embedding_target = apply_existing_memory_state_update(
                connection=connection,
                state_update=state_update,
                created_at=created_at,
            )
            if embedding_target is not None:
                embedding_targets.append(embedding_target)
        memory_state_targets.append(memory_state_target)
        state_id_by_ref[str(state_update["state_ref"])] = str(memory_state_target["entity_id"])
    return memory_state_targets, embedding_targets, state_id_by_ref


__all__ = [
    "apply_state_updates",
    "fetch_memory_state_row_for_update",
]
