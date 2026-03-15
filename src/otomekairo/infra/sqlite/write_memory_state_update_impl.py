"""SQLite の write_memory 状態更新処理。"""

from __future__ import annotations

import sqlite3
from typing import Any

from otomekairo.infra.sqlite_store_legacy_runtime import (
    _json_text,
    _merged_unique_strings,
    _opaque_id,
    _string_list_or_empty,
)
from otomekairo.infra.sqlite_store_snapshots import (
    _decoded_string_array_json,
    _memory_state_revision_json,
    _memory_state_revision_json_from_row,
    _memory_state_target,
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


# Block: 既存状態更新適用
def apply_existing_memory_state_update(
    *,
    connection: sqlite3.Connection,
    state_update: dict[str, Any],
    created_at: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    target_state_row = fetch_memory_state_row_for_update(
        connection=connection,
        memory_state_id=str(state_update["target_state_id"]),
    )
    target_memory_kind = str(target_state_row["memory_kind"])
    if target_memory_kind != str(state_update["memory_kind"]):
        raise RuntimeError("write_memory state_updates.memory_kind must match target_state_id memory_kind")
    operation = str(state_update["operation"])
    before_json = _memory_state_revision_json_from_row(target_state_row)
    if operation == "close":
        after_json = closed_memory_state_revision_json(
            before_json=before_json,
            valid_to_ts=int(state_update["valid_to_ts"]),
            evidence_event_ids=list(state_update["evidence_event_ids"]),
            updated_at=created_at,
        )
    elif operation == "mark_done":
        after_json = done_memory_state_revision_json(
            before_json=before_json,
            done_at=int(state_update["done_at"]),
            done_reason=str(state_update["done_reason"]),
            evidence_event_ids=list(state_update["evidence_event_ids"]),
            updated_at=created_at,
        )
    elif operation == "revise_confidence":
        after_json = revised_memory_state_revision_json(
            before_json=before_json,
            confidence=float(state_update["confidence"]),
            importance=float(state_update["importance"]),
            memory_strength=float(state_update["memory_strength"]),
            last_confirmed_at=int(state_update["last_confirmed_at"]),
            evidence_event_ids=list(state_update["evidence_event_ids"]),
            updated_at=created_at,
        )
    else:
        raise RuntimeError("write_memory state_updates.operation is invalid")
    if after_json == before_json:
        return (
            _memory_state_target(
                entity_id=str(target_state_row["memory_state_id"]),
                source_updated_at=int(target_state_row["updated_at"]),
                current_searchable=bool(target_state_row["searchable"]),
            ),
            None,
        )
    connection.execute(
        """
        UPDATE memory_states
        SET payload_json = ?,
            confidence = ?,
            importance = ?,
            memory_strength = ?,
            searchable = ?,
            last_confirmed_at = ?,
            evidence_event_ids_json = ?,
            updated_at = ?,
            valid_to_ts = ?
        WHERE memory_state_id = ?
        """,
        (
            _json_text(after_json["payload"]),
            float(after_json["confidence"]),
            float(after_json["importance"]),
            float(after_json["memory_strength"]),
            1 if bool(after_json["searchable"]) else 0,
            int(after_json["last_confirmed_at"]),
            _json_text(list(after_json["evidence_event_ids"])),
            int(after_json["updated_at"]),
            after_json.get("valid_to_ts"),
            str(target_state_row["memory_state_id"]),
        ),
    )
    memory_state_target = _memory_state_target(
        entity_id=str(target_state_row["memory_state_id"]),
        source_updated_at=created_at,
        current_searchable=bool(after_json["searchable"]),
    )
    embedding_target = None
    if bool(before_json["searchable"]) != bool(after_json["searchable"]):
        embedding_target = dict(memory_state_target)
    return memory_state_target, embedding_target


# Block: 更新対象状態取得
def fetch_memory_state_row_for_update(
    *,
    connection: sqlite3.Connection,
    memory_state_id: str,
) -> sqlite3.Row:
    row = connection.execute(
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
        WHERE memory_state_id = ?
        """,
        (memory_state_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("write_memory state_updates.target_state_id is missing")
    return row


# Block: close 後状態生成
def closed_memory_state_revision_json(
    *,
    before_json: dict[str, Any],
    valid_to_ts: int,
    evidence_event_ids: list[str],
    updated_at: int,
) -> dict[str, Any]:
    return {
        **before_json,
        "searchable": False,
        "last_confirmed_at": updated_at,
        "evidence_event_ids": _merged_unique_strings(
            list(before_json["evidence_event_ids"]),
            evidence_event_ids,
        ),
        "updated_at": updated_at,
        "valid_to_ts": valid_to_ts,
    }


# Block: done 後状態生成
def done_memory_state_revision_json(
    *,
    before_json: dict[str, Any],
    done_at: int,
    done_reason: str,
    evidence_event_ids: list[str],
    updated_at: int,
) -> dict[str, Any]:
    after_payload = dict(before_json["payload"])
    after_payload["status"] = "done"
    after_payload["done_at"] = done_at
    after_payload["done_reason"] = done_reason
    after_payload["done_evidence_event_ids"] = _merged_unique_strings(
        _string_list_or_empty(after_payload.get("done_evidence_event_ids")),
        evidence_event_ids,
    )
    return {
        **before_json,
        "payload": after_payload,
        "searchable": False,
        "last_confirmed_at": updated_at,
        "evidence_event_ids": _merged_unique_strings(
            list(before_json["evidence_event_ids"]),
            evidence_event_ids,
        ),
        "updated_at": updated_at,
        "valid_to_ts": done_at,
    }


# Block: revise_confidence 後状態生成
def revised_memory_state_revision_json(
    *,
    before_json: dict[str, Any],
    confidence: float,
    importance: float,
    memory_strength: float,
    last_confirmed_at: int,
    evidence_event_ids: list[str],
    updated_at: int,
) -> dict[str, Any]:
    return {
        **before_json,
        "confidence": confidence,
        "importance": importance,
        "memory_strength": memory_strength,
        "last_confirmed_at": last_confirmed_at,
        "evidence_event_ids": _merged_unique_strings(
            list(before_json["evidence_event_ids"]),
            evidence_event_ids,
        ),
        "updated_at": updated_at,
    }
