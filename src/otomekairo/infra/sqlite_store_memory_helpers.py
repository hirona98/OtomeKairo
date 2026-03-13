"""Memory snapshot, write-memory, and preview helper functions for the SQLite state store."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from otomekairo.infra.sqlite_store_legacy_runtime import _bounded_float
from otomekairo.infra.sqlite_store_snapshots import (
    _decoded_object_json,
    _decoded_string_array_json,
    _event_summary_text,
)


# Block: Event fetch by ids
def _fetch_events_for_ids(
    *,
    connection: sqlite3.Connection,
    event_ids: list[str],
) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in event_ids)
    rows = connection.execute(
        f"""
        SELECT
            event_id,
            source,
            kind,
            searchable,
            updated_at,
            observation_summary,
            action_summary,
            result_summary,
            payload_ref_json,
            input_journal_refs_json,
            created_at,
            COALESCE(updated_at, created_at) AS source_updated_at
        FROM events
        WHERE event_id IN ({placeholders})
        """,
        tuple(event_ids),
    ).fetchall()
    rows_by_id = {str(row["event_id"]): row for row in rows}
    ordered_rows: list[sqlite3.Row] = []
    for event_id in event_ids:
        row = rows_by_id.get(event_id)
        if row is None:
            raise RuntimeError("source event for write_memory is missing")
        ordered_rows.append(row)
    return ordered_rows


# Block: Event link fetch for snapshot
def _fetch_event_links_for_memory_snapshot(
    *,
    connection: sqlite3.Connection,
    event_ids: list[str],
) -> list[sqlite3.Row]:
    if not event_ids:
        return []
    placeholders = ",".join("?" for _ in event_ids)
    return connection.execute(
        f"""
        SELECT
            event_link_id,
            from_event_id,
            to_event_id,
            label,
            confidence,
            created_at,
            updated_at
        FROM event_links
        WHERE from_event_id IN ({placeholders})
           OR to_event_id IN ({placeholders})
        ORDER BY updated_at DESC
        LIMIT 48
        """,
        tuple(event_ids + event_ids),
    ).fetchall()


# Block: Event entity fetch for snapshot
def _fetch_event_entities_for_memory_snapshot(
    *,
    connection: sqlite3.Connection,
    event_ids: list[str],
) -> list[sqlite3.Row]:
    if not event_ids:
        return []
    placeholders = ",".join("?" for _ in event_ids)
    return connection.execute(
        f"""
        SELECT
            event_entity_id,
            event_id,
            entity_type_norm,
            entity_name_raw,
            entity_name_norm,
            confidence,
            created_at
        FROM event_entities
        WHERE event_id IN ({placeholders})
        ORDER BY created_at DESC
        LIMIT 64
        """,
        tuple(event_ids),
    ).fetchall()


# Block: Event about-time fetch for snapshot
def _fetch_event_about_time_for_memory_snapshot(
    *,
    connection: sqlite3.Connection,
    event_ids: list[str],
) -> list[sqlite3.Row]:
    if not event_ids:
        return []
    placeholders = ",".join("?" for _ in event_ids)
    return connection.execute(
        f"""
        SELECT
            event_about_time_id,
            event_id,
            about_start_ts,
            about_end_ts,
            about_year_start,
            about_year_end,
            life_stage,
            confidence,
            created_at,
            updated_at
        FROM event_about_time
        WHERE event_id IN ({placeholders})
        ORDER BY updated_at DESC
        LIMIT 32
        """,
        tuple(event_ids),
    ).fetchall()


# Block: Event about-time fetch for preview
def _fetch_event_about_time_for_preview(
    *,
    connection: sqlite3.Connection,
    event_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT
            event_about_time_id,
            event_id,
            about_start_ts,
            about_end_ts,
            about_year_start,
            about_year_end,
            life_stage,
            confidence,
            created_at,
            updated_at
        FROM event_about_time
        WHERE event_id = ?
        """,
        (event_id,),
    ).fetchone()


