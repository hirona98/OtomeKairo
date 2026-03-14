"""Snapshot helper functions for the SQLite state store."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from otomekairo.usecase.about_time_text import about_years_from_text, life_stage_from_text


# Block: Task snapshot rows
def _build_task_snapshot_rows(
    *,
    active_task_rows: list[sqlite3.Row],
    waiting_task_rows: list[sqlite3.Row],
) -> dict[str, Any]:
    return {
        "active_tasks": [
            _task_snapshot_entry(row)
            for row in active_task_rows
        ],
        "waiting_external_tasks": [
            _task_snapshot_entry(row)
            for row in waiting_task_rows
        ],
    }


def _task_snapshot_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "task_id": str(row["task_id"]),
        "task_kind": str(row["task_kind"]),
        "task_status": str(row["task_status"]),
        "goal_hint": str(row["goal_hint"]),
        "completion_hint": json.loads(row["completion_hint_json"]),
        "resume_condition": json.loads(row["resume_condition_json"]),
        "interruptible": bool(row["interruptible"]),
        "priority": int(row["priority"]),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
        "title": str(row["title"]) if row["title"] is not None else None,
        "step_hints": (
            json.loads(row["step_hints_json"])
            if isinstance(row["step_hints_json"], str) and row["step_hints_json"]
            else []
        ),
    }


# Block: Memory snapshot rows
def _build_memory_snapshot_rows(
    *,
    recent_event_rows: list[sqlite3.Row],
    memory_rows: list[sqlite3.Row],
    affect_rows: list[sqlite3.Row],
    stable_preference_rows: list[sqlite3.Row],
    event_link_rows: list[sqlite3.Row],
    event_thread_rows: list[sqlite3.Row],
    event_about_time_rows: list[sqlite3.Row],
    event_entity_rows: list[sqlite3.Row],
    state_link_rows: list[sqlite3.Row],
    state_about_time_rows: list[sqlite3.Row],
    state_entity_rows: list[sqlite3.Row],
) -> dict[str, Any]:
    working_memory_items: list[dict[str, Any]] = []
    episodic_items: list[dict[str, Any]] = []
    semantic_items: list[dict[str, Any]] = []
    affective_items: list[dict[str, Any]] = []
    relationship_items: list[dict[str, Any]] = []
    preference_items: list[dict[str, Any]] = []
    reflection_items: list[dict[str, Any]] = []
    for row in recent_event_rows:
        episodic_items.append(_event_memory_snapshot_entry(row))
    for row in memory_rows:
        entry = _memory_snapshot_entry(row)
        memory_kind = str(row["memory_kind"])
        if memory_kind == "summary":
            working_memory_items.append(entry)
            continue
        if memory_kind == "fact":
            semantic_items.append(entry)
            continue
        if memory_kind == "long_mood_state":
            affective_items.append(entry)
            continue
        if memory_kind == "relation":
            relationship_items.append(entry)
            continue
        if memory_kind == "reflection_note":
            reflection_items.append(entry)
    for row in affect_rows:
        affective_items.append(_event_affect_snapshot_entry(row))
    for row in stable_preference_rows:
        preference_items.append(_preference_snapshot_entry(row))
    return {
        "working_memory_items": working_memory_items[:3],
        "episodic_items": episodic_items,
        "semantic_items": semantic_items,
        "affective_items": affective_items,
        "relationship_items": relationship_items,
        "preference_items": preference_items,
        "reflection_items": reflection_items,
        "recent_event_window": [
            _recent_event_entry(row)
            for row in recent_event_rows
        ],
        "event_links": [_event_link_snapshot_entry(row) for row in event_link_rows],
        "event_threads": [_event_thread_snapshot_entry(row) for row in event_thread_rows],
        "event_about_time": [_event_about_time_snapshot_entry(row) for row in event_about_time_rows],
        "event_entities": [_event_entity_snapshot_entry(row) for row in event_entity_rows],
        "state_links": [_state_link_snapshot_entry(row) for row in state_link_rows],
        "state_about_time": [_state_about_time_snapshot_entry(row) for row in state_about_time_rows],
        "state_entities": [_state_entity_snapshot_entry(row) for row in state_entity_rows],
    }


def _memory_snapshot_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "memory_state_id": str(row["memory_state_id"]),
        "memory_kind": str(row["memory_kind"]),
        "body_text": str(row["body_text"]),
        "payload": json.loads(row["payload_json"]),
        "confidence": float(row["confidence"]),
        "importance": float(row["importance"]),
        "memory_strength": float(row["memory_strength"]),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
        "last_confirmed_at": int(row["last_confirmed_at"]),
    }


# Block: Stable preference projection read
def _read_stable_preference_projection_rows(
    *,
    connection: sqlite3.Connection,
    bucket_limit: int,
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = []
    for status, polarity in (
        ("confirmed", "like"),
        ("confirmed", "dislike"),
        ("revoked", None),
    ):
        if polarity is None:
            rows.extend(
                connection.execute(
                    """
                    SELECT
                        preference_id,
                        owner_scope,
                        target_entity_ref_json,
                        domain,
                        polarity,
                        status,
                        confidence,
                        evidence_event_ids_json,
                        created_at,
                        updated_at
                    FROM stable_preference_projection
                    WHERE owner_scope = 'self'
                      AND status = 'revoked'
                    ORDER BY confidence DESC, updated_at DESC, created_at DESC, preference_id DESC
                    LIMIT ?
                    """,
                    (bucket_limit,),
                ).fetchall()
            )
            continue
        rows.extend(
            connection.execute(
                """
                SELECT
                    preference_id,
                    owner_scope,
                    target_entity_ref_json,
                    domain,
                    polarity,
                    status,
                    confidence,
                    evidence_event_ids_json,
                    created_at,
                    updated_at
                FROM stable_preference_projection
                WHERE owner_scope = 'self'
                  AND status = ?
                  AND polarity = ?
                ORDER BY confidence DESC, updated_at DESC, created_at DESC, preference_id DESC
                LIMIT ?
                """,
                (status, polarity, bucket_limit),
            ).fetchall()
        )
    return rows


# Block: Event snapshot rows
def _event_memory_snapshot_entry(row: sqlite3.Row) -> dict[str, Any]:
    summary_text = _event_summary_text(row)
    created_at = int(row["created_at"])
    entry = {
        "memory_state_id": str(row["event_id"]),
        "memory_kind": "episodic_event",
        "body_text": summary_text,
        "payload": {
            "event_id": str(row["event_id"]),
            "source": str(row["source"]),
            "kind": str(row["kind"]),
            "summary_text": summary_text,
        },
        "confidence": 1.0,
        "importance": 0.65,
        "memory_strength": 0.45,
        "created_at": created_at,
        "updated_at": created_at,
        "last_confirmed_at": created_at,
    }
    preview_text = row["preview_text"]
    if isinstance(preview_text, str) and preview_text.strip():
        entry["payload"]["preview_text"] = preview_text.strip()
    return entry


def _event_affect_snapshot_entry(row: sqlite3.Row) -> dict[str, Any]:
    created_at = int(row["created_at"])
    confidence = float(row["confidence"])
    return {
        "memory_state_id": str(row["event_affect_id"]),
        "memory_kind": "event_affect",
        "body_text": str(row["moment_affect_text"]),
        "payload": {
            "event_id": str(row["event_id"]),
            "labels": json.loads(row["moment_affect_labels_json"]),
            "vad": json.loads(row["vad_json"]),
            "event_source": str(row["source"]),
            "event_kind": str(row["kind"]),
            "event_summary_text": _event_summary_text(row),
        },
        "confidence": confidence,
        "importance": min(1.0, confidence + 0.10),
        "memory_strength": min(1.0, confidence + 0.05),
        "created_at": created_at,
        "updated_at": created_at,
        "last_confirmed_at": created_at,
    }


def _event_link_snapshot_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_link_id": str(row["event_link_id"]),
        "from_event_id": str(row["from_event_id"]),
        "to_event_id": str(row["to_event_id"]),
        "label": str(row["label"]),
        "confidence": float(row["confidence"]),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }


def _event_thread_snapshot_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_thread_id": str(row["event_thread_id"]),
        "event_id": str(row["event_id"]),
        "thread_key": str(row["thread_key"]),
        "thread_role": str(row["thread_role"]) if row["thread_role"] is not None else None,
        "confidence": float(row["confidence"]),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }


def _event_entity_snapshot_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_entity_id": str(row["event_entity_id"]),
        "event_id": str(row["event_id"]),
        "entity_type_norm": str(row["entity_type_norm"]),
        "entity_name_raw": str(row["entity_name_raw"]),
        "entity_name_norm": str(row["entity_name_norm"]),
        "confidence": float(row["confidence"]),
        "created_at": int(row["created_at"]),
    }


def _event_about_time_snapshot_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_about_time_id": str(row["event_about_time_id"]),
        "event_id": str(row["event_id"]),
        "about_start_ts": int(row["about_start_ts"]) if row["about_start_ts"] is not None else None,
        "about_end_ts": int(row["about_end_ts"]) if row["about_end_ts"] is not None else None,
        "about_year_start": (
            int(row["about_year_start"])
            if row["about_year_start"] is not None
            else None
        ),
        "about_year_end": (
            int(row["about_year_end"])
            if row["about_year_end"] is not None
            else None
        ),
        "life_stage": str(row["life_stage"]) if row["life_stage"] is not None else None,
        "confidence": float(row["confidence"]),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }


def _state_about_time_snapshot_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "state_about_time_id": str(row["state_about_time_id"]),
        "memory_state_id": str(row["memory_state_id"]),
        "about_start_ts": int(row["about_start_ts"]) if row["about_start_ts"] is not None else None,
        "about_end_ts": int(row["about_end_ts"]) if row["about_end_ts"] is not None else None,
        "about_year_start": (
            int(row["about_year_start"])
            if row["about_year_start"] is not None
            else None
        ),
        "about_year_end": (
            int(row["about_year_end"])
            if row["about_year_end"] is not None
            else None
        ),
        "life_stage": str(row["life_stage"]) if row["life_stage"] is not None else None,
        "confidence": float(row["confidence"]),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }


def _state_link_snapshot_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "state_link_id": str(row["state_link_id"]),
        "from_state_id": str(row["from_state_id"]),
        "to_state_id": str(row["to_state_id"]),
        "label": str(row["label"]),
        "confidence": float(row["confidence"]),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }


def _state_entity_snapshot_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "state_entity_id": str(row["state_entity_id"]),
        "memory_state_id": str(row["memory_state_id"]),
        "entity_type_norm": str(row["entity_type_norm"]),
        "entity_name_raw": str(row["entity_name_raw"]),
        "entity_name_norm": str(row["entity_name_norm"]),
        "confidence": float(row["confidence"]),
        "created_at": int(row["created_at"]),
    }


# Block: Preference snapshot rows
def _preference_snapshot_entry(row: sqlite3.Row) -> dict[str, Any]:
    target_entity_ref = json.loads(row["target_entity_ref_json"])
    evidence_event_ids = json.loads(row["evidence_event_ids_json"])
    return {
        "memory_state_id": str(row["preference_id"]),
        "memory_kind": "preference",
        "body_text": " ".join(
            [
                str(row["owner_scope"]),
                str(row["domain"]),
                str(row["polarity"]),
                _preference_target_text(target_entity_ref),
            ]
        ).strip(),
        "payload": {
            "owner_scope": str(row["owner_scope"]),
            "target_entity_ref": target_entity_ref,
            "domain": str(row["domain"]),
            "polarity": str(row["polarity"]),
            "status": str(row["status"]),
            "evidence_event_ids": evidence_event_ids,
        },
        "confidence": float(row["confidence"]),
        "importance": min(1.0, float(row["confidence"]) + 0.10),
        "memory_strength": _memory_strength_from_evidence(evidence_event_ids),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
        "last_confirmed_at": int(row["updated_at"]),
    }


def _preference_target_text(target_entity_ref: Any) -> str:
    if isinstance(target_entity_ref, str):
        return target_entity_ref
    if not isinstance(target_entity_ref, dict):
        return json.dumps(target_entity_ref, sort_keys=True)
    for key in ("target_key", "name", "entity_name", "entity_ref", "text"):
        value = target_entity_ref.get(key)
        if isinstance(value, str) and value:
            return value
    return json.dumps(target_entity_ref, sort_keys=True)


def _memory_strength_from_evidence(evidence_event_ids: Any) -> float:
    if not isinstance(evidence_event_ids, list):
        return 0.25
    return min(1.0, 0.20 + len(evidence_event_ids) * 0.15)


# Block: Memory state revision helpers
def _memory_state_revision_json(
    *,
    memory_kind: str,
    body_text: str,
    payload_json: dict[str, Any],
    confidence: float,
    importance: float,
    memory_strength: float,
    searchable: bool,
    last_confirmed_at: int,
    evidence_event_ids: list[str],
    created_at: int,
    updated_at: int,
    valid_from_ts: int | None,
    valid_to_ts: int | None,
    last_accessed_at: int | None,
) -> dict[str, Any]:
    revision_json = {
        "memory_kind": memory_kind,
        "body_text": body_text,
        "payload": payload_json,
        "confidence": confidence,
        "importance": importance,
        "memory_strength": memory_strength,
        "searchable": searchable,
        "last_confirmed_at": last_confirmed_at,
        "evidence_event_ids": evidence_event_ids,
        "created_at": created_at,
        "updated_at": updated_at,
    }
    if valid_from_ts is not None:
        revision_json["valid_from_ts"] = valid_from_ts
    if valid_to_ts is not None:
        revision_json["valid_to_ts"] = valid_to_ts
    if last_accessed_at is not None:
        revision_json["last_accessed_at"] = last_accessed_at
    return revision_json


def _memory_state_revision_json_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return _memory_state_revision_json(
        memory_kind=str(row["memory_kind"]),
        body_text=str(row["body_text"]),
        payload_json=_decoded_object_json(row["payload_json"]),
        confidence=float(row["confidence"]),
        importance=float(row["importance"]),
        memory_strength=float(row["memory_strength"]),
        searchable=bool(row["searchable"]),
        last_confirmed_at=int(row["last_confirmed_at"]),
        evidence_event_ids=_decoded_string_array_json(row["evidence_event_ids_json"]),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
        valid_from_ts=row["valid_from_ts"],
        valid_to_ts=row["valid_to_ts"],
        last_accessed_at=row["last_accessed_at"],
    )


def _memory_state_target(
    *,
    entity_id: str,
    source_updated_at: int,
    current_searchable: bool,
) -> dict[str, Any]:
    return {
        "entity_type": "memory_state",
        "entity_id": entity_id,
        "source_updated_at": source_updated_at,
        "current_searchable": current_searchable,
    }


# Block: Entity and about-time helpers
def _event_entity_entries_from_annotation(event_annotation: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for entity_entry in event_annotation["entities"]:
        _append_state_entity_entry(
            entries=entries,
            seen_keys=seen_keys,
            entity_type_norm=str(entity_entry["entity_type_norm"]),
            entity_name_raw=str(entity_entry["entity_name_raw"]),
            confidence=float(entity_entry["confidence"]),
        )
    return entries


def _state_about_time_from_row(row: sqlite3.Row) -> dict[str, Any] | None:
    payload = json.loads(row["payload_json"])
    if not isinstance(payload, dict):
        raise RuntimeError("memory_states.payload_json must decode to object")
    source_texts = [str(row["body_text"]).strip()]
    summary_text = payload.get("summary_text")
    if isinstance(summary_text, str) and summary_text.strip():
        source_texts.append(summary_text.strip())
    about_years: list[int] = []
    life_stage: str | None = None
    for source_text in source_texts:
        for about_year in about_years_from_text(source_text):
            if about_year not in about_years:
                about_years.append(about_year)
        if life_stage is None:
            life_stage = life_stage_from_text(source_text)
    if not about_years and life_stage is None:
        return None
    return {
        "about_start_ts": None,
        "about_end_ts": None,
        "about_year_start": about_years[0] if about_years else None,
        "about_year_end": about_years[-1] if about_years else None,
        "life_stage": life_stage,
        "confidence": 0.82 if about_years else 0.58,
    }


def _state_entity_entries_from_row(row: sqlite3.Row) -> list[dict[str, Any]]:
    payload = json.loads(row["payload_json"])
    if not isinstance(payload, dict):
        raise RuntimeError("memory_states.payload_json must decode to object")
    entries: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    query = payload.get("query")
    if isinstance(query, str) and query.strip():
        _append_state_entity_entry(
            entries=entries,
            seen_keys=seen_keys,
            entity_type_norm="topic",
            entity_name_raw=query,
            confidence=0.86,
        )
    source_task_id = payload.get("source_task_id")
    if isinstance(source_task_id, str) and source_task_id.strip():
        _append_state_entity_entry(
            entries=entries,
            seen_keys=seen_keys,
            entity_type_norm="task",
            entity_name_raw=source_task_id,
            confidence=0.72,
        )
    fact_kind = payload.get("fact_kind")
    if isinstance(fact_kind, str) and fact_kind.strip():
        _append_state_entity_entry(
            entries=entries,
            seen_keys=seen_keys,
            entity_type_norm="fact_kind",
            entity_name_raw=fact_kind,
            confidence=0.58,
        )
    summary_text = payload.get("summary_text")
    if isinstance(summary_text, str) and summary_text.strip():
        _append_state_entity_entry(
            entries=entries,
            seen_keys=seen_keys,
            entity_type_norm="summary_phrase",
            entity_name_raw=summary_text,
            confidence=0.48,
        )
    return entries


def _append_state_entity_entry(
    *,
    entries: list[dict[str, Any]],
    seen_keys: set[tuple[str, str]],
    entity_type_norm: str,
    entity_name_raw: str,
    confidence: float,
) -> None:
    entity_name_norm = _normalized_entity_name(entity_name_raw)
    if not entity_name_norm:
        return
    entity_key = (entity_type_norm, entity_name_norm)
    if entity_key in seen_keys:
        return
    seen_keys.add(entity_key)
    entries.append(
        {
            "entity_type_norm": entity_type_norm,
            "entity_name_raw": entity_name_raw.strip(),
            "entity_name_norm": entity_name_norm,
            "confidence": confidence,
        }
    )


def _normalized_entity_name(text: str) -> str:
    return "".join(text.strip().lower().split())


# Block: Recent event helpers
def _recent_event_entry(row: sqlite3.Row) -> dict[str, Any]:
    entry = {
        "event_id": str(row["event_id"]),
        "source": str(row["source"]),
        "kind": str(row["kind"]),
        "summary_text": _event_summary_text(row),
        "created_at": int(row["created_at"]),
    }
    preview_text = row["preview_text"]
    if isinstance(preview_text, str) and preview_text.strip():
        entry["preview_text"] = preview_text.strip()
    return entry


def _event_summary_text(row: sqlite3.Row) -> str:
    for key in ("result_summary", "observation_summary", "action_summary"):
        value = row[key]
        if isinstance(value, str) and value:
            return value
    return str(row["kind"])


# Block: JSON decode helpers
def _decoded_object_json(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    decoded = json.loads(str(value))
    if not isinstance(decoded, dict):
        raise RuntimeError("decoded JSON must be an object")
    return decoded


def _decoded_string_array_json(value: Any) -> list[str]:
    if value is None:
        return []
    decoded = json.loads(str(value))
    if not isinstance(decoded, list):
        raise RuntimeError("decoded JSON must be an array")
    normalized: list[str] = []
    for item in decoded:
        if not isinstance(item, str) or not item:
            raise RuntimeError("decoded JSON array entries must be non-empty strings")
        normalized.append(item)
    return normalized
