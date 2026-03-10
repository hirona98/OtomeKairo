"""Build retrieval evaluation reports from retrieval_runs."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any
import unicodedata

from otomekairo.usecase.retrieval_public_view import build_public_retrieval_detail


# Block: Eval constants
REPORT_SCHEMA_VERSION = 3
THREAD_COLLECTORS = frozenset({"reply_chain", "context_threads"})
EXPLICIT_TIME_COLLECTORS = frozenset({"explicit_time"})
EXPLICIT_TIME_REASON_CODES = frozenset(
    {"matched_explicit_date", "matched_explicit_year", "matched_life_stage"}
)
MODE_ORDER = (
    "explicit_about_time",
    "reflection_recall",
    "task_targeted",
    "associative_recent",
)
TOP_COUNT_LIMIT = 8
TEXT_OVERLAP_MIN_LENGTH = 8
TEXT_CONTAIN_MIN_LENGTH = 12


# Block: Eval report builder
def build_retrieval_eval_report(retrieval_runs: list[dict[str, Any]]) -> dict[str, Any]:
    ordered_runs = sorted(
        retrieval_runs,
        key=lambda retrieval_run: int(retrieval_run["created_at"]),
        reverse=True,
    )
    report = _build_eval_slice(ordered_runs)
    mode_breakdown = _build_mode_breakdown(ordered_runs)
    report["report_schema_version"] = REPORT_SCHEMA_VERSION
    report["mode_names"] = list(mode_breakdown.keys())
    report["mode_breakdown"] = mode_breakdown
    return report


# Block: Eval report formatting
def format_retrieval_eval_report(report: dict[str, Any]) -> str:
    run_count = int(report.get("run_count", 0))
    if run_count == 0:
        return "retrieval eval: no retrieval runs"
    lines = _format_eval_slice(
        title="retrieval eval",
        slice_report=report,
    )
    mode_names = report.get("mode_names")
    mode_breakdown = report.get("mode_breakdown")
    if isinstance(mode_names, list) and isinstance(mode_breakdown, dict) and mode_names:
        lines.append("mode breakdown:")
        for mode_name in mode_names:
            if not isinstance(mode_name, str) or not mode_name:
                raise RuntimeError("report.mode_names must contain non-empty strings")
            mode_report = mode_breakdown.get(mode_name)
            if not isinstance(mode_report, dict):
                raise RuntimeError(f"report.mode_breakdown[{mode_name}] must be object")
            lines.extend(
                _format_eval_slice(
                    title=f"  {mode_name}",
                    slice_report=mode_report,
                )
            )
    return "\n".join(lines)


# Block: Eval slice builder
def _build_eval_slice(retrieval_runs: list[dict[str, Any]]) -> dict[str, Any]:
    if not retrieval_runs:
        return _empty_eval_slice()
    totals = _empty_eval_totals()
    created_at_values: list[int] = []
    for retrieval_run in retrieval_runs:
        created_at = int(retrieval_run["created_at"])
        created_at_values.append(created_at)
        _accumulate_eval_totals(
            totals=totals,
            retrieval_run=retrieval_run,
        )
    total_runs = len(retrieval_runs)
    return {
        "run_count": total_runs,
        "window": {
            "latest_created_at": created_at_values[0],
            "latest_created_at_utc_text": _utc_text(created_at_values[0]),
            "oldest_created_at": created_at_values[-1],
            "oldest_created_at_utc_text": _utc_text(created_at_values[-1]),
        },
        "overview": {
            "empty_run_count": totals["empty_run_count"],
            "empty_run_rate_percent": _ratio_percent(totals["empty_run_count"], total_runs),
            "avg_raw_candidate_count": _average(totals["sum_raw_candidate_count"], total_runs),
            "avg_merged_candidate_count": _average(
                totals["sum_merged_candidate_count"],
                total_runs,
            ),
            "avg_selector_input_candidate_count": _average(
                totals["sum_selector_input_candidate_count"],
                total_runs,
            ),
            "avg_selected_item_count": _average(totals["sum_selected_item_count"], total_runs),
        },
        "selector": {
            "avg_llm_selected_ref_count": _average(
                totals["sum_llm_selected_ref_count"],
                total_runs,
            ),
            "avg_selected_candidate_count": _average(
                totals["sum_selected_candidate_count"],
                total_runs,
            ),
            "avg_llm_return_ratio_percent": _average(
                totals["sum_llm_return_ratio_percent"],
                total_runs,
            ),
            "avg_selected_candidate_ratio_percent": _average(
                totals["sum_selected_candidate_ratio_percent"],
                total_runs,
            ),
            "avg_duplicate_hit_count": _average(
                totals["sum_duplicate_hit_count"],
                total_runs,
            ),
            "avg_slot_skipped_count": _average(
                totals["sum_slot_skipped_count"],
                total_runs,
            ),
            "avg_reserve_candidate_count": _average(
                totals["sum_reserve_candidate_count"],
                total_runs,
            ),
        },
        "coverage": {
            "explicit_time_selected_run_count": totals["explicit_time_selected_run_count"],
            "explicit_time_selected_run_rate_percent": _ratio_percent(
                totals["explicit_time_selected_run_count"],
                total_runs,
            ),
            "explicit_time_input_run_count": totals["explicit_time_input_run_count"],
            "explicit_time_input_run_rate_percent": _ratio_percent(
                totals["explicit_time_input_run_count"],
                total_runs,
            ),
            "thread_selected_run_count": totals["thread_selected_run_count"],
            "thread_selected_run_rate_percent": _ratio_percent(
                totals["thread_selected_run_count"],
                total_runs,
            ),
            "thread_input_run_count": totals["thread_input_run_count"],
            "thread_input_run_rate_percent": _ratio_percent(
                totals["thread_input_run_count"],
                total_runs,
            ),
            "relationship_selected_run_count": totals["relationship_selected_run_count"],
            "relationship_selected_run_rate_percent": _ratio_percent(
                totals["relationship_selected_run_count"],
                total_runs,
            ),
        },
        "preference": {
            "preference_input_run_count": totals["preference_input_run_count"],
            "preference_input_run_rate_percent": _ratio_percent(
                totals["preference_input_run_count"],
                total_runs,
            ),
            "preference_selected_run_count": totals["preference_selected_run_count"],
            "preference_selected_run_rate_percent": _ratio_percent(
                totals["preference_selected_run_count"],
                total_runs,
            ),
            "preference_carryover_rate_percent": _ratio_percent(
                totals["preference_selected_run_count"],
                totals["preference_input_run_count"],
            ),
            "avg_selected_preference_item_count": _average(
                totals["sum_selected_preference_item_count"],
                total_runs,
            ),
        },
        "redundancy": {
            "redundant_selected_run_count": totals["redundant_selected_run_count"],
            "redundant_selected_run_rate_percent": _ratio_percent(
                totals["redundant_selected_run_count"],
                total_runs,
            ),
            "avg_redundant_selected_item_count": _average(
                totals["sum_redundant_selected_item_count"],
                total_runs,
            ),
        },
        "top_selected_collectors": _top_counts(totals["total_selected_collectors"]),
        "top_selected_reasons": _top_counts(totals["total_selected_reasons"]),
        "top_selected_slots": _top_counts(totals["total_selected_slots"]),
        "top_selector_input_collectors": _top_counts(
            totals["total_selector_input_collectors"]
        ),
    }


# Block: Eval slice formatting
def _format_eval_slice(*, title: str, slice_report: dict[str, Any]) -> list[str]:
    window = _require_object(slice_report, "window")
    overview = _require_object(slice_report, "overview")
    selector = _require_object(slice_report, "selector")
    coverage = _require_object(slice_report, "coverage")
    preference = _require_object(slice_report, "preference")
    redundancy = _require_object(slice_report, "redundancy")
    run_count = int(slice_report["run_count"])
    return [
        title,
        (
            "window: "
            f"{window['oldest_created_at_utc_text']} -> {window['latest_created_at_utc_text']} "
            f"({run_count} runs)"
        ),
        (
            "overview: "
            f"empty {overview['empty_run_count']} ({overview['empty_run_rate_percent']}%), "
            f"raw {overview['avg_raw_candidate_count']}, "
            f"merged {overview['avg_merged_candidate_count']}, "
            f"selector_input {overview['avg_selector_input_candidate_count']}, "
            f"selected_items {overview['avg_selected_item_count']}"
        ),
        (
            "selector: "
            f"llm_selected {selector['avg_llm_selected_ref_count']}, "
            f"selected_candidates {selector['avg_selected_candidate_count']}, "
            f"return {selector['avg_llm_return_ratio_percent']}%, "
            f"adopt {selector['avg_selected_candidate_ratio_percent']}%, "
            f"duplicate {selector['avg_duplicate_hit_count']}, "
            f"skip {selector['avg_slot_skipped_count']}, "
            f"reserve {selector['avg_reserve_candidate_count']}"
        ),
        (
            "coverage: "
            f"explicit selected {coverage['explicit_time_selected_run_rate_percent']}% "
            f"({coverage['explicit_time_selected_run_count']}), "
            f"explicit input {coverage['explicit_time_input_run_rate_percent']}% "
            f"({coverage['explicit_time_input_run_count']}), "
            f"thread selected {coverage['thread_selected_run_rate_percent']}% "
            f"({coverage['thread_selected_run_count']}), "
            f"thread input {coverage['thread_input_run_rate_percent']}% "
            f"({coverage['thread_input_run_count']}), "
            f"relationship {coverage['relationship_selected_run_rate_percent']}% "
            f"({coverage['relationship_selected_run_count']})"
        ),
        (
            "preference: "
            f"input {preference['preference_input_run_rate_percent']}% "
            f"({preference['preference_input_run_count']}), "
            f"selected {preference['preference_selected_run_rate_percent']}% "
            f"({preference['preference_selected_run_count']}), "
            f"carryover {preference['preference_carryover_rate_percent']}%, "
            f"selected_items {preference['avg_selected_preference_item_count']}"
        ),
        (
            "redundancy: "
            f"runs {redundancy['redundant_selected_run_rate_percent']}% "
            f"({redundancy['redundant_selected_run_count']}), "
            f"selected_items {redundancy['avg_redundant_selected_item_count']}"
        ),
        (
            "top selected collectors: "
            f"{_format_count_map(_require_int_map(slice_report, 'top_selected_collectors'))}"
        ),
        (
            "top selected reasons: "
            f"{_format_count_map(_require_int_map(slice_report, 'top_selected_reasons'))}"
        ),
        (
            "top selected slots: "
            f"{_format_count_map(_require_int_map(slice_report, 'top_selected_slots'))}"
        ),
        (
            "top selector input collectors: "
            f"{_format_count_map(_require_int_map(slice_report, 'top_selector_input_collectors'))}"
        ),
    ]


# Block: Mode breakdown builder
def _build_mode_breakdown(retrieval_runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped_runs: dict[str, list[dict[str, Any]]] = {}
    for retrieval_run in retrieval_runs:
        mode = _retrieval_mode(retrieval_run)
        if mode not in grouped_runs:
            grouped_runs[mode] = []
        grouped_runs[mode].append(retrieval_run)
    return {
        mode_name: _build_eval_slice(grouped_runs[mode_name])
        for mode_name in _ordered_mode_names(grouped_runs.keys())
    }


# Block: Totals initializer
def _empty_eval_totals() -> dict[str, Any]:
    return {
        "empty_run_count": 0,
        "explicit_time_selected_run_count": 0,
        "explicit_time_input_run_count": 0,
        "thread_selected_run_count": 0,
        "thread_input_run_count": 0,
        "relationship_selected_run_count": 0,
        "preference_input_run_count": 0,
        "preference_selected_run_count": 0,
        "redundant_selected_run_count": 0,
        "sum_raw_candidate_count": 0,
        "sum_merged_candidate_count": 0,
        "sum_selector_input_candidate_count": 0,
        "sum_llm_selected_ref_count": 0,
        "sum_selected_candidate_count": 0,
        "sum_selected_item_count": 0,
        "sum_selected_preference_item_count": 0,
        "sum_redundant_selected_item_count": 0,
        "sum_llm_return_ratio_percent": 0,
        "sum_selected_candidate_ratio_percent": 0,
        "sum_duplicate_hit_count": 0,
        "sum_slot_skipped_count": 0,
        "sum_reserve_candidate_count": 0,
        "total_selected_collectors": {},
        "total_selected_reasons": {},
        "total_selected_slots": {},
        "total_selector_input_collectors": {},
    }


# Block: Totals accumulation
def _accumulate_eval_totals(
    *,
    totals: dict[str, Any],
    retrieval_run: dict[str, Any],
) -> None:
    selected_json = _require_object(retrieval_run, "selected")
    candidates_json = _require_object(retrieval_run, "candidates")
    public_detail = build_public_retrieval_detail(retrieval_run)
    selector_summary = _countable_object(selected_json.get("selector_summary"))
    selected_counts = _require_non_negative_int_map(selected_json, "selected_counts")
    selected_collector_counts = _count_map(selected_json.get("collector_counts"))
    selected_reason_counts = _count_map(selected_json.get("selected_reason_counts"))
    selector_input_trace = _optional_trace_entries(public_detail.get("selector_input_trace"))
    selection_trace = _optional_trace_entries(public_detail.get("selection_trace"))
    selected_item_total = sum(selected_counts.values())
    if selected_item_total == 0:
        totals["empty_run_count"] += 1
    if int(selected_counts.get("relationship_items", 0)) > 0:
        totals["relationship_selected_run_count"] += 1
    if _contains_any_key(selected_collector_counts, EXPLICIT_TIME_COLLECTORS) or _contains_any_key(
        selected_reason_counts,
        EXPLICIT_TIME_REASON_CODES,
    ):
        totals["explicit_time_selected_run_count"] += 1
    selector_input_collectors = _count_map(
        candidates_json.get("selector_input_collector_counts")
    )
    if _contains_any_key(selector_input_collectors, EXPLICIT_TIME_COLLECTORS):
        totals["explicit_time_input_run_count"] += 1
    if _contains_any_key(selected_collector_counts, THREAD_COLLECTORS):
        totals["thread_selected_run_count"] += 1
    if _contains_any_key(selector_input_collectors, THREAD_COLLECTORS):
        totals["thread_input_run_count"] += 1
    if _trace_has_memory_kind(selector_input_trace, "preference"):
        totals["preference_input_run_count"] += 1
    selected_preference_item_count = _trace_memory_kind_count(selection_trace, "preference")
    if selected_preference_item_count > 0:
        totals["preference_selected_run_count"] += 1
    totals["sum_selected_preference_item_count"] += selected_preference_item_count
    redundant_selected_item_count = _redundant_selected_item_count(selection_trace)
    if redundant_selected_item_count > 0:
        totals["redundant_selected_run_count"] += 1
    totals["sum_redundant_selected_item_count"] += redundant_selected_item_count
    totals["sum_raw_candidate_count"] += _int_metric(selector_summary, "raw_candidate_count")
    totals["sum_merged_candidate_count"] += _int_metric(
        selector_summary,
        "merged_candidate_count",
    )
    totals["sum_selector_input_candidate_count"] += _int_metric(
        selector_summary,
        "selector_input_candidate_count",
    )
    totals["sum_llm_selected_ref_count"] += _int_metric(
        selector_summary,
        "llm_selected_ref_count",
    )
    totals["sum_selected_candidate_count"] += _int_metric(
        selector_summary,
        "selected_candidate_count",
    )
    totals["sum_selected_item_count"] += selected_item_total
    totals["sum_llm_return_ratio_percent"] += _int_metric(
        selector_summary,
        "llm_return_ratio_percent",
    )
    totals["sum_selected_candidate_ratio_percent"] += _int_metric(
        selector_summary,
        "selected_candidate_ratio_percent",
    )
    totals["sum_duplicate_hit_count"] += _int_metric(selector_summary, "duplicate_hit_count")
    totals["sum_slot_skipped_count"] += _int_metric(selector_summary, "slot_skipped_count")
    totals["sum_reserve_candidate_count"] += _int_metric(
        selector_summary,
        "reserve_candidate_count",
    )
    _merge_counts(totals["total_selected_collectors"], selected_collector_counts)
    _merge_counts(totals["total_selected_reasons"], selected_reason_counts)
    _merge_counts(totals["total_selected_slots"], _selected_slot_counts(selected_counts))
    _merge_counts(totals["total_selector_input_collectors"], selector_input_collectors)


# Block: Empty eval slice
def _empty_eval_slice() -> dict[str, Any]:
    return {
        "run_count": 0,
        "window": {
            "latest_created_at": 0,
            "latest_created_at_utc_text": "",
            "oldest_created_at": 0,
            "oldest_created_at_utc_text": "",
        },
        "overview": {
            "empty_run_count": 0,
            "empty_run_rate_percent": 0,
            "avg_raw_candidate_count": 0.0,
            "avg_merged_candidate_count": 0.0,
            "avg_selector_input_candidate_count": 0.0,
            "avg_selected_item_count": 0.0,
        },
        "selector": {
            "avg_llm_selected_ref_count": 0.0,
            "avg_selected_candidate_count": 0.0,
            "avg_llm_return_ratio_percent": 0.0,
            "avg_selected_candidate_ratio_percent": 0.0,
            "avg_duplicate_hit_count": 0.0,
            "avg_slot_skipped_count": 0.0,
            "avg_reserve_candidate_count": 0.0,
        },
        "coverage": {
            "explicit_time_selected_run_count": 0,
            "explicit_time_selected_run_rate_percent": 0,
            "explicit_time_input_run_count": 0,
            "explicit_time_input_run_rate_percent": 0,
            "thread_selected_run_count": 0,
            "thread_selected_run_rate_percent": 0,
            "thread_input_run_count": 0,
            "thread_input_run_rate_percent": 0,
            "relationship_selected_run_count": 0,
            "relationship_selected_run_rate_percent": 0,
        },
        "preference": {
            "preference_input_run_count": 0,
            "preference_input_run_rate_percent": 0,
            "preference_selected_run_count": 0,
            "preference_selected_run_rate_percent": 0,
            "preference_carryover_rate_percent": 0,
            "avg_selected_preference_item_count": 0.0,
        },
        "redundancy": {
            "redundant_selected_run_count": 0,
            "redundant_selected_run_rate_percent": 0,
            "avg_redundant_selected_item_count": 0.0,
        },
        "top_selected_collectors": {},
        "top_selected_reasons": {},
        "top_selected_slots": {},
        "top_selector_input_collectors": {},
    }


# Block: Retrieval mode read
def _retrieval_mode(retrieval_run: dict[str, Any]) -> str:
    plan_json = _require_object(retrieval_run, "plan")
    mode = plan_json.get("mode")
    if not isinstance(mode, str) or not mode:
        raise RuntimeError("retrieval_run.plan.mode must be non-empty string")
    return mode


# Block: Mode ordering
def _ordered_mode_names(mode_names: Any) -> list[str]:
    if not isinstance(mode_names, Iterable):
        raise RuntimeError("mode_names must be iterable")
    unique_mode_names = {
        mode_name
        for mode_name in mode_names
        if isinstance(mode_name, str) and mode_name
    }
    known_mode_names = [
        mode_name
        for mode_name in MODE_ORDER
        if mode_name in unique_mode_names
    ]
    extra_mode_names = sorted(unique_mode_names - set(MODE_ORDER))
    return known_mode_names + extra_mode_names


# Block: Selected slot counts
def _selected_slot_counts(selected_counts: dict[str, int]) -> dict[str, int]:
    return {
        str(slot_name): int(count)
        for slot_name, count in selected_counts.items()
        if isinstance(count, int) and not isinstance(count, bool) and count > 0
    }


# Block: Generic count map
def _count_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): int(entry_value)
        for key, entry_value in value.items()
        if isinstance(entry_value, int) and not isinstance(entry_value, bool) and entry_value > 0
    }


# Block: Count map merge
def _merge_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + value


# Block: Top counts
def _top_counts(counts: dict[str, int]) -> dict[str, int]:
    ordered_items = sorted(
        counts.items(),
        key=lambda item: (-item[1], item[0]),
    )
    return {
        key: value
        for key, value in ordered_items[:TOP_COUNT_LIMIT]
    }


# Block: Count map formatting
def _format_count_map(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(
        f"{key}={value}"
        for key, value in counts.items()
    )


# Block: Membership helper
def _contains_any_key(counts: dict[str, int], expected_keys: frozenset[str]) -> bool:
    return any(key in counts for key in expected_keys)


# Block: Optional trace entries
def _optional_trace_entries(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RuntimeError("trace entries must be list")
    trace_entries: list[dict[str, Any]] = []
    for trace_entry in value:
        if not isinstance(trace_entry, dict):
            raise RuntimeError("trace entry must be object")
        trace_entries.append(trace_entry)
    return trace_entries


# Block: Trace memory kind 判定
def _trace_has_memory_kind(trace_entries: list[dict[str, Any]], memory_kind: str) -> bool:
    return any(
        trace_entry.get("memory_kind") == memory_kind
        for trace_entry in trace_entries
    )


# Block: Trace memory kind 件数
def _trace_memory_kind_count(trace_entries: list[dict[str, Any]], memory_kind: str) -> int:
    return sum(
        1
        for trace_entry in trace_entries
        if trace_entry.get("memory_kind") == memory_kind
    )


# Block: 冗長 selected item 件数
def _redundant_selected_item_count(selection_trace: list[dict[str, Any]]) -> int:
    if not selection_trace:
        return 0
    recent_texts = [
        normalized_text
        for normalized_text in (
            _normalized_trace_text(trace_entry)
            for trace_entry in selection_trace
            if trace_entry.get("slot") == "recent_event_window"
        )
        if normalized_text
    ]
    long_term_texts: list[str] = []
    redundant_item_count = 0
    for trace_entry in selection_trace:
        if trace_entry.get("slot") == "recent_event_window":
            continue
        normalized_text = _normalized_trace_text(trace_entry)
        if not normalized_text:
            continue
        if any(_texts_overlap(normalized_text, recent_text) for recent_text in recent_texts):
            redundant_item_count += 1
            continue
        if any(_texts_overlap(normalized_text, existing_text) for existing_text in long_term_texts):
            redundant_item_count += 1
            continue
        long_term_texts.append(normalized_text)
    return redundant_item_count


# Block: Trace text 正規化
def _normalized_trace_text(trace_entry: dict[str, Any]) -> str:
    text = trace_entry.get("text")
    if not isinstance(text, str) or not text:
        return ""
    return "".join(
        character.lower()
        for character in text
        if unicodedata.category(character)[0] in {"L", "N"}
    )


# Block: Text overlap 判定
def _texts_overlap(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right and min(len(left), len(right)) >= TEXT_OVERLAP_MIN_LENGTH:
        return True
    shorter_text, longer_text = sorted((left, right), key=len)
    if len(shorter_text) < TEXT_CONTAIN_MIN_LENGTH:
        return False
    return shorter_text in longer_text


# Block: Countable object read
def _countable_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return value


# Block: Required object read
def _require_object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise RuntimeError(f"{key} must be object")
    return value


# Block: Required int map read
def _require_int_map(payload: dict[str, Any], key: str) -> dict[str, int]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise RuntimeError(f"{key} must be object")
    int_map = _count_map(value)
    if len(int_map) != len(value):
        raise RuntimeError(f"{key} must contain only positive integer values")
    return int_map


# Block: Required non-negative int map read
def _require_non_negative_int_map(payload: dict[str, Any], key: str) -> dict[str, int]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise RuntimeError(f"{key} must be object")
    int_map = {
        str(entry_key): int(entry_value)
        for entry_key, entry_value in value.items()
        if isinstance(entry_value, int) and not isinstance(entry_value, bool) and entry_value >= 0
    }
    if len(int_map) != len(value):
        raise RuntimeError(f"{key} must contain only non-negative integer values")
    return int_map


# Block: Integer metric read
def _int_metric(payload: Any, key: str) -> int:
    if not isinstance(payload, dict):
        return 0
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return value


# Block: Rounded average
def _average(total: int, count: int) -> float:
    if count <= 0:
        return 0.0
    return round(total / count, 2)


# Block: Integer ratio percent
def _ratio_percent(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return 0
    return round((numerator / denominator) * 100)


# Block: UTC text
def _utc_text(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=timezone.utc,
    ).strftime("%Y-%m-%d %H:%M:%S UTC")
