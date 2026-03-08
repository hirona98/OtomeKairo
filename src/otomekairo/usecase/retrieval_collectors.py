"""Collect retrieval candidates from the current memory snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from otomekairo.usecase.retrieval_common import (
    append_reason,
    item_ref_for_slot,
    observation_query_hint,
    observation_text_hints,
    payload_contains_text_hint,
    payload_text_values,
)


# Block: Collection result
@dataclass(frozen=True, slots=True)
class CandidateCollection:
    candidates: list[dict[str, Any]]
    collector_runs: list[dict[str, Any]]


# Block: Public collector
def collect_retrieval_candidates(
    *,
    memory_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
) -> CandidateCollection:
    candidates: list[dict[str, Any]] = []
    collector_runs: list[dict[str, Any]] = []
    for collector_name in retrieval_plan["collector_names"]:
        collector = _collector_for_name(collector_name)
        raw_candidates = collector(
            memory_snapshot=memory_snapshot,
            current_observation=current_observation,
            retrieval_plan=retrieval_plan,
        )
        limited_candidates = _limit_candidates(
            candidates=raw_candidates,
            limit=_collector_limit(
                collector_name=collector_name,
                retrieval_plan=retrieval_plan,
            ),
        )
        candidates.extend(limited_candidates)
        collector_runs.append(
            {
                "collector": collector_name,
                "candidate_count": len(limited_candidates),
                "truncated_count": max(0, len(raw_candidates) - len(limited_candidates)),
                "slot_counts": _slot_counts(limited_candidates),
            }
        )
    return CandidateCollection(
        candidates=candidates,
        collector_runs=collector_runs,
    )


# Block: Collector registry
def _collector_for_name(
    collector_name: str,
) -> Callable[..., list[dict[str, Any]]]:
    collectors: dict[str, Callable[..., list[dict[str, Any]]]] = {
        "recent_event_window": _collect_recent_event_window_candidates,
        "associative_memory": _collect_associative_memory_candidates,
        "episodic_memory": _collect_episodic_memory_candidates,
        "reply_chain": _collect_reply_chain_candidates,
        "context_threads": _collect_context_thread_candidates,
        "state_link_expand": _collect_state_link_candidates,
        "entity_expand": _collect_entity_expand_candidates,
        "relationship_focus": _collect_relationship_focus_candidates,
        "task_focus": _collect_task_focus_candidates,
        "reflection_focus": _collect_reflection_focus_candidates,
        "explicit_time": _collect_explicit_time_candidates,
    }
    try:
        return collectors[collector_name]
    except KeyError as exc:
        raise ValueError(f"unsupported retrieval collector: {collector_name}") from exc


# Block: Collector implementations
def _collect_recent_event_window_candidates(
    *,
    memory_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for event_entry in memory_snapshot["recent_event_window"]:
        score, reason_codes = _event_relevance_score(
            event_entry=event_entry,
            current_observation=current_observation,
            retrieval_plan=retrieval_plan,
        )
        score += 0.55
        append_reason(reason_codes, "collector_recent")
        collected.append(
            _candidate_entry(
                collector="recent_event_window",
                slot_name="recent_event_window",
                item=event_entry,
                score=score,
                reason_codes=reason_codes,
                sort_timestamp=int(event_entry["created_at"]),
            )
        )
    return collected


def _collect_associative_memory_candidates(
    *,
    memory_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for slot_name in ("working_memory_items", "semantic_items", "relationship_items", "affective_items"):
        for memory_entry in memory_snapshot[slot_name]:
            score, reason_codes = _memory_relevance_score(
                memory_entry=memory_entry,
                current_observation=current_observation,
                retrieval_plan=retrieval_plan,
            )
            if score <= 0.0:
                continue
            score += 0.20
            append_reason(reason_codes, "collector_associative")
            collected.append(
                _candidate_entry(
                    collector="associative_memory",
                    slot_name=slot_name,
                    item=memory_entry,
                    score=score,
                    reason_codes=reason_codes,
                    sort_timestamp=int(memory_entry["updated_at"]),
                )
            )
    return collected


def _collect_episodic_memory_candidates(
    *,
    memory_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for memory_entry in memory_snapshot["episodic_items"]:
        score, reason_codes = _memory_relevance_score(
            memory_entry=memory_entry,
            current_observation=current_observation,
            retrieval_plan=retrieval_plan,
        )
        if score <= 0.0:
            continue
        score += 0.35
        append_reason(reason_codes, "collector_episodic")
        collected.append(
            _candidate_entry(
                collector="episodic_memory",
                slot_name="episodic_items",
                item=memory_entry,
                score=score,
                reason_codes=reason_codes,
                sort_timestamp=int(memory_entry["updated_at"]),
            )
        )
    return collected


def _collect_reply_chain_candidates(
    *,
    memory_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    event_items = _event_items_by_id(memory_snapshot=memory_snapshot)
    anchor_event_ids = _matched_event_anchor_ids(
        event_items=event_items,
        current_observation=current_observation,
        retrieval_plan=retrieval_plan,
    )
    if not anchor_event_ids:
        return []
    collected: list[dict[str, Any]] = []
    for event_link in memory_snapshot.get("event_links", []):
        if not isinstance(event_link, dict):
            raise ValueError("memory_snapshot.event_links must contain only objects")
        from_event_id = str(event_link["from_event_id"])
        to_event_id = str(event_link["to_event_id"])
        if from_event_id in anchor_event_ids:
            candidate_event_id = to_event_id
        elif to_event_id in anchor_event_ids:
            candidate_event_id = from_event_id
        else:
            continue
        candidate_info = event_items.get(candidate_event_id)
        if candidate_info is None:
            continue
        slot_name, item = candidate_info
        if slot_name == "recent_event_window":
            score, reason_codes = _event_relevance_score(
                event_entry=item,
                current_observation=current_observation,
                retrieval_plan=retrieval_plan,
            )
            sort_timestamp = int(item["created_at"])
        else:
            score, reason_codes = _memory_relevance_score(
                memory_entry=item,
                current_observation=current_observation,
                retrieval_plan=retrieval_plan,
            )
            sort_timestamp = int(item["updated_at"])
        score += 0.85 + float(event_link["confidence"]) * 0.35
        append_reason(reason_codes, "collector_reply_chain")
        append_reason(reason_codes, f"event_link:{event_link['label']}")
        collected.append(
            _candidate_entry(
                collector="reply_chain",
                slot_name=slot_name,
                item=item,
                score=score,
                reason_codes=reason_codes,
                sort_timestamp=sort_timestamp,
            )
        )
    return collected


def _collect_context_thread_candidates(
    *,
    memory_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    event_items = _event_items_by_id(memory_snapshot=memory_snapshot)
    anchor_event_ids = _matched_event_anchor_ids(
        event_items=event_items,
        current_observation=current_observation,
        retrieval_plan=retrieval_plan,
    )
    if not anchor_event_ids:
        return []
    thread_keys = {
        str(event_thread["thread_key"])
        for event_thread in memory_snapshot.get("event_threads", [])
        if isinstance(event_thread, dict)
        and str(event_thread["event_id"]) in anchor_event_ids
    }
    if not thread_keys:
        return []
    collected: list[dict[str, Any]] = []
    for event_thread in memory_snapshot.get("event_threads", []):
        if not isinstance(event_thread, dict):
            raise ValueError("memory_snapshot.event_threads must contain only objects")
        if str(event_thread["thread_key"]) not in thread_keys:
            continue
        event_id = str(event_thread["event_id"])
        if event_id in anchor_event_ids:
            continue
        candidate_info = event_items.get(event_id)
        if candidate_info is None:
            continue
        slot_name, item = candidate_info
        if slot_name == "recent_event_window":
            score, reason_codes = _event_relevance_score(
                event_entry=item,
                current_observation=current_observation,
                retrieval_plan=retrieval_plan,
            )
            sort_timestamp = int(item["created_at"])
        else:
            score, reason_codes = _memory_relevance_score(
                memory_entry=item,
                current_observation=current_observation,
                retrieval_plan=retrieval_plan,
            )
            sort_timestamp = int(item["updated_at"])
        score += 0.70 + float(event_thread["confidence"]) * 0.30
        append_reason(reason_codes, "collector_context_threads")
        append_reason(reason_codes, f"thread_role:{event_thread.get('thread_role') or 'unknown'}")
        collected.append(
            _candidate_entry(
                collector="context_threads",
                slot_name=slot_name,
                item=item,
                score=score,
                reason_codes=reason_codes,
                sort_timestamp=sort_timestamp,
            )
        )
    return collected


def _collect_state_link_candidates(
    *,
    memory_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    state_items = _state_items_by_id(memory_snapshot=memory_snapshot)
    anchor_state_ids = _matched_state_anchor_ids(
        state_items=state_items,
        current_observation=current_observation,
        retrieval_plan=retrieval_plan,
    )
    if not anchor_state_ids:
        return []
    collected: list[dict[str, Any]] = []
    for state_link in memory_snapshot.get("state_links", []):
        if not isinstance(state_link, dict):
            raise ValueError("memory_snapshot.state_links must contain only objects")
        from_state_id = str(state_link["from_state_id"])
        to_state_id = str(state_link["to_state_id"])
        if from_state_id in anchor_state_ids:
            candidate_state_id = to_state_id
        elif to_state_id in anchor_state_ids:
            candidate_state_id = from_state_id
        else:
            continue
        candidate_info = state_items.get(candidate_state_id)
        if candidate_info is None:
            continue
        slot_name, item = candidate_info
        score, reason_codes = _memory_relevance_score(
            memory_entry=item,
            current_observation=current_observation,
            retrieval_plan=retrieval_plan,
        )
        score += 0.78 + float(state_link["confidence"]) * 0.32
        append_reason(reason_codes, "collector_state_link_expand")
        append_reason(reason_codes, f"state_link:{state_link['label']}")
        collected.append(
            _candidate_entry(
                collector="state_link_expand",
                slot_name=slot_name,
                item=item,
                score=score,
                reason_codes=reason_codes,
                sort_timestamp=int(item["updated_at"]),
            )
        )
    return collected


def _collect_entity_expand_candidates(
    *,
    memory_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    event_items = _event_items_by_id(memory_snapshot=memory_snapshot)
    state_items = _state_items_by_id(memory_snapshot=memory_snapshot)
    normalized_hints = _normalized_observation_hints(current_observation=current_observation)
    if not normalized_hints:
        return []
    collected: list[dict[str, Any]] = []
    for event_entity in memory_snapshot.get("event_entities", []):
        if not isinstance(event_entity, dict):
            raise ValueError("memory_snapshot.event_entities must contain only objects")
        entity_name_norm = str(event_entity["entity_name_norm"])
        if not any(
            hint == entity_name_norm or hint in entity_name_norm or entity_name_norm in hint
            for hint in normalized_hints
        ):
            continue
        candidate_info = event_items.get(str(event_entity["event_id"]))
        if candidate_info is None:
            continue
        slot_name, item = candidate_info
        if slot_name == "recent_event_window":
            score, reason_codes = _event_relevance_score(
                event_entry=item,
                current_observation=current_observation,
                retrieval_plan=retrieval_plan,
            )
            sort_timestamp = int(item["created_at"])
        else:
            score, reason_codes = _memory_relevance_score(
                memory_entry=item,
                current_observation=current_observation,
                retrieval_plan=retrieval_plan,
            )
            sort_timestamp = int(item["updated_at"])
        score += 0.74 + float(event_entity["confidence"]) * 0.28
        append_reason(reason_codes, "collector_entity_expand")
        append_reason(reason_codes, f"entity:{event_entity['entity_type_norm']}")
        collected.append(
            _candidate_entry(
                collector="entity_expand",
                slot_name=slot_name,
                item=item,
                score=score,
                reason_codes=reason_codes,
                sort_timestamp=sort_timestamp,
            )
        )
    for state_entity in memory_snapshot.get("state_entities", []):
        if not isinstance(state_entity, dict):
            raise ValueError("memory_snapshot.state_entities must contain only objects")
        entity_name_norm = str(state_entity["entity_name_norm"])
        if not any(
            hint == entity_name_norm or hint in entity_name_norm or entity_name_norm in hint
            for hint in normalized_hints
        ):
            continue
        candidate_info = state_items.get(str(state_entity["memory_state_id"]))
        if candidate_info is None:
            continue
        slot_name, item = candidate_info
        score, reason_codes = _memory_relevance_score(
            memory_entry=item,
            current_observation=current_observation,
            retrieval_plan=retrieval_plan,
        )
        score += 0.68 + float(state_entity["confidence"]) * 0.30
        append_reason(reason_codes, "collector_entity_expand")
        append_reason(reason_codes, f"entity:{state_entity['entity_type_norm']}")
        collected.append(
            _candidate_entry(
                collector="entity_expand",
                slot_name=slot_name,
                item=item,
                score=score,
                reason_codes=reason_codes,
                sort_timestamp=int(item["updated_at"]),
            )
        )
    return collected


def _collect_relationship_focus_candidates(
    *,
    memory_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    if current_observation["input_kind"] not in {"chat_message", "microphone_message"}:
        return []
    collected: list[dict[str, Any]] = []
    for slot_name in ("relationship_items", "affective_items", "working_memory_items"):
        for memory_entry in memory_snapshot[slot_name]:
            score, reason_codes = _memory_relevance_score(
                memory_entry=memory_entry,
                current_observation=current_observation,
                retrieval_plan=retrieval_plan,
            )
            if score <= 0.0:
                continue
            score += 0.40
            append_reason(reason_codes, "collector_relationship")
            collected.append(
                _candidate_entry(
                    collector="relationship_focus",
                    slot_name=slot_name,
                    item=memory_entry,
                    score=score,
                    reason_codes=reason_codes,
                    sort_timestamp=int(memory_entry["updated_at"]),
                )
            )
    return collected


def _collect_task_focus_candidates(
    *,
    memory_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    query_hint = observation_query_hint(current_observation)
    source_task_id = retrieval_plan["focus_refs"].get("source_task_id")
    if query_hint is None and source_task_id is None and retrieval_plan["mode"] != "task_targeted":
        return []
    collected: list[dict[str, Any]] = []
    for slot_name in ("working_memory_items", "semantic_items", "relationship_items", "reflection_items"):
        for memory_entry in memory_snapshot[slot_name]:
            score, reason_codes = _memory_relevance_score(
                memory_entry=memory_entry,
                current_observation=current_observation,
                retrieval_plan=retrieval_plan,
            )
            if score <= 0.0:
                continue
            score += 0.75
            append_reason(reason_codes, "collector_task_focus")
            collected.append(
                _candidate_entry(
                    collector="task_focus",
                    slot_name=slot_name,
                    item=memory_entry,
                    score=score,
                    reason_codes=reason_codes,
                    sort_timestamp=int(memory_entry["updated_at"]),
                )
            )
    return collected


def _collect_reflection_focus_candidates(
    *,
    memory_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    if retrieval_plan["mode"] != "reflection_recall":
        return []
    collected: list[dict[str, Any]] = []
    for slot_name in ("reflection_items", "affective_items", "episodic_items"):
        for memory_entry in memory_snapshot[slot_name]:
            score, reason_codes = _memory_relevance_score(
                memory_entry=memory_entry,
                current_observation=current_observation,
                retrieval_plan=retrieval_plan,
            )
            if score <= 0.0:
                continue
            score += 0.80
            append_reason(reason_codes, "collector_reflection")
            collected.append(
                _candidate_entry(
                    collector="reflection_focus",
                    slot_name=slot_name,
                    item=memory_entry,
                    score=score,
                    reason_codes=reason_codes,
                    sort_timestamp=int(memory_entry["updated_at"]),
                )
            )
    return collected


def _collect_explicit_time_candidates(
    *,
    memory_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    explicit_years = retrieval_plan["time_hint"]["explicit_years"]
    life_stage_hints = retrieval_plan["time_hint"].get("life_stage_hints", [])
    if not explicit_years and not life_stage_hints:
        return []
    explicit_year_texts = {str(year) for year in explicit_years}
    life_stage_hint_set = {
        str(life_stage_hint)
        for life_stage_hint in life_stage_hints
        if isinstance(life_stage_hint, str) and life_stage_hint
    }
    collected: list[dict[str, Any]] = []
    event_items = _event_items_by_id(memory_snapshot=memory_snapshot)
    state_items = _state_items_by_id(memory_snapshot=memory_snapshot)
    for slot_name in ("episodic_items", "semantic_items", "working_memory_items"):
        for memory_entry in memory_snapshot[slot_name]:
            score, reason_codes = _memory_relevance_score(
                memory_entry=memory_entry,
                current_observation=current_observation,
                retrieval_plan=retrieval_plan,
            )
            if "matched_explicit_year" not in reason_codes:
                continue
            score += 0.90
            append_reason(reason_codes, "collector_explicit_time")
            collected.append(
                _candidate_entry(
                    collector="explicit_time",
                    slot_name=slot_name,
                    item=memory_entry,
                    score=score,
                    reason_codes=reason_codes,
                    sort_timestamp=int(memory_entry["updated_at"]),
                )
            )
    for event_entry in memory_snapshot["recent_event_window"]:
        score, reason_codes = _event_relevance_score(
            event_entry=event_entry,
            current_observation=current_observation,
            retrieval_plan=retrieval_plan,
        )
        if "matched_explicit_year" not in reason_codes:
            continue
        score += 0.90
        append_reason(reason_codes, "collector_explicit_time")
        collected.append(
            _candidate_entry(
                collector="explicit_time",
                slot_name="recent_event_window",
                item=event_entry,
                score=score,
                reason_codes=reason_codes,
                sort_timestamp=int(event_entry["created_at"]),
            )
        )
    for state_entity in memory_snapshot.get("state_entities", []):
        if not isinstance(state_entity, dict):
            raise ValueError("memory_snapshot.state_entities must contain only objects")
        entity_type_norm = str(state_entity["entity_type_norm"])
        if entity_type_norm != "about_year":
            if entity_type_norm != "life_stage":
                continue
            if str(state_entity["entity_name_raw"]) not in life_stage_hint_set:
                continue
        elif str(state_entity["entity_name_raw"]) not in explicit_year_texts:
            continue
        candidate_info = state_items.get(str(state_entity["memory_state_id"]))
        if candidate_info is None:
            continue
        slot_name, item = candidate_info
        score, reason_codes = _memory_relevance_score(
            memory_entry=item,
            current_observation=current_observation,
            retrieval_plan=retrieval_plan,
        )
        append_reason(reason_codes, "collector_explicit_time")
        if entity_type_norm == "life_stage":
            append_reason(reason_codes, "matched_life_stage")
        else:
            append_reason(reason_codes, "matched_explicit_year")
        collected.append(
            _candidate_entry(
                collector="explicit_time",
                slot_name=slot_name,
                item=item,
                score=score + 1.05,
                reason_codes=reason_codes,
                sort_timestamp=int(item["updated_at"]),
            )
        )
    for event_about_time in memory_snapshot.get("event_about_time", []):
        if not isinstance(event_about_time, dict):
            raise ValueError("memory_snapshot.event_about_time must contain only objects")
        about_year_start = event_about_time.get("about_year_start")
        about_year_end = event_about_time.get("about_year_end")
        life_stage = event_about_time.get("life_stage")
        if isinstance(life_stage, str) and life_stage:
            if life_stage not in life_stage_hint_set:
                continue
            matched_reason_code = "matched_life_stage"
        else:
            matched_year_values = {
                str(about_year)
                for about_year in (about_year_start, about_year_end)
                if isinstance(about_year, int)
            }
            if not matched_year_values.intersection(explicit_year_texts):
                continue
            matched_reason_code = "matched_explicit_year"
        candidate_info = event_items.get(str(event_about_time["event_id"]))
        if candidate_info is None:
            continue
        slot_name, item = candidate_info
        if slot_name == "recent_event_window":
            score, reason_codes = _event_relevance_score(
                event_entry=item,
                current_observation=current_observation,
                retrieval_plan=retrieval_plan,
            )
            sort_timestamp = int(item["created_at"])
        else:
            score, reason_codes = _memory_relevance_score(
                memory_entry=item,
                current_observation=current_observation,
                retrieval_plan=retrieval_plan,
            )
            sort_timestamp = int(item["updated_at"])
        append_reason(reason_codes, "collector_explicit_time")
        append_reason(reason_codes, matched_reason_code)
        collected.append(
            _candidate_entry(
                collector="explicit_time",
                slot_name=slot_name,
                item=item,
                score=score + 1.05,
                reason_codes=reason_codes,
                sort_timestamp=sort_timestamp,
            )
        )
    return collected


# Block: Candidate sort helpers
def _limit_candidates(
    *,
    candidates: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("retrieval collector limit must be positive")
    sorted_candidates = sorted(
        candidates,
        key=lambda candidate: (
            float(candidate["score"]),
            int(candidate["sort_timestamp"]),
        ),
        reverse=True,
    )
    return sorted_candidates[:limit]


def _collector_limit(
    *,
    collector_name: str,
    retrieval_plan: dict[str, Any],
) -> int:
    limits = retrieval_plan["limits"]
    if collector_name == "recent_event_window":
        return max(
            int(limits["recent_event_window"]),
            min(6, int(limits["semantic_candidate_top_k"])),
        )
    return int(limits["semantic_candidate_top_k"])


def _slot_counts(candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        slot_name = str(candidate["slot"])
        counts[slot_name] = counts.get(slot_name, 0) + 1
    return counts


# Block: Candidate entry helpers
def _candidate_entry(
    *,
    collector: str,
    slot_name: str,
    item: dict[str, Any],
    score: float,
    reason_codes: list[str],
    sort_timestamp: int,
) -> dict[str, Any]:
    return {
        "collector": collector,
        "slot": slot_name,
        "item_ref": item_ref_for_slot(slot_name=slot_name, item=item),
        "score": round(score, 3),
        "reason_codes": reason_codes,
        "sort_timestamp": sort_timestamp,
        "item": item,
    }


def _event_items_by_id(
    *,
    memory_snapshot: dict[str, Any],
) -> dict[str, tuple[str, dict[str, Any]]]:
    event_items: dict[str, tuple[str, dict[str, Any]]] = {}
    for event_entry in memory_snapshot["recent_event_window"]:
        event_items[str(event_entry["event_id"])] = ("recent_event_window", event_entry)
    for memory_entry in memory_snapshot["episodic_items"]:
        payload = memory_entry.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("episodic memory entry payload must be object")
        event_id = payload.get("event_id")
        if isinstance(event_id, str) and event_id:
            event_items[event_id] = ("episodic_items", memory_entry)
    return event_items


def _state_items_by_id(
    *,
    memory_snapshot: dict[str, Any],
) -> dict[str, tuple[str, dict[str, Any]]]:
    state_items: dict[str, tuple[str, dict[str, Any]]] = {}
    for slot_name in (
        "working_memory_items",
        "semantic_items",
        "affective_items",
        "relationship_items",
        "reflection_items",
    ):
        for memory_entry in memory_snapshot[slot_name]:
            state_items[str(memory_entry["memory_state_id"])] = (slot_name, memory_entry)
    return state_items


def _matched_event_anchor_ids(
    *,
    event_items: dict[str, tuple[str, dict[str, Any]]],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
) -> set[str]:
    anchor_event_ids: set[str] = set()
    for event_id, (slot_name, item) in event_items.items():
        if slot_name == "recent_event_window":
            score, _ = _event_relevance_score(
                event_entry=item,
                current_observation=current_observation,
                retrieval_plan=retrieval_plan,
            )
        else:
            score, _ = _memory_relevance_score(
                memory_entry=item,
                current_observation=current_observation,
                retrieval_plan=retrieval_plan,
            )
        if score > 0.0:
            anchor_event_ids.add(event_id)
    return anchor_event_ids


def _matched_state_anchor_ids(
    *,
    state_items: dict[str, tuple[str, dict[str, Any]]],
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
) -> set[str]:
    anchor_state_ids: set[str] = set()
    for state_id, (_, item) in state_items.items():
        score, _ = _memory_relevance_score(
            memory_entry=item,
            current_observation=current_observation,
            retrieval_plan=retrieval_plan,
        )
        if score > 0.0:
            anchor_state_ids.add(state_id)
    return anchor_state_ids


def _normalized_observation_hints(
    *,
    current_observation: dict[str, Any],
) -> list[str]:
    return [
        "".join(text_hint.strip().lower().split())
        for text_hint in observation_text_hints(current_observation)
        if text_hint.strip()
    ]


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
    for text_hint in observation_text_hints(current_observation):
        if text_hint in body_text:
            score += 1.0
            append_reason(reason_codes, "matched_observation_text")
        if payload_contains_text_hint(payload=payload, text_hint=text_hint):
            score += 0.55
            append_reason(reason_codes, "matched_payload_text")
    query_hint = observation_query_hint(current_observation)
    if query_hint is not None and payload.get("query") == query_hint:
        score += 1.5
        append_reason(reason_codes, "matched_query")
    source_task_id = retrieval_plan["focus_refs"].get("source_task_id")
    if isinstance(source_task_id, str) and source_task_id:
        if payload.get("source_task_id") == source_task_id:
            score += 2.0
            append_reason(reason_codes, "matched_source_task")
    explicit_year_bonus = _explicit_year_bonus(
        text_values=[body_text, *payload_text_values(payload)],
        explicit_years=retrieval_plan["time_hint"]["explicit_years"],
    )
    if explicit_year_bonus > 0.0:
        score += explicit_year_bonus
        append_reason(reason_codes, "matched_explicit_year")
    mode_bonus = _mode_bonus(
        retrieval_plan=retrieval_plan,
        memory_kind=str(memory_entry["memory_kind"]),
    )
    if mode_bonus > 0.0:
        score += mode_bonus
        append_reason(reason_codes, "mode_priority")
    profile_bias = _memory_profile_bias(
        memory_kind=str(memory_entry["memory_kind"]),
        retrieval_plan=retrieval_plan,
    )
    if profile_bias > 0.0:
        score += profile_bias
        append_reason(reason_codes, "profile_bias")
    importance = min(1.0, float(memory_entry["importance"]))
    memory_strength = min(1.0, float(memory_entry["memory_strength"]))
    if importance > 0.0:
        score += importance * 0.25
        append_reason(reason_codes, "importance_bias")
    if memory_strength > 0.0:
        score += memory_strength * 0.20
        append_reason(reason_codes, "memory_strength_bias")
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
    for text_hint in observation_text_hints(current_observation):
        if text_hint in summary_text:
            score += 1.0
            append_reason(reason_codes, "matched_observation_text")
    explicit_year_bonus = _explicit_year_bonus(
        text_values=[summary_text],
        explicit_years=retrieval_plan["time_hint"]["explicit_years"],
    )
    if explicit_year_bonus > 0.0:
        score += explicit_year_bonus
        append_reason(reason_codes, "matched_explicit_year")
    if current_observation["input_kind"] == "network_result" and event_entry["source"] == "network_result":
        score += 1.0
        append_reason(reason_codes, "same_input_kind")
    if retrieval_plan["mode"] == "associative_recent":
        score += 0.35
        append_reason(reason_codes, "mode_priority")
    profile_bias = _event_profile_bias(retrieval_plan=retrieval_plan)
    if profile_bias > 0.0:
        score += profile_bias
        append_reason(reason_codes, "profile_bias")
    return (score, reason_codes)


def _mode_bonus(
    *,
    retrieval_plan: dict[str, Any],
    memory_kind: str,
) -> float:
    mode = str(retrieval_plan["mode"])
    if mode == "task_targeted" and memory_kind in {"summary", "fact", "relation", "preference"}:
        return 0.45
    if mode == "reflection_recall" and memory_kind in {"reflection_note", "event_affect"}:
        return 0.70
    if mode == "explicit_about_time" and memory_kind in {"summary", "episodic_event", "fact"}:
        return 0.35
    if mode == "associative_recent" and memory_kind in {"summary", "episodic_event"}:
        return 0.20
    return 0.0


def _memory_profile_bias(
    *,
    memory_kind: str,
    retrieval_plan: dict[str, Any],
) -> float:
    profile = retrieval_plan["profile"]
    if memory_kind == "summary":
        return float(profile["summary_bias"]) * 0.35
    if memory_kind in {"fact", "relation", "preference"}:
        return float(profile["fact_bias"]) * 0.35
    if memory_kind == "episodic_event":
        return float(profile["event_bias"]) * 0.35
    return 0.0


def _event_profile_bias(*, retrieval_plan: dict[str, Any]) -> float:
    return float(retrieval_plan["profile"]["event_bias"]) * 0.35


def _explicit_year_bonus(
    *,
    text_values: list[str],
    explicit_years: list[int],
) -> float:
    if not explicit_years:
        return 0.0
    for year in explicit_years:
        year_text = str(year)
        if any(year_text in text_value for text_value in text_values):
            return 1.20
    return 0.0
