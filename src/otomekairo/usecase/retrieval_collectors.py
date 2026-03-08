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
    if not retrieval_plan["time_hint"]["explicit_years"]:
        return []
    collected: list[dict[str, Any]] = []
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