# Block: Event affect fetch for preview
def _fetch_event_affect_for_preview(
    *,
    connection: sqlite3.Connection,
    event_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT moment_affect_text, moment_affect_labels_json
        FROM event_affects
        WHERE event_id = ?
        """,
        (event_id,),
    ).fetchone()


# Block: Event thread fetch for snapshot
def _fetch_event_threads_for_memory_snapshot(
    *,
    connection: sqlite3.Connection,
    event_ids: list[str],
) -> list[sqlite3.Row]:
    if not event_ids:
        return []
    placeholders = ",".join("?" for _ in event_ids)
    return connection.execute(
        f"""
        SELECT
            event_thread_id,
            event_id,
            thread_key,
            thread_role,
            confidence,
            created_at,
            updated_at
        FROM event_threads
        WHERE event_id IN ({placeholders})
        ORDER BY updated_at DESC
        LIMIT 48
        """,
        tuple(event_ids),
    ).fetchall()


# Block: State link fetch for snapshot
def _fetch_state_links_for_memory_snapshot(
    *,
    connection: sqlite3.Connection,
    memory_state_ids: list[str],
) -> list[sqlite3.Row]:
    if not memory_state_ids:
        return []
    placeholders = ",".join("?" for _ in memory_state_ids)
    return connection.execute(
        f"""
        SELECT
            state_link_id,
            from_state_id,
            to_state_id,
            label,
            confidence,
            created_at,
            updated_at
        FROM state_links
        WHERE from_state_id IN ({placeholders})
           OR to_state_id IN ({placeholders})
        ORDER BY updated_at DESC
        LIMIT 48
        """,
        tuple(memory_state_ids + memory_state_ids),
    ).fetchall()


# Block: State about-time fetch for snapshot
def _fetch_state_about_time_for_memory_snapshot(
    *,
    connection: sqlite3.Connection,
    memory_state_ids: list[str],
) -> list[sqlite3.Row]:
    if not memory_state_ids:
        return []
    placeholders = ",".join("?" for _ in memory_state_ids)
    return connection.execute(
        f"""
        SELECT
            state_about_time_id,
            memory_state_id,
            about_start_ts,
            about_end_ts,
            about_year_start,
            about_year_end,
            life_stage,
            confidence,
            created_at,
            updated_at
        FROM state_about_time
        WHERE memory_state_id IN ({placeholders})
        ORDER BY updated_at DESC
        LIMIT 32
        """,
        tuple(memory_state_ids),
    ).fetchall()


# Block: State entity fetch for snapshot
def _fetch_state_entities_for_memory_snapshot(
    *,
    connection: sqlite3.Connection,
    memory_state_ids: list[str],
) -> list[sqlite3.Row]:
    if not memory_state_ids:
        return []
    placeholders = ",".join("?" for _ in memory_state_ids)
    return connection.execute(
        f"""
        SELECT
            state_entity_id,
            memory_state_id,
            entity_type_norm,
            entity_name_raw,
            entity_name_norm,
            confidence,
            created_at
        FROM state_entities
        WHERE memory_state_id IN ({placeholders})
        ORDER BY created_at DESC
        LIMIT 64
        """,
        tuple(memory_state_ids),
    ).fetchall()


# Block: Event snapshot refs for write-memory
def _event_snapshot_refs_for_write_memory_job(
    *,
    connection: sqlite3.Connection,
    event_ids: list[str],
) -> list[dict[str, int | str]]:
    return [
        {
            "event_id": str(row["event_id"]),
            "event_updated_at": int(row["source_updated_at"]),
        }
        for row in _fetch_events_for_ids(
            connection=connection,
            event_ids=event_ids,
        )
    ]


# Block: Action history fetch for write-memory
def _fetch_action_history_for_cycle(
    *,
    connection: sqlite3.Connection,
    cycle_id: str,
    action_type: str,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT command_json, observed_effects_json
        FROM action_history
        WHERE cycle_id = ?
          AND action_type = ?
          AND status = 'succeeded'
        ORDER BY started_at ASC
        """,
        (cycle_id, action_type),
    ).fetchall()


# Block: Browse query extraction
def _browse_query_from_action_history(command_json: dict[str, Any]) -> str:
    parameters = command_json.get("parameters")
    if not isinstance(parameters, dict):
        raise RuntimeError("browse action_history.command_json.parameters must be object")
    query = parameters.get("query")
    if not isinstance(query, str) or not query:
        raise RuntimeError("browse action_history.command_json.parameters.query must be non-empty string")
    return query


# Block: Browse summary extraction
def _browse_summary_from_action_history(observed_effects_json: dict[str, Any]) -> str:
    summary_text = observed_effects_json.get("summary_text")
    if not isinstance(summary_text, str) or not summary_text:
        raise RuntimeError("browse action_history.observed_effects_json.summary_text must be non-empty string")
    return summary_text


