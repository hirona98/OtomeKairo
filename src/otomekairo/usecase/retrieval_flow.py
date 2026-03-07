"""Build deterministic retrieval plan and memory bundle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


# Block: Retrieval constants
WORKING_MEMORY_LIMIT = 3
EPISODIC_LIMIT = 3
SEMANTIC_LIMIT = 3
AFFECTIVE_LIMIT = 2
RELATIONSHIP_LIMIT = 2
REFLECTION_LIMIT = 2
RECENT_EVENT_LIMIT = 5


# Block: Retrieval artifacts
@dataclass(frozen=True, slots=True)
class RetrievalArtifacts:
    memory_bundle: dict[str, Any]
    retrieval_plan: dict[str, Any]
    candidates_json: dict[str, Any]
    selected_json: dict[str, Any]


# Block: Public builder
def build_retrieval_artifacts(
    *,
    memory_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
    task_snapshot: dict[str, Any],
    resolved_at: int,
) -> RetrievalArtifacts:
    retrieval_plan = _build_retrieval_plan(
        current_observation=current_observation,
        task_snapshot=task_snapshot,
    )
    selected_working_memory, working_traces = _select_memory_entries(
        slot_name="working_memory_items",
        memory_entries=memory_snapshot["working_memory_items"],
        current_observation=current_observation,
        retrieval_plan=retrieval_plan,
        limit=WORKING_MEMORY_LIMIT,
    )
    selected_episodic_items, episodic_traces = _select_memory_entries(
        slot_name="episodic_items",
        memory_entries=memory_snapshot["episodic_items"],
        current_observation=current_observation,
        retrieval_plan=retrieval_plan,
        limit=EPISODIC_LIMIT,
    )
    selected_semantic_items, semantic_traces = _select_memory_entries(
        slot_name="semantic_items",
        memory_entries=memory_snapshot["semantic_items"],
        current_observation=current_observation,
        retrieval_plan=retrieval_plan,
        limit=SEMANTIC_LIMIT,
    )
    selected_affective_items, affective_traces = _select_memory_entries(
        slot_name="affective_items",
        memory_entries=memory_snapshot["affective_items"],
        current_observation=current_observation,
        retrieval_plan=retrieval_plan,
        limit=AFFECTIVE_LIMIT,
    )
    selected_relationship_items, relationship_traces = _select_memory_entries(
        slot_name="relationship_items",
        memory_entries=memory_snapshot["relationship_items"],
        current_observation=current_observation,
        retrieval_plan=retrieval_plan,
        limit=RELATIONSHIP_LIMIT,
    )
    selected_reflection_items, reflection_traces = _select_memory_entries(
        slot_name="reflection_items",
        memory_entries=memory_snapshot["reflection_items"],
        current_observation=current_observation,
        retrieval_plan=retrieval_plan,
        limit=REFLECTION_LIMIT,
    )
    selected_recent_events, recent_event_traces = _select_recent_events(
        event_entries=memory_snapshot["recent_event_window"],
        current_observation=current_observation,
        retrieval_plan=retrieval_plan,
        limit=RECENT_EVENT_LIMIT,
    )
    selection_trace = [
        *working_traces,
        *episodic_traces,
        *semantic_traces,
        *affective_traces,
        *relationship_traces,
        *reflection_traces,
        *recent_event_traces,
    ]
    memory_bundle = {
        "working_memory_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in selected_working_memory
        ],
        "episodic_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in selected_episodic_items
        ],
        "semantic_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in selected_semantic_items
        ],
        "affective_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in selected_affective_items
        ],
        "relationship_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in selected_relationship_items
        ],
        "reflection_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in selected_reflection_items
        ],
        "recent_event_window": [
            _recent_event_for_cognition(event_entry, resolved_at=resolved_at)
            for event_entry in selected_recent_events
        ],
    }
    return RetrievalArtifacts(
        memory_bundle=memory_bundle,
        retrieval_plan=retrieval_plan,
        candidates_json=_build_candidates_json(memory_snapshot=memory_snapshot),
        selected_json=_build_selected_json(
            memory_bundle=memory_bundle,
            selection_trace=selection_trace,
        ),
    )


# Block: Retrieval plan
def _build_retrieval_plan(
    *,
    current_observation: dict[str, Any],
    task_snapshot: dict[str, Any],
) -> dict[str, Any]:
    explicit_years = _explicit_years(current_observation=current_observation)
    return {
        "mode": _retrieval_mode(
            current_observation=current_observation,
            explicit_years=explicit_years,
        ),
        "queries": _build_queries(
            current_observation=current_observation,
            task_snapshot=task_snapshot,
        ),
        "time_hint": {
            "explicit_years": explicit_years,
            "has_explicit_time_hint": bool(explicit_years),
        },
        "limits": {
            "working_memory_items": WORKING_MEMORY_LIMIT,
            "episodic_items": EPISODIC_LIMIT,
            "semantic_items": SEMANTIC_LIMIT,
            "affective_items": AFFECTIVE_LIMIT,
            "relationship_items": RELATIONSHIP_LIMIT,
            "reflection_items": REFLECTION_LIMIT,
            "recent_event_window": RECENT_EVENT_LIMIT,
        },
    }


# Block: Query helpers
def _build_queries(
    *,
    current_observation: dict[str, Any],
    task_snapshot: dict[str, Any],
) -> list[str]:
    queries: list[str] = []
    observation_text = current_observation["observation_text"]
    if not isinstance(observation_text, str) or not observation_text:
        raise ValueError("current_observation.observation_text must be non-empty string")
    _append_unique_text(queries, observation_text)
    query_hint = _observation_query_hint(current_observation)
    if query_hint is not None:
        _append_unique_text(queries, query_hint)
    for task_entry in task_snapshot["active_tasks"][:2]:
        goal_hint = task_entry.get("goal_hint")
        if isinstance(goal_hint, str) and goal_hint:
            _append_unique_text(queries, goal_hint)
    for task_entry in task_snapshot["waiting_external_tasks"][:1]:
        goal_hint = task_entry.get("goal_hint")
        if isinstance(goal_hint, str) and goal_hint:
            _append_unique_text(queries, goal_hint)
    return queries


def _append_unique_text(target: list[str], text: str) -> None:
    normalized = text.strip()
    if not normalized:
        return
    if normalized not in target:
        target.append(normalized)


# Block: Mode helpers
def _retrieval_mode(
    *,
    current_observation: dict[str, Any],
    explicit_years: list[int],
) -> str:
    if explicit_years:
        return "explicit_about_time"
    observation_text = str(current_observation["observation_text"])
    if any(token in observation_text for token in ("失敗", "原因", "再発", "避けたい", "反省", "注意")):
        return "reflection_recall"
    if current_observation["input_kind"] == "network_result":
        return "task_targeted"
    if isinstance(current_observation.get("source_task_id"), str):
        return "task_targeted"
    return "associative_recent"


def _explicit_years(
    *,
    current_observation: dict[str, Any],
) -> list[int]:
    years: list[int] = []
    for token in _text_hint_tokens(str(current_observation["observation_text"])):
        if len(token) != 4 or not token.isdigit():
            continue
        year = int(token)
        if 1900 <= year <= 2100 and year not in years:
            years.append(year)
    return years


# Block: Candidate builders
def _build_candidates_json(
    *,
    memory_snapshot: dict[str, Any],
) -> dict[str, Any]:
    category_counts = {
        "working_memory_items": len(memory_snapshot["working_memory_items"]),
        "episodic_items": len(memory_snapshot["episodic_items"]),
        "semantic_items": len(memory_snapshot["semantic_items"]),
        "affective_items": len(memory_snapshot["affective_items"]),
        "relationship_items": len(memory_snapshot["relationship_items"]),
        "reflection_items": len(memory_snapshot["reflection_items"]),
        "recent_event_window": len(memory_snapshot["recent_event_window"]),
    }
    return {
        "total_candidate_count": sum(category_counts.values()),
        "category_counts": category_counts,
        "non_empty_categories": [
            category_name
            for category_name, count in category_counts.items()
            if count > 0
        ],
    }


def _build_selected_json(
    *,
    memory_bundle: dict[str, Any],
    selection_trace: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "selected_counts": {
            "working_memory_items": len(memory_bundle["working_memory_items"]),
            "episodic_items": len(memory_bundle["episodic_items"]),
            "semantic_items": len(memory_bundle["semantic_items"]),
            "affective_items": len(memory_bundle["affective_items"]),
            "relationship_items": len(memory_bundle["relationship_items"]),
            "reflection_items": len(memory_bundle["reflection_items"]),
            "recent_event_window": len(memory_bundle["recent_event_window"]),
        },
        "selected_refs": {
            "working_memory_item_ids": [
                str(item["memory_state_id"])
                for item in memory_bundle["working_memory_items"]
            ],
            "episodic_item_ids": [
                str(item["memory_state_id"])
                for item in memory_bundle["episodic_items"]
            ],
            "semantic_item_ids": [
                str(item["memory_state_id"])
                for item in memory_bundle["semantic_items"]
            ],
            "affective_item_ids": [
                str(item["memory_state_id"])
                for item in memory_bundle["affective_items"]
            ],
            "relationship_item_ids": [
                str(item["memory_state_id"])
                for item in memory_bundle["relationship_items"]
            ],
            "reflection_item_ids": [
                str(item["memory_state_id"])
                for item in memory_bundle["reflection_items"]
            ],
            "recent_event_ids": [
                str(item["event_id"])
                for item in memory_bundle["recent_event_window"]
            ],
        },
        "selection_trace": selection_trace,
    }


# Block: Memory selection
def _select_memory_entries(
    *,
    slot_name: str,
    memory_entries: list[dict[str, Any]],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if limit <= 0:
        raise ValueError("memory selection limit must be positive")
    scored_entries: list[tuple[float, int, dict[str, Any], list[str]]] = []
    for memory_entry in memory_entries:
        score, reason_codes = _memory_relevance_score(
            memory_entry=memory_entry,
            current_observation=current_observation,
            retrieval_plan=retrieval_plan,
        )
        if score <= 0.0:
            continue
        scored_entries.append(
            (
                score,
                int(memory_entry["updated_at"]),
                memory_entry,
                reason_codes,
            )
        )
    scored_entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected_rows = scored_entries[:limit]
    return (
        [memory_entry for _, _, memory_entry, _ in selected_rows],
        [
            {
                "slot": slot_name,
                "item_ref": _memory_item_ref(memory_entry),
                "score": round(score, 3),
                "reason_codes": reason_codes,
            }
            for score, _, memory_entry, reason_codes in selected_rows
        ],
    )


def _select_recent_events(
    *,
    event_entries: list[dict[str, Any]],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if limit <= 0:
        raise ValueError("recent event selection limit must be positive")
    scored_entries: list[tuple[float, int, dict[str, Any], list[str]]] = []
    for event_entry in event_entries:
        score, reason_codes = _event_relevance_score(
            event_entry=event_entry,
            current_observation=current_observation,
            retrieval_plan=retrieval_plan,
        )
        if score <= 0.0:
            continue
        scored_entries.append(
            (
                score,
                int(event_entry["created_at"]),
                event_entry,
                reason_codes,
            )
        )
    scored_entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected_rows = scored_entries[:limit]
    return (
        [event_entry for _, _, event_entry, _ in selected_rows],
        [
            {
                "slot": "recent_event_window",
                "item_ref": f"event:{event_entry['event_id']}",
                "score": round(score, 3),
                "reason_codes": reason_codes,
            }
            for score, _, event_entry, reason_codes in selected_rows
        ],
    )


# Block: Scoring helpers
def _memory_relevance_score(
    *,
    memory_entry: dict[str, Any],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
) -> tuple[float, list[str]]:
    body_text = memory_entry["body_text"]
    if not isinstance(body_text, str) or not body_text:
        raise ValueError("memory entry body_text must be non-empty string")
    payload = memory_entry["payload"]
    if not isinstance(payload, dict):
        raise ValueError("memory entry payload must be object")
    reason_codes: list[str] = []
    score = 0.0
    for text_hint in _observation_text_hints(current_observation):
        if text_hint in body_text:
            score += 1.0
            _append_reason(reason_codes, "matched_observation_text")
        if _payload_contains_text_hint(payload=payload, text_hint=text_hint):
            score += 0.6
            _append_reason(reason_codes, "matched_payload_text")
    query_hint = _observation_query_hint(current_observation)
    if query_hint is not None and payload.get("query") == query_hint:
        score += 1.5
        _append_reason(reason_codes, "matched_query")
    source_task_id = current_observation.get("source_task_id")
    if isinstance(source_task_id, str) and source_task_id:
        if payload.get("source_task_id") == source_task_id:
            score += 2.0
            _append_reason(reason_codes, "matched_source_task")
    mode_bonus = _mode_bonus(
        retrieval_plan=retrieval_plan,
        memory_kind=str(memory_entry["memory_kind"]),
    )
    if mode_bonus > 0.0:
        score += mode_bonus
        _append_reason(reason_codes, "mode_priority")
    importance = min(1.0, float(memory_entry["importance"]))
    memory_strength = min(1.0, float(memory_entry["memory_strength"]))
    if importance > 0.0:
        score += importance * 0.2
        _append_reason(reason_codes, "importance_bias")
    if memory_strength > 0.0:
        score += memory_strength * 0.2
        _append_reason(reason_codes, "memory_strength_bias")
    return (score, reason_codes)


def _event_relevance_score(
    *,
    event_entry: dict[str, Any],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
) -> tuple[float, list[str]]:
    summary_text = event_entry["summary_text"]
    if not isinstance(summary_text, str) or not summary_text:
        raise ValueError("recent event summary_text must be non-empty string")
    reason_codes: list[str] = []
    score = 0.0
    for text_hint in _observation_text_hints(current_observation):
        if text_hint in summary_text:
            score += 1.0
            _append_reason(reason_codes, "matched_observation_text")
    if current_observation["input_kind"] == "network_result" and event_entry["source"] == "network_result":
        score += 1.0
        _append_reason(reason_codes, "same_input_kind")
    if retrieval_plan["mode"] == "associative_recent":
        score += 0.4
        _append_reason(reason_codes, "mode_priority")
    return (score, reason_codes)


def _mode_bonus(
    *,
    retrieval_plan: dict[str, Any],
    memory_kind: str,
) -> float:
    mode = str(retrieval_plan["mode"])
    if mode == "task_targeted" and memory_kind in {"summary", "fact", "relation", "preference"}:
        return 0.40
    if mode == "reflection_recall" and memory_kind in {"reflection_note", "event_affect"}:
        return 0.60
    if mode == "explicit_about_time" and memory_kind in {"summary", "episodic_event"}:
        return 0.30
    if mode == "associative_recent" and memory_kind in {"summary", "episodic_event"}:
        return 0.25
    return 0.0


def _payload_contains_text_hint(
    *,
    payload: dict[str, Any],
    text_hint: str,
) -> bool:
    return any(text_hint in text_part for text_part in _payload_text_values(payload))


def _payload_text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        text_values: list[str] = []
        for nested_value in value.values():
            text_values.extend(_payload_text_values(nested_value))
        return text_values
    if isinstance(value, list):
        text_values = []
        for nested_value in value:
            text_values.extend(_payload_text_values(nested_value))
        return text_values
    return []


def _append_reason(reason_codes: list[str], reason_code: str) -> None:
    if reason_code not in reason_codes:
        reason_codes.append(reason_code)


def _memory_item_ref(memory_entry: dict[str, Any]) -> str:
    memory_kind = str(memory_entry["memory_kind"])
    memory_state_id = str(memory_entry["memory_state_id"])
    if memory_kind == "episodic_event":
        return f"event:{memory_state_id}"
    if memory_kind == "event_affect":
        return f"event_affect:{memory_state_id}"
    if memory_kind == "preference":
        return f"preference:{memory_state_id}"
    return f"memory_state:{memory_state_id}"


# Block: Observation hints
def _observation_text_hints(current_observation: dict[str, Any]) -> list[str]:
    observation_text = current_observation["observation_text"]
    if not isinstance(observation_text, str) or not observation_text:
        raise ValueError("current_observation.observation_text must be non-empty string")
    hints: list[str] = [observation_text]
    for token in _text_hint_tokens(observation_text):
        if token not in hints:
            hints.append(token)
    query_hint = _observation_query_hint(current_observation)
    if query_hint is not None and query_hint not in hints:
        hints.append(query_hint)
    return hints


def _observation_query_hint(current_observation: dict[str, Any]) -> str | None:
    if current_observation["input_kind"] != "network_result":
        return None
    query = current_observation.get("query")
    if not isinstance(query, str) or not query:
        raise ValueError("network_result query must be non-empty string")
    return query


def _text_hint_tokens(text: str) -> list[str]:
    normalized_text = text
    for separator in (
        "　",
        "\n",
        "\t",
        ",",
        "、",
        ".",
        "。",
        "!",
        "！",
        "?",
        "？",
        ":",
        "：",
        ";",
        "；",
        "(",
        ")",
        "（",
        "）",
        "[",
        "]",
        "「",
        "」",
        "『",
        "』",
        "/",
        "／",
    ):
        normalized_text = normalized_text.replace(separator, " ")
    tokens: list[str] = []
    for raw_token in normalized_text.split(" "):
        token = raw_token.strip()
        if len(token) < 2:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


# Block: Cognition formatting
def _memory_entry_for_cognition(
    memory_entry: dict[str, Any],
    *,
    resolved_at: int,
) -> dict[str, Any]:
    updated_at = int(memory_entry["updated_at"])
    created_at = int(memory_entry["created_at"])
    last_confirmed_at = int(memory_entry["last_confirmed_at"])
    return {
        **memory_entry,
        "created_at_utc_text": _utc_text(created_at),
        "created_at_local_text": _local_text(created_at),
        "updated_at_utc_text": _utc_text(updated_at),
        "updated_at_local_text": _local_text(updated_at),
        "last_confirmed_at_utc_text": _utc_text(last_confirmed_at),
        "last_confirmed_at_local_text": _local_text(last_confirmed_at),
        "relative_time_text": _relative_time_text(resolved_at, updated_at),
    }


def _recent_event_for_cognition(
    event_entry: dict[str, Any],
    *,
    resolved_at: int,
) -> dict[str, Any]:
    created_at = int(event_entry["created_at"])
    return {
        **event_entry,
        "created_at_utc_text": _utc_text(created_at),
        "created_at_local_text": _local_text(created_at),
        "relative_time_text": _relative_time_text(resolved_at, created_at),
    }


# Block: Time helpers
def _utc_text(unix_ms: int) -> str:
    return datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _local_text(unix_ms: int) -> str:
    local_dt = datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc).astimezone()
    timezone_name = local_dt.tzname() or "UTC"
    return local_dt.strftime(f"%Y-%m-%d %H:%M:%S {timezone_name}")


def _relative_time_text(now_ms: int, past_ms: int) -> str:
    delta_seconds = max(0, (now_ms - past_ms) // 1000)
    if delta_seconds < 60:
        return f"{delta_seconds}秒前"
    delta_minutes = delta_seconds // 60
    if delta_minutes < 60:
        return f"{delta_minutes}分前"
    delta_hours = delta_minutes // 60
    if delta_hours < 24:
        return f"{delta_hours}時間前"
    delta_days = delta_hours // 24
    return f"{delta_days}日前"
