"""Run structured retrieval selection before cognition planning."""

from __future__ import annotations

from typing import Any

from otomekairo.gateway.cognition_client import (
    CognitionClient,
    RetrievalSelectionRequest,
)


# Block: 想起選別実行
def run_retrieval_selection(
    *,
    cycle_id: str,
    current_observation: dict[str, Any],
    retrieval_plan: dict[str, Any],
    candidate_pack: dict[str, Any],
    completion_settings: dict[str, Any],
    cognition_client: CognitionClient,
) -> dict[str, Any]:
    request = RetrievalSelectionRequest(
        cycle_id=cycle_id,
        current_observation=current_observation,
        retrieval_plan=retrieval_plan,
        candidate_pack=candidate_pack,
        completion_settings=completion_settings,
    )
    retrieval_selection = cognition_client.select_retrieval_candidates(request).retrieval_selection
    return _validated_retrieval_selection(
        retrieval_selection=retrieval_selection,
        candidate_pack=candidate_pack,
    )


# Block: 想起選別バリデーション
def _validated_retrieval_selection(
    *,
    retrieval_selection: dict[str, Any],
    candidate_pack: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(retrieval_selection, dict):
        raise RuntimeError("retrieval_selection must be an object")
    selected_item_refs = retrieval_selection.get("selected_item_refs")
    selection_reason = retrieval_selection.get("selection_reason")
    if not isinstance(selected_item_refs, list):
        raise RuntimeError("retrieval_selection.selected_item_refs must be a list")
    if not isinstance(selection_reason, str) or not selection_reason.strip():
        raise RuntimeError("retrieval_selection.selection_reason must be a non-empty string")
    candidate_entries = candidate_pack.get("candidate_entries")
    if not isinstance(candidate_entries, list):
        raise RuntimeError("candidate_pack.candidate_entries must be a list")
    known_refs = {
        str(candidate_entry["item_ref"])
        for candidate_entry in candidate_entries
        if isinstance(candidate_entry, dict)
    }
    normalized_refs: list[str] = []
    seen_refs: set[str] = set()
    for selected_item_ref in selected_item_refs:
        if not isinstance(selected_item_ref, str) or not selected_item_ref:
            raise RuntimeError("retrieval_selection.selected_item_refs must contain non-empty strings")
        if selected_item_ref not in known_refs:
            raise RuntimeError("retrieval_selection.selected_item_refs must reference known candidate refs")
        if selected_item_ref in seen_refs:
            raise RuntimeError("retrieval_selection.selected_item_refs must not contain duplicates")
        seen_refs.add(selected_item_ref)
        normalized_refs.append(selected_item_ref)
    if candidate_entries and not normalized_refs:
        raise RuntimeError("retrieval_selection.selected_item_refs must not be empty when candidates exist")
    return {
        "selected_item_refs": normalized_refs,
        "selection_reason": selection_reason.strip(),
    }
