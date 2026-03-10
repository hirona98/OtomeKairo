"""Public retrieval summary/detail serializers."""

from __future__ import annotations

from typing import Any


# Block: Public retrieval summary
def build_public_retrieval_summary(retrieval_run: dict[str, Any]) -> dict[str, Any]:
    plan_json = retrieval_run["plan"]
    selected_json = retrieval_run["selected"]
    payload = {
        "cycle_id": str(retrieval_run["cycle_id"]),
        "created_at": int(retrieval_run["created_at"]),
        "mode": str(plan_json["mode"]),
        "queries": list(plan_json["queries"]),
        "selected_counts": dict(selected_json["selected_counts"]),
    }
    collector_names = plan_json.get("collector_names")
    if isinstance(collector_names, list):
        public_collector_names = [
            str(collector_name)
            for collector_name in collector_names
            if isinstance(collector_name, str) and collector_name
        ]
        if public_collector_names:
            payload["collector_names"] = public_collector_names
    for field_name in (
        "collector_counts",
        "selected_reason_counts",
        "slot_skipped_slot_counts",
        "reserve_slot_counts",
    ):
        public_counts = _public_positive_int_map(selected_json.get(field_name))
        if public_counts:
            payload[field_name] = public_counts
    selector_summary = _public_selector_summary(selected_json.get("selector_summary"))
    if selector_summary:
        payload["selector_summary"] = selector_summary
    return payload


# Block: Public retrieval detail
def build_public_retrieval_detail(retrieval_run: dict[str, Any]) -> dict[str, Any]:
    candidates_json = retrieval_run["candidates"]
    selected_json = retrieval_run["selected"]
    payload = build_public_retrieval_summary(retrieval_run)
    selector_input_trace_by_item_ref: dict[str, dict[str, Any]] = {}
    selector_input_trace = candidates_json.get("selector_input_trace")
    if isinstance(selector_input_trace, list):
        payload["selector_input_trace"] = _public_selector_input_trace(selector_input_trace)
        selector_input_trace_by_item_ref = _selector_input_trace_by_item_ref(
            payload["selector_input_trace"]
        )
    selection_trace = selected_json.get("selection_trace")
    if isinstance(selection_trace, list):
        payload["selection_trace"] = _public_trace(
            selection_trace,
            selector_input_trace_by_item_ref=selector_input_trace_by_item_ref,
            trace_name="selection_trace",
            require_selection_rank=True,
        )
    slot_skipped_trace = selected_json.get("slot_skipped_trace")
    if isinstance(slot_skipped_trace, list):
        payload["slot_skipped_trace"] = _public_trace(
            slot_skipped_trace,
            selector_input_trace_by_item_ref=selector_input_trace_by_item_ref,
            trace_name="slot_skipped_trace",
            require_selection_rank=True,
        )
    reserve_trace = selected_json.get("reserve_trace")
    if isinstance(reserve_trace, list):
        payload["reserve_trace"] = _public_trace(
            reserve_trace,
            selector_input_trace_by_item_ref=selector_input_trace_by_item_ref,
            trace_name="reserve_trace",
            require_selection_rank=False,
        )
    for field_name in (
        "slot_skipped_collector_counts",
        "slot_skipped_reason_counts",
        "reserve_collector_counts",
        "reserve_reason_counts",
    ):
        public_counts = _public_positive_int_map(selected_json.get(field_name))
        if public_counts:
            payload[field_name] = public_counts
    for field_name in (
        "selector_input_collector_counts",
        "selector_input_slot_counts",
        "selector_input_reason_counts",
    ):
        public_counts = _public_positive_int_map(candidates_json.get(field_name))
        if public_counts:
            payload[field_name] = public_counts
    trimmed_item_refs = selected_json.get("trimmed_item_refs")
    if isinstance(trimmed_item_refs, list):
        payload["trimmed_item_refs"] = [
            str(item_ref)
            for item_ref in trimmed_item_refs
            if isinstance(item_ref, str) and item_ref
        ]
    resolved_event_ids = retrieval_run.get("resolved_event_ids")
    if isinstance(resolved_event_ids, list):
        payload["resolved_event_ids"] = [
            str(event_id)
            for event_id in resolved_event_ids
            if isinstance(event_id, str) and event_id
        ]
    return payload


# Block: Public positive int map
def _public_positive_int_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): int(entry_value)
        for key, entry_value in value.items()
        if isinstance(entry_value, int) and not isinstance(entry_value, bool) and entry_value > 0
    }


# Block: Public selector summary
def _public_selector_summary(value: Any) -> dict[str, int | str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): (
            int(entry_value)
            if isinstance(entry_value, int) and not isinstance(entry_value, bool)
            else str(entry_value)
        )
        for key, entry_value in value.items()
        if (
            (isinstance(entry_value, int) and not isinstance(entry_value, bool))
            or (isinstance(entry_value, str) and entry_value)
        )
    }


