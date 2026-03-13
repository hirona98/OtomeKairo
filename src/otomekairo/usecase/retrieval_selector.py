"""Select final memory bundle from merged retrieval candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from otomekairo.usecase.retrieval_trace_stats import collector_counts, reason_counts, slot_counts


# Block: Slot order
SLOT_ORDER = (
    "working_memory_items",
    "episodic_items",
    "semantic_items",
    "affective_items",
    "relationship_items",
    "preference_items",
    "reflection_items",
    "recent_event_window",
)

SLOT_PRIORITY = {
    "recent_event_window": 0,
    "working_memory_items": 1,
    "semantic_items": 2,
    "relationship_items": 3,
    "preference_items": 4,
    "reflection_items": 5,
    "affective_items": 6,
    "episodic_items": 7,
}

TRACE_PREVIEW_LIMIT = 8


# Block: Selection result
@dataclass(frozen=True, slots=True)
class SelectionArtifacts:
    memory_bundle: dict[str, Any]
    selected_json: dict[str, Any]


# Block: 候補 merge
def merge_retrieval_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
    return _sorted_merged_candidates(list(merged_by_ref.values()))


# Block: Empty selection artifacts
def empty_selection_artifacts(
    *,
    raw_candidate_count: int,
    selector_candidate_limit: int,
    selection_reason: str = "候補なし",
) -> SelectionArtifacts:
    memory_bundle = _empty_memory_bundle()
    return SelectionArtifacts(
        memory_bundle=memory_bundle,
        selected_json=_build_selected_json(
            memory_bundle=memory_bundle,
            raw_candidate_count=raw_candidate_count,
            merged_candidate_count=0,
            selector_input_candidate_count=0,
            selector_candidate_limit=selector_candidate_limit,
            llm_selected_ref_count=0,
            selection_reason=selection_reason,
            selected_trace=[],
            slot_skipped_trace=[],
            slot_skipped_all_trace=[],
            reserve_trace=[],
            reserve_all_trace=[],
        ),
    )


# Block: LLM 順序から最終選別
def select_retrieval_candidates(
    *,
    merged_candidates: list[dict[str, Any]],
    raw_candidate_count: int,
    selector_input_candidate_count: int,
    selector_candidate_limit: int,
    retrieval_plan: dict[str, Any],
    ordered_item_refs: list[str],
    selection_reason: str,
) -> SelectionArtifacts:
    selected_bundle = _empty_memory_bundle()
    selected_trace: list[dict[str, Any]] = []
    slot_skipped_trace: list[dict[str, Any]] = []
    slot_skipped_all_trace: list[dict[str, Any]] = []
    slot_limits = _slot_limits(retrieval_plan=retrieval_plan)
    candidate_by_ref = {
        str(candidate["item_ref"]): candidate
        for candidate in merged_candidates
    }
    used_refs: set[str] = set()
    for selection_rank, item_ref in enumerate(ordered_item_refs, start=1):
        candidate = candidate_by_ref.get(item_ref)
        if candidate is None:
            raise RuntimeError("retrieval selection returned unknown item_ref")
        slot_name = str(candidate["slot"])
        if len(selected_bundle[slot_name]) >= slot_limits[slot_name]:
            skipped_trace_entry = _trace_entry(
                candidate,
                selection_rank=selection_rank,
            )
            slot_skipped_all_trace.append(skipped_trace_entry)
            if len(slot_skipped_trace) < TRACE_PREVIEW_LIMIT:
                slot_skipped_trace.append(skipped_trace_entry)
            continue
        if item_ref in used_refs:
            raise RuntimeError("retrieval selection returned duplicate item_ref")
        selected_bundle[slot_name].append(candidate["item"])
        used_refs.add(item_ref)
        selected_trace.append(
            _trace_entry(
                candidate,
                selection_rank=selection_rank,
            )
        )
    if merged_candidates and not selected_trace:
        raise RuntimeError("retrieval selection produced no usable candidates")
    reserve_trace: list[dict[str, Any]] = []
    reserve_all_trace: list[dict[str, Any]] = []
    for merged_candidate in merged_candidates:
        if str(merged_candidate["item_ref"]) in used_refs:
            continue
        reserve_trace_entry = _trace_entry(merged_candidate)
        reserve_all_trace.append(reserve_trace_entry)
        if len(reserve_trace) < TRACE_PREVIEW_LIMIT:
            reserve_trace.append(reserve_trace_entry)
    return SelectionArtifacts(
        memory_bundle=selected_bundle,
        selected_json=_build_selected_json(
            memory_bundle=selected_bundle,
            raw_candidate_count=raw_candidate_count,
            merged_candidate_count=len(merged_candidates),
            selector_input_candidate_count=selector_input_candidate_count,
            selector_candidate_limit=selector_candidate_limit,
            llm_selected_ref_count=len(ordered_item_refs),
            selection_reason=selection_reason,
            selected_trace=selected_trace,
            slot_skipped_trace=slot_skipped_trace,
            slot_skipped_all_trace=slot_skipped_all_trace,
            reserve_trace=reserve_trace,
            reserve_all_trace=reserve_all_trace,
        ),
    )


# Block: ソート済み候補列
def _sorted_merged_candidates(merged_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        merged_candidates,
        key=lambda candidate: (
            float(candidate["score"]),
            -SLOT_PRIORITY.get(str(candidate["slot"]), 99),
            int(candidate["sort_timestamp"]),
        ),
        reverse=True,
    )


# Block: 優先 slot
def _preferred_slot(*, current_slot: str, new_slot: str) -> str:
    if SLOT_PRIORITY.get(new_slot, 99) < SLOT_PRIORITY.get(current_slot, 99):
        return new_slot
    return current_slot


# Block: Slot 上限
def _slot_limits(*, retrieval_plan: dict[str, Any]) -> dict[str, int]:
    limits = retrieval_plan["limits"]
    return {
        "working_memory_items": int(limits["working_memory_items"]),
        "episodic_items": int(limits["episodic_items"]),
        "semantic_items": int(limits["semantic_items"]),
        "affective_items": int(limits["affective_items"]),
        "relationship_items": int(limits["relationship_items"]),
        "preference_items": int(limits["preference_items"]),
        "reflection_items": int(limits["reflection_items"]),
        "recent_event_window": int(limits["recent_event_window"]),
    }


# Block: Empty memory bundle
def _empty_memory_bundle() -> dict[str, list[Any]]:
    return {slot_name: [] for slot_name in SLOT_ORDER}


# Block: Selected json builder
def _build_selected_json(
    *,
    memory_bundle: dict[str, Any],
    raw_candidate_count: int,
    merged_candidate_count: int,
    selector_input_candidate_count: int,
    selector_candidate_limit: int,
    llm_selected_ref_count: int,
    selection_reason: str,
    selected_trace: list[dict[str, Any]],
    slot_skipped_trace: list[dict[str, Any]],
    slot_skipped_all_trace: list[dict[str, Any]],
    reserve_trace: list[dict[str, Any]],
    reserve_all_trace: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "selected_counts": _selected_counts(memory_bundle=memory_bundle),
        "selected_refs": _selected_refs(memory_bundle=memory_bundle),
        "selection_trace": selected_trace,
        "slot_skipped_trace": slot_skipped_trace,
        "collector_counts": collector_counts(selected_trace),
        "selected_reason_counts": reason_counts(selected_trace),
        "slot_skipped_collector_counts": collector_counts(slot_skipped_all_trace),
        "slot_skipped_slot_counts": slot_counts(slot_skipped_all_trace),
        "slot_skipped_reason_counts": reason_counts(slot_skipped_all_trace),
        "reserve_collector_counts": collector_counts(reserve_all_trace),
        "reserve_slot_counts": slot_counts(reserve_all_trace),
        "reserve_reason_counts": reason_counts(reserve_all_trace),
        "selector_summary": _selector_summary(
            raw_candidate_count=raw_candidate_count,
            merged_candidate_count=merged_candidate_count,
            selector_input_candidate_count=selector_input_candidate_count,
            selector_candidate_limit=selector_candidate_limit,
            llm_selected_ref_count=llm_selected_ref_count,
            selected_candidate_count=len(selected_trace),
            reserve_candidate_count=len(reserve_all_trace),
            slot_skipped_count=len(slot_skipped_all_trace),
            selection_reason=selection_reason,
        ),
        "reserve_trace": reserve_trace,
    }


# Block: Selector summary
def _selector_summary(
    *,
    raw_candidate_count: int,
    merged_candidate_count: int,
    selector_input_candidate_count: int,
    selector_candidate_limit: int,
    llm_selected_ref_count: int,
    selected_candidate_count: int,
    reserve_candidate_count: int,
    slot_skipped_count: int,
    selection_reason: str,
) -> dict[str, int | str]:
    return {
        "selector_mode": "llm_ranked",
        "selection_reason": selection_reason,
        "raw_candidate_count": raw_candidate_count,
        "merged_candidate_count": merged_candidate_count,
        "selector_input_candidate_count": selector_input_candidate_count,
        "selector_candidate_limit": selector_candidate_limit,
        "llm_selected_ref_count": llm_selected_ref_count,
        "llm_unselected_count": max(
            0,
            selector_input_candidate_count - llm_selected_ref_count,
        ),
        "llm_return_ratio_percent": _ratio_percent(
            numerator=llm_selected_ref_count,
            denominator=selector_input_candidate_count,
        ),
        "selected_candidate_count": selected_candidate_count,
        "selector_input_unused_count": max(
            0,
            selector_input_candidate_count - selected_candidate_count,
        ),
        "selected_candidate_ratio_percent": _ratio_percent(
            numerator=selected_candidate_count,
            denominator=selector_input_candidate_count,
        ),
        "duplicate_hit_count": max(0, raw_candidate_count - merged_candidate_count),
        "reserve_candidate_count": reserve_candidate_count,
        "slot_skipped_count": slot_skipped_count,
    }


# Block: Trace 変換
def _trace_entry(
    candidate: dict[str, Any],
    *,
    selection_rank: int | None = None,
) -> dict[str, Any]:
    trace_entry = {
        "slot": str(candidate["slot"]),
        "item_ref": str(candidate["item_ref"]),
        "score": round(float(candidate["score"]), 3),
        "reason_codes": list(candidate["reason_codes"]),
        "collector_names": list(candidate["collector_names"]),
        "duplicate_hits": int(candidate["duplicate_hits"]),
    }
    if selection_rank is not None:
        trace_entry["selection_rank"] = selection_rank
    return trace_entry


# Block: 比率パーセント
def _ratio_percent(*, numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return 0
    return round((numerator / denominator) * 100)


# Block: 件数要約
def _selected_counts(*, memory_bundle: dict[str, Any]) -> dict[str, int]:
    return {
        "working_memory_items": len(memory_bundle["working_memory_items"]),
        "episodic_items": len(memory_bundle["episodic_items"]),
        "semantic_items": len(memory_bundle["semantic_items"]),
        "affective_items": len(memory_bundle["affective_items"]),
        "relationship_items": len(memory_bundle["relationship_items"]),
        "preference_items": len(memory_bundle["preference_items"]),
        "reflection_items": len(memory_bundle["reflection_items"]),
        "recent_event_window": len(memory_bundle["recent_event_window"]),
    }


# Block: 参照要約
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
        "preference_item_ids": [
            str(item["memory_state_id"])
            for item in memory_bundle["preference_items"]
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
