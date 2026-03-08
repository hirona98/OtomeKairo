"""Select final memory bundle from collected retrieval candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Block: Slot order
SLOT_ORDER = (
    "working_memory_items",
    "episodic_items",
    "semantic_items",
    "affective_items",
    "relationship_items",
    "reflection_items",
    "recent_event_window",
)

SLOT_PRIORITY = {
    "recent_event_window": 0,
    "working_memory_items": 1,
    "semantic_items": 2,
    "relationship_items": 3,
    "reflection_items": 4,
    "affective_items": 5,
    "episodic_items": 6,
}


# Block: Selection result
@dataclass(frozen=True, slots=True)
class SelectionArtifacts:
    memory_bundle: dict[str, Any]
    selected_json: dict[str, Any]


# Block: Public selector
def select_retrieval_candidates(
    *,
    candidates: list[dict[str, Any]],
    retrieval_plan: dict[str, Any],
) -> SelectionArtifacts:
    merged_candidates = _merge_candidates(candidates)
    selected_bundle = {slot_name: [] for slot_name in SLOT_ORDER}
    selected_trace: list[dict[str, Any]] = []
    reserve_trace: list[dict[str, Any]] = []
    slot_limits = _slot_limits(retrieval_plan=retrieval_plan)
    for merged_candidate in sorted(
        merged_candidates,
        key=lambda candidate: (
            float(candidate["score"]),
            -SLOT_PRIORITY.get(str(candidate["slot"]), 99),
            int(candidate["sort_timestamp"]),
        ),
        reverse=True,
    ):
        slot_name = str(merged_candidate["slot"])
        if len(selected_bundle[slot_name]) >= slot_limits[slot_name]:
            if len(reserve_trace) < 8:
                reserve_trace.append(_trace_entry(merged_candidate))
            continue
        selected_bundle[slot_name].append(merged_candidate["item"])
        selected_trace.append(_trace_entry(merged_candidate))
    return SelectionArtifacts(
        memory_bundle=selected_bundle,
        selected_json={
            "selected_counts": _selected_counts(memory_bundle=selected_bundle),
            "selected_refs": _selected_refs(memory_bundle=selected_bundle),
            "selection_trace": selected_trace,
            "collector_counts": _collector_counts(selected_trace),
            "selector_summary": {
                "raw_candidate_count": len(candidates),
                "merged_candidate_count": len(merged_candidates),
                "selected_candidate_count": len(selected_trace),
                "duplicate_hit_count": max(0, len(candidates) - len(merged_candidates)),
                "reserve_candidate_count": len(reserve_trace),
            },
            "reserve_trace": reserve_trace,
        },
    )


# Block: Merge helpers
def _merge_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged_by_ref: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        item_ref = str(candidate["item_ref"])
        existing = merged_by_ref.get(item_ref)
        if existing is None:
            merged_by_ref[item_ref] = {
                "slot": str(candidate["slot"]),
                "item_ref": item_ref,
                "item": candidate["item"],
                "score": float(candidate["score"]),
                "reason_codes": list(candidate["reason_codes"]),
                "collector_names": [str(candidate["collector"])],
                "sort_timestamp": int(candidate["sort_timestamp"]),
                "duplicate_hits": 0,
            }
            continue
        existing["score"] = round(float(existing["score"]) + float(candidate["score"]), 3)
        existing["sort_timestamp"] = max(
            int(existing["sort_timestamp"]),
            int(candidate["sort_timestamp"]),
        )
        existing["duplicate_hits"] = int(existing["duplicate_hits"]) + 1
        for reason_code in candidate["reason_codes"]:
            if reason_code not in existing["reason_codes"]:
                existing["reason_codes"].append(reason_code)
        collector_name = str(candidate["collector"])
        if collector_name not in existing["collector_names"]:
            existing["collector_names"].append(collector_name)
        preferred_slot = _preferred_slot(
            current_slot=str(existing["slot"]),
            new_slot=str(candidate["slot"]),
        )
        if preferred_slot != existing["slot"]:
            existing["slot"] = preferred_slot
            existing["item"] = candidate["item"]
    return list(merged_by_ref.values())


def _preferred_slot(*, current_slot: str, new_slot: str) -> str:
    if SLOT_PRIORITY.get(new_slot, 99) < SLOT_PRIORITY.get(current_slot, 99):
        return new_slot
    return current_slot


def _slot_limits(*, retrieval_plan: dict[str, Any]) -> dict[str, int]:
    limits = retrieval_plan["limits"]
    return {
        "working_memory_items": int(limits["working_memory_items"]),
        "episodic_items": int(limits["episodic_items"]),
        "semantic_items": int(limits["semantic_items"]),
        "affective_items": int(limits["affective_items"]),
        "relationship_items": int(limits["relationship_items"]),
        "reflection_items": int(limits["reflection_items"]),
        "recent_event_window": int(limits["recent_event_window"]),
    }


# Block: Trace builders
def _trace_entry(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "slot": str(candidate["slot"]),
        "item_ref": str(candidate["item_ref"]),
        "score": round(float(candidate["score"]), 3),
        "reason_codes": list(candidate["reason_codes"]),
        "collector_names": list(candidate["collector_names"]),
        "duplicate_hits": int(candidate["duplicate_hits"]),
    }


def _collector_counts(selection_trace: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for trace_entry in selection_trace:
        for collector_name in trace_entry["collector_names"]:
            collector_key = str(collector_name)
            counts[collector_key] = counts.get(collector_key, 0) + 1
    return counts


# Block: Selected json builders
def _selected_counts(*, memory_bundle: dict[str, Any]) -> dict[str, int]:
    return {
        "working_memory_items": len(memory_bundle["working_memory_items"]),
        "episodic_items": len(memory_bundle["episodic_items"]),
        "semantic_items": len(memory_bundle["semantic_items"]),
        "affective_items": len(memory_bundle["affective_items"]),
        "relationship_items": len(memory_bundle["relationship_items"]),
        "reflection_items": len(memory_bundle["reflection_items"]),
        "recent_event_window": len(memory_bundle["recent_event_window"]),
    }


def _selected_refs(*, memory_bundle: dict[str, Any]) -> dict[str, Any]:
    return {
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
    }