# Block: Selector input trace 公開整形
def _public_selector_input_trace(selector_input_trace: list[Any]) -> list[dict[str, Any]]:
    public_trace: list[dict[str, Any]] = []
    for index, trace_entry in enumerate(selector_input_trace):
        if not isinstance(trace_entry, dict):
            raise RuntimeError(f"selector_input_trace[{index}] must be object")
        item_ref = trace_entry.get("item_ref")
        slot_name = trace_entry.get("slot")
        score = trace_entry.get("score")
        collector_names = trace_entry.get("collector_names")
        reason_codes = trace_entry.get("reason_codes")
        text = trace_entry.get("text")
        relative_time_text = trace_entry.get("relative_time_text")
        if not isinstance(item_ref, str) or not item_ref:
            raise RuntimeError(f"selector_input_trace[{index}].item_ref must be non-empty string")
        if not isinstance(slot_name, str) or not slot_name:
            raise RuntimeError(f"selector_input_trace[{index}].slot must be non-empty string")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise RuntimeError(f"selector_input_trace[{index}].score must be number")
        if not isinstance(collector_names, list):
            raise RuntimeError(f"selector_input_trace[{index}].collector_names must be list")
        if not isinstance(reason_codes, list):
            raise RuntimeError(f"selector_input_trace[{index}].reason_codes must be list")
        if not isinstance(text, str) or not text:
            raise RuntimeError(f"selector_input_trace[{index}].text must be non-empty string")
        if not isinstance(relative_time_text, str) or not relative_time_text:
            raise RuntimeError(f"selector_input_trace[{index}].relative_time_text must be non-empty string")
        public_entry: dict[str, Any] = {
            "item_ref": item_ref,
            "slot": slot_name,
            "score": round(float(score), 3),
            "collector_names": [
                str(collector_name)
                for collector_name in collector_names
                if isinstance(collector_name, str) and collector_name
            ],
            "reason_codes": [
                str(reason_code)
                for reason_code in reason_codes
                if isinstance(reason_code, str) and reason_code
            ],
            "text": text,
            "relative_time_text": relative_time_text,
        }
        memory_kind = trace_entry.get("memory_kind")
        if isinstance(memory_kind, str) and memory_kind:
            public_entry["memory_kind"] = memory_kind
        about_time_hint_text = trace_entry.get("about_time_hint_text")
        if isinstance(about_time_hint_text, str) and about_time_hint_text:
            public_entry["about_time_hint_text"] = about_time_hint_text
        public_trace.append(public_entry)
    return public_trace


# Block: Trace 公開整形
def _public_trace(
    trace_entries: list[Any],
    *,
    selector_input_trace_by_item_ref: dict[str, dict[str, Any]],
    trace_name: str,
    require_selection_rank: bool,
) -> list[dict[str, Any]]:
    public_trace: list[dict[str, Any]] = []
    for index, trace_entry in enumerate(trace_entries):
        if not isinstance(trace_entry, dict):
            raise RuntimeError(f"{trace_name}[{index}] must be object")
        item_ref = trace_entry.get("item_ref")
        slot_name = trace_entry.get("slot")
        score = trace_entry.get("score")
        collector_names = trace_entry.get("collector_names")
        reason_codes = trace_entry.get("reason_codes")
        duplicate_hits = trace_entry.get("duplicate_hits")
        if not isinstance(item_ref, str) or not item_ref:
            raise RuntimeError(f"{trace_name}[{index}].item_ref must be non-empty string")
        if not isinstance(slot_name, str) or not slot_name:
            raise RuntimeError(f"{trace_name}[{index}].slot must be non-empty string")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise RuntimeError(f"{trace_name}[{index}].score must be number")
        if not isinstance(collector_names, list):
            raise RuntimeError(f"{trace_name}[{index}].collector_names must be list")
        if not isinstance(reason_codes, list):
            raise RuntimeError(f"{trace_name}[{index}].reason_codes must be list")
        if not isinstance(duplicate_hits, int) or isinstance(duplicate_hits, bool):
            raise RuntimeError(f"{trace_name}[{index}].duplicate_hits must be integer")
        public_entry: dict[str, Any] = {
            "item_ref": item_ref,
            "slot": slot_name,
            "score": round(float(score), 3),
            "collector_names": [
                str(collector_name)
                for collector_name in collector_names
                if isinstance(collector_name, str) and collector_name
            ],
            "reason_codes": [
                str(reason_code)
                for reason_code in reason_codes
                if isinstance(reason_code, str) and reason_code
            ],
            "duplicate_hits": duplicate_hits,
        }
        if require_selection_rank:
            selection_rank = trace_entry.get("selection_rank")
            if not isinstance(selection_rank, int) or isinstance(selection_rank, bool) or selection_rank <= 0:
                raise RuntimeError(f"{trace_name}[{index}].selection_rank must be positive integer")
            public_entry["selection_rank"] = selection_rank
        _merge_selector_input_context(
            public_entry=public_entry,
            selector_input_trace_by_item_ref=selector_input_trace_by_item_ref,
            item_ref=item_ref,
        )
        public_trace.append(public_entry)
    return public_trace


# Block: Selector input trace 索引
def _selector_input_trace_by_item_ref(
    selector_input_trace: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        str(trace_entry["item_ref"]): trace_entry
        for trace_entry in selector_input_trace
    }


# Block: Trace 文面情報の合成
def _merge_selector_input_context(
    *,
    public_entry: dict[str, Any],
    selector_input_trace_by_item_ref: dict[str, dict[str, Any]],
    item_ref: str,
) -> None:
    selector_input_trace = selector_input_trace_by_item_ref.get(item_ref)
    if selector_input_trace is None:
        return
    for field_name in ("memory_kind", "text", "relative_time_text", "about_time_hint_text"):
        field_value = selector_input_trace.get(field_name)
        if isinstance(field_value, str) and field_value:
            public_entry[field_name] = field_value
