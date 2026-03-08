"""Build retrieval plan, candidate traces, and final memory bundle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from otomekairo.usecase.retrieval_collectors import collect_retrieval_candidates
from otomekairo.usecase.retrieval_common import local_text, relative_time_text, utc_text
from otomekairo.usecase.retrieval_plan import build_retrieval_plan
from otomekairo.usecase.retrieval_selector import select_retrieval_candidates


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
    retrieval_profile: dict[str, Any],
    current_observation: dict[str, Any],
    task_snapshot: dict[str, Any],
    resolved_at: int,
) -> RetrievalArtifacts:
    retrieval_plan = build_retrieval_plan(
        retrieval_profile=retrieval_profile,
        current_observation=current_observation,
        task_snapshot=task_snapshot,
    )
    candidate_collection = collect_retrieval_candidates(
        memory_snapshot=memory_snapshot,
        current_observation=current_observation,
        retrieval_plan=retrieval_plan,
    )
    selection_artifacts = select_retrieval_candidates(
        candidates=candidate_collection.candidates,
        retrieval_plan=retrieval_plan,
    )
    memory_bundle = {
        "working_memory_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in selection_artifacts.memory_bundle["working_memory_items"]
        ],
        "episodic_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in selection_artifacts.memory_bundle["episodic_items"]
        ],
        "semantic_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in selection_artifacts.memory_bundle["semantic_items"]
        ],
        "affective_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in selection_artifacts.memory_bundle["affective_items"]
        ],
        "relationship_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in selection_artifacts.memory_bundle["relationship_items"]
        ],
        "reflection_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in selection_artifacts.memory_bundle["reflection_items"]
        ],
        "recent_event_window": [
            _recent_event_for_cognition(event_entry, resolved_at=resolved_at)
            for event_entry in selection_artifacts.memory_bundle["recent_event_window"]
        ],
    }
    return RetrievalArtifacts(
        memory_bundle=memory_bundle,
        retrieval_plan=retrieval_plan,
        candidates_json=_build_candidates_json(
            candidates=candidate_collection.candidates,
            collector_runs=candidate_collection.collector_runs,
        ),
        selected_json=selection_artifacts.selected_json,
    )


# Block: Candidate builders
def _build_candidates_json(
    *,
    candidates: list[dict[str, Any]],
    collector_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    category_counts: dict[str, int] = {}
    unique_refs: set[str] = set()
    for candidate in candidates:
        slot_name = str(candidate["slot"])
        category_counts[slot_name] = category_counts.get(slot_name, 0) + 1
        unique_refs.add(str(candidate["item_ref"]))
    return {
        "total_candidate_count": len(candidates),
        "unique_candidate_count": len(unique_refs),
        "category_counts": category_counts,
        "non_empty_categories": [
            category_name
            for category_name, count in category_counts.items()
            if count > 0
        ],
        "collector_runs": collector_runs,
    }


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
    return utc_text(unix_ms)


def _local_text(unix_ms: int) -> str:
    return local_text(unix_ms)


def _relative_time_text(now_ms: int, past_ms: int) -> str:
    return relative_time_text(now_ms, past_ms)
