"""Build retrieval plan for the current cognition cycle."""

from __future__ import annotations

from typing import Any

from otomekairo.schema.settings import normalize_retrieval_profile
from otomekairo.usecase.retrieval_common import (
    append_unique_text,
    explicit_years_from_observation,
    observation_query_hint,
)


# Block: Retrieval selection limits
WORKING_MEMORY_LIMIT = 3
EPISODIC_LIMIT = 3
SEMANTIC_LIMIT = 3
AFFECTIVE_LIMIT = 2
RELATIONSHIP_LIMIT = 2
REFLECTION_LIMIT = 2


# Block: Public builder
def build_retrieval_plan(
    *,
    retrieval_profile: dict[str, Any],
    current_observation: dict[str, Any],
    task_snapshot: dict[str, Any],
) -> dict[str, Any]:
    normalized_retrieval_profile = normalize_retrieval_profile(retrieval_profile)
    explicit_years = explicit_years_from_observation(current_observation)
    mode = _retrieval_mode(
        current_observation=current_observation,
        explicit_years=explicit_years,
    )
    return {
        "mode": mode,
        "queries": _build_queries(
            current_observation=current_observation,
            task_snapshot=task_snapshot,
        ),
        "time_hint": {
            "explicit_years": explicit_years,
            "has_explicit_time_hint": bool(explicit_years),
        },
        "focus_refs": _build_focus_refs(
            current_observation=current_observation,
            task_snapshot=task_snapshot,
        ),
        "collector_names": _build_collector_names(
            mode=mode,
            current_observation=current_observation,
            explicit_years=explicit_years,
        ),
        "profile": dict(normalized_retrieval_profile),
        "limits": _build_selection_limits(
            retrieval_profile=normalized_retrieval_profile,
        ),
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
    append_unique_text(queries, observation_text)
    query_hint = observation_query_hint(current_observation)
    if query_hint is not None:
        append_unique_text(queries, query_hint)
    for task_entry in task_snapshot["active_tasks"][:2]:
        goal_hint = task_entry.get("goal_hint")
        if isinstance(goal_hint, str) and goal_hint:
            append_unique_text(queries, goal_hint)
    for task_entry in task_snapshot["waiting_external_tasks"][:1]:
        goal_hint = task_entry.get("goal_hint")
        if isinstance(goal_hint, str) and goal_hint:
            append_unique_text(queries, goal_hint)
    return queries


def _build_focus_refs(
    *,
    current_observation: dict[str, Any],
    task_snapshot: dict[str, Any],
) -> dict[str, Any]:
    source_task_id = current_observation.get("source_task_id")
    return {
        "source_task_id": (
            str(source_task_id)
            if isinstance(source_task_id, str) and source_task_id
            else None
        ),
        "query": observation_query_hint(current_observation),
        "active_task_ids": [
            str(task_entry["task_id"])
            for task_entry in task_snapshot["active_tasks"][:3]
            if isinstance(task_entry, dict) and isinstance(task_entry.get("task_id"), str)
        ],
        "active_goal_hints": [
            str(task_entry["goal_hint"])
            for task_entry in task_snapshot["active_tasks"][:3]
            if isinstance(task_entry, dict) and isinstance(task_entry.get("goal_hint"), str)
        ],
        "waiting_goal_hints": [
            str(task_entry["goal_hint"])
            for task_entry in task_snapshot["waiting_external_tasks"][:2]
            if isinstance(task_entry, dict) and isinstance(task_entry.get("goal_hint"), str)
        ],
    }


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


def _build_collector_names(
    *,
    mode: str,
    current_observation: dict[str, Any],
    explicit_years: list[int],
) -> list[str]:
    collector_names = [
        "recent_event_window",
        "associative_memory",
        "episodic_memory",
        "reply_chain",
        "context_threads",
        "state_link_expand",
    ]
    if current_observation["input_kind"] in {"chat_message", "microphone_message"}:
        collector_names.append("relationship_focus")
    if mode == "task_targeted":
        collector_names.append("task_focus")
    if mode == "reflection_recall":
        collector_names.append("reflection_focus")
    if explicit_years:
        collector_names.append("explicit_time")
    deduplicated: list[str] = []
    for collector_name in collector_names:
        if collector_name not in deduplicated:
            deduplicated.append(collector_name)
    return deduplicated


# Block: Limit helpers
def _build_selection_limits(*, retrieval_profile: dict[str, Any]) -> dict[str, int]:
    return {
        "working_memory_items": WORKING_MEMORY_LIMIT,
        "episodic_items": EPISODIC_LIMIT,
        "semantic_items": SEMANTIC_LIMIT,
        "affective_items": AFFECTIVE_LIMIT,
        "relationship_items": RELATIONSHIP_LIMIT,
        "reflection_items": REFLECTION_LIMIT,
        "recent_event_window": int(retrieval_profile["recent_window_limit"]),
        "semantic_candidate_top_k": int(retrieval_profile["semantic_top_k"]),
    }