# Block: Browse task-id extraction
def _browse_task_id_from_action_history(command_json: dict[str, Any]) -> str:
    related_task_id = command_json.get("related_task_id")
    if not isinstance(related_task_id, str) or not related_task_id:
        raise RuntimeError("browse action_history.command_json.related_task_id must be non-empty string")
    return related_task_id


# Block: Write-memory event entries
def _write_memory_plan_event_entries(event_rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [
        {
            "event_id": str(row["event_id"]),
            "kind": str(row["kind"]),
            "summary_text": _event_summary_text(row),
            "source_updated_at": int(row["source_updated_at"]),
        }
        for row in event_rows
    ]


# Block: Write-memory action entries
def _action_entries_for_write_memory_plan(
    *,
    connection: sqlite3.Connection,
    cycle_id: str,
) -> list[dict[str, Any]]:
    action_rows = connection.execute(
        """
        SELECT action_type,
               status,
               failure_mode,
               command_json,
               observed_effects_json,
               adapter_trace_ref_json
        FROM action_history
        WHERE cycle_id = ?
        ORDER BY started_at ASC
        """,
        (cycle_id,),
    ).fetchall()
    return [
        {
            "action_type": str(row["action_type"]),
            "status": str(row["status"]),
            "failure_mode": (
                str(row["failure_mode"])
                if row["failure_mode"] is not None
                else None
            ),
            "command": _decoded_optional_json_object(
                raw_value=row["command_json"],
                field_name="action_history.command_json",
            ),
            "observed_effects": _decoded_optional_json_object(
                raw_value=row["observed_effects_json"],
                field_name="action_history.observed_effects_json",
            ),
            "adapter_trace": _decoded_optional_json_object(
                raw_value=row["adapter_trace_ref_json"],
                field_name="action_history.adapter_trace_ref_json",
            ),
        }
        for row in action_rows
    ]


# Block: Optional JSON object decode
def _decoded_optional_json_object(
    *,
    raw_value: Any,
    field_name: str,
) -> dict[str, Any] | None:
    if raw_value is None:
        return None
    decoded_value = json.loads(raw_value)
    if not isinstance(decoded_value, dict):
        raise RuntimeError(f"{field_name} must decode to object")
    return decoded_value


# Block: Browse fact entries for write-memory
def _browse_fact_entries_for_write_memory_plan(
    *,
    connection: sqlite3.Connection,
    cycle_id: str,
) -> list[dict[str, Any]]:
    action_rows = _fetch_action_history_for_cycle(
        connection=connection,
        cycle_id=cycle_id,
        action_type="complete_browse_task",
    )
    browse_fact_entries: list[dict[str, Any]] = []
    for action_row in action_rows:
        command_json = json.loads(action_row["command_json"])
        observed_effects_json = json.loads(action_row["observed_effects_json"])
        browse_fact_entries.append(
            {
                "query": _browse_query_from_action_history(command_json),
                "summary_text": _browse_summary_from_action_history(observed_effects_json),
                "source_task_id": _browse_task_id_from_action_history(command_json),
            }
        )
    return browse_fact_entries


# Block: Recent dialogue context for write-memory
def _recent_dialogue_context_for_write_memory_plan(
    *,
    connection: sqlite3.Connection,
    before_created_at: int,
) -> list[dict[str, Any]]:
    context_rows = connection.execute(
        """
        SELECT
            event_id,
            source,
            kind,
            observation_summary,
            action_summary,
            result_summary,
            created_at
        FROM events
        WHERE searchable = 1
          AND created_at < ?
          AND kind IN ('observation', 'external_response')
          AND source IN ('web_input', 'microphone', 'runtime')
        ORDER BY created_at DESC
        LIMIT 6
        """,
        (before_created_at,),
    ).fetchall()
    if not context_rows:
        return []
    event_ids = [str(context_row["event_id"]) for context_row in context_rows]
    thread_rows = _fetch_event_threads_for_memory_snapshot(
        connection=connection,
        event_ids=event_ids,
    )
    thread_keys_by_event_id: dict[str, list[str]] = {}
    for thread_row in thread_rows:
        event_id = str(thread_row["event_id"])
        thread_key = str(thread_row["thread_key"])
        if event_id not in thread_keys_by_event_id:
            thread_keys_by_event_id[event_id] = []
        if thread_key not in thread_keys_by_event_id[event_id]:
            thread_keys_by_event_id[event_id].append(thread_key)
    return [
        {
            "event_id": str(context_row["event_id"]),
            "source": str(context_row["source"]),
            "kind": str(context_row["kind"]),
            "summary_text": _event_summary_text(context_row),
            "thread_keys": thread_keys_by_event_id.get(str(context_row["event_id"]), []),
            "created_at": int(context_row["created_at"]),
        }
        for context_row in context_rows
    ]


# Block: Existing long mood entry for write-memory
def _write_memory_plan_long_mood_entry(
    *,
    connection: sqlite3.Connection,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT
            memory_state_id,
            body_text,
            payload_json,
            confidence,
            importance,
            memory_strength,
            last_confirmed_at,
            evidence_event_ids_json,
            created_at,
            updated_at
        FROM memory_states
        WHERE memory_kind = 'long_mood_state'
        ORDER BY searchable DESC, updated_at DESC, created_at DESC, memory_state_id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return {
        "memory_state_id": str(row["memory_state_id"]),
        "body_text": str(row["body_text"]),
        "payload": _decoded_object_json(row["payload_json"]),
        "confidence": float(row["confidence"]),
        "importance": float(row["importance"]),
        "memory_strength": float(row["memory_strength"]),
        "last_confirmed_at": int(row["last_confirmed_at"]),
        "evidence_event_ids": _decoded_string_array_json(row["evidence_event_ids_json"]),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }


# Block: Existing preference entries for write-memory
def _write_memory_plan_preference_entries(
    *,
    connection: sqlite3.Connection,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            owner_scope,
            target_entity_ref_json,
            target_key,
            domain,
            polarity,
            status,
            confidence,
            evidence_event_ids_json,
            created_at,
            updated_at
        FROM preference_memory
        WHERE owner_scope = 'self'
        ORDER BY updated_at DESC, created_at DESC, preference_id DESC
        """
    ).fetchall()
    entries: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for row in rows:
        target_entity_ref = _decoded_object_json(row["target_entity_ref_json"])
        target_key = row["target_key"]
        if not isinstance(target_key, str) or not target_key:
            continue
        entry_key = (
            str(row["domain"]),
            target_key,
            str(row["polarity"]),
        )
        if entry_key in seen_keys:
            continue
        seen_keys.add(entry_key)
        entries.append(
            {
                "owner_scope": str(row["owner_scope"]),
                "target_entity_ref": target_entity_ref,
                "domain": str(row["domain"]),
                "polarity": str(row["polarity"]),
                "status": str(row["status"]),
                "confidence": float(row["confidence"]),
                "evidence_event_ids": _decoded_string_array_json(row["evidence_event_ids_json"]),
                "created_at": int(row["created_at"]),
                "updated_at": int(row["updated_at"]),
            }
        )
    return entries


# Block: Current emotion derivation from long mood
def _current_emotion_json_from_long_mood_payload(
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    current_vad = payload.get("current")
    if not isinstance(current_vad, dict):
        raise RuntimeError("long_mood_state.payload.current must be an object")
    active_biases = payload.get("active_biases")
    if not isinstance(active_biases, dict):
        raise RuntimeError("long_mood_state.payload.active_biases must be an object")
    primary_label = payload.get("primary_label")
    if not isinstance(primary_label, str) or not primary_label:
        raise RuntimeError("long_mood_state.payload.primary_label must be non-empty string")
    stability = payload.get("stability")
    if isinstance(stability, bool) or not isinstance(stability, (int, float)):
        raise RuntimeError("long_mood_state.payload.stability must be numeric")
    return {
        "primary_label": primary_label,
        "valence": _bounded_float(current_vad.get("v")),
        "arousal": _bounded_float(current_vad.get("a")),
        "dominance": _bounded_float(current_vad.get("d")),
        "stability": round(max(0.0, min(1.0, float(stability))), 2),
        "active_biases": {
            "caution_bias": _bounded_float(active_biases.get("caution_bias")),
            "approach_bias": _bounded_float(active_biases.get("approach_bias")),
            "avoidance_bias": _bounded_float(active_biases.get("avoidance_bias")),
            "speech_intensity_bias": _bounded_float(active_biases.get("speech_intensity_bias")),
        },
    }


# Block: Event preview text build
def _build_event_preview_text(
    *,
    event_row: sqlite3.Row,
    event_entity_rows: list[sqlite3.Row],
    event_thread_rows: list[sqlite3.Row],
    event_about_time_row: sqlite3.Row | None,
    event_affect_row: sqlite3.Row | None,
) -> str:
    summary_text = _event_summary_text(event_row).strip()
    preview_parts = [
        summary_text if summary_text else str(event_row["kind"]),
        f"source={event_row['source']}",
        f"kind={event_row['kind']}",
    ]
    entity_terms = _event_preview_entity_terms(event_entity_rows)
    if entity_terms:
        preview_parts.append("entities=" + ", ".join(entity_terms))
    thread_terms = _event_preview_thread_terms(event_thread_rows)
    if thread_terms:
        preview_parts.append("threads=" + ", ".join(thread_terms))
    about_time_term = _event_preview_about_time_term(event_about_time_row)
    if about_time_term is not None:
        preview_parts.append(about_time_term)
    affect_term = _event_preview_affect_term(event_affect_row)
    if affect_term is not None:
        preview_parts.append(affect_term)
    return " / ".join(preview_parts)[:320]


# Block: Event preview entity terms
def _event_preview_entity_terms(event_entity_rows: list[sqlite3.Row]) -> list[str]:
    entity_terms: list[str] = []
    for row in event_entity_rows:
        entity_name_raw = str(row["entity_name_raw"]).strip()
        if entity_name_raw and entity_name_raw not in entity_terms:
            entity_terms.append(entity_name_raw)
        if len(entity_terms) >= 4:
            break
    return entity_terms


# Block: Event preview thread terms
def _event_preview_thread_terms(event_thread_rows: list[sqlite3.Row]) -> list[str]:
    thread_terms: list[str] = []
    for row in event_thread_rows:
        thread_key = str(row["thread_key"]).strip()
        if thread_key and thread_key not in thread_terms:
            thread_terms.append(thread_key)
        if len(thread_terms) >= 3:
            break
    return thread_terms


# Block: Event preview affect term
def _event_preview_affect_term(event_affect_row: sqlite3.Row | None) -> str | None:
    if event_affect_row is None:
        return None
    affect_labels = json.loads(event_affect_row["moment_affect_labels_json"])
    if not isinstance(affect_labels, list):
        raise RuntimeError("event_affects.moment_affect_labels_json must decode to list")
    normalized_labels = [
        str(label)
        for label in affect_labels
        if isinstance(label, str) and label
    ]
    if normalized_labels:
        return "affect=" + ", ".join(normalized_labels[:3])
    affect_text = str(event_affect_row["moment_affect_text"]).strip()
    if not affect_text:
        return None
    return "affect_text=" + affect_text[:80]


# Block: Event preview about-time term
def _event_preview_about_time_term(event_about_time_row: sqlite3.Row | None) -> str | None:
    if event_about_time_row is None:
        return None
    about_terms: list[str] = []
    date_range_text = _event_preview_about_time_date_range(event_about_time_row)
    if date_range_text is not None:
        about_terms.append(date_range_text)
    about_year_start = event_about_time_row["about_year_start"]
    about_year_end = event_about_time_row["about_year_end"]
    if isinstance(about_year_start, int):
        if isinstance(about_year_end, int) and about_year_end != about_year_start:
            about_terms.append(f"{about_year_start}-{about_year_end}")
        else:
            about_terms.append(str(about_year_start))
    life_stage = event_about_time_row["life_stage"]
    if isinstance(life_stage, str) and life_stage:
        about_terms.append(life_stage)
    if not about_terms:
        return None
    return "about_time=" + ", ".join(about_terms)


# Block: Event preview date range
def _event_preview_about_time_date_range(event_about_time_row: sqlite3.Row) -> str | None:
    about_start_ts = event_about_time_row["about_start_ts"]
    about_end_ts = event_about_time_row["about_end_ts"]
    if isinstance(about_start_ts, int):
        start_text = _event_preview_local_date_text(about_start_ts)
        if isinstance(about_end_ts, int) and about_end_ts != about_start_ts:
            return f"{start_text}..{_event_preview_local_date_text(about_end_ts)}"
        return start_text
    if isinstance(about_end_ts, int):
        return _event_preview_local_date_text(about_end_ts)
    return None


# Block: Event preview local date
def _event_preview_local_date_text(unix_ms: int) -> str:
    return datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")
