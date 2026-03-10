"""Build retrieval evaluation reports from retrieval_runs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# Block: Eval constants
THREAD_COLLECTORS = frozenset({"reply_chain", "context_threads"})
EXPLICIT_TIME_COLLECTORS = frozenset({"explicit_time"})
EXPLICIT_TIME_REASON_CODES = frozenset(
    {"matched_explicit_date", "matched_explicit_year", "matched_life_stage"}
)
TOP_COUNT_LIMIT = 8


# Block: Eval report builder
def build_retrieval_eval_report(retrieval_runs: list[dict[str, Any]]) -> dict[str, Any]:
    if not retrieval_runs:
        return {
            "run_count": 0,
            "window": {},
            "overview": {},
            "selector": {},
            "coverage": {},
            "top_selected_collectors": {},
            "top_selected_reasons": {},
            "top_selected_slots": {},
            "top_selector_input_collectors": {},
        }
    ordered_runs = sorted(
        retrieval_runs,
        key=lambda retrieval_run: int(retrieval_run["created_at"]),
        reverse=True,
    )
    total_runs = len(ordered_runs)
    created_at_values = [
        int(retrieval_run["created_at"])
        for retrieval_run in ordered_runs
    ]
    empty_run_count = 0
    explicit_time_selected_run_count = 0
    explicit_time_input_run_count = 0
    thread_selected_run_count = 0
    thread_input_run_count = 0
    relationship_selected_run_count = 0
    sum_raw_candidate_count = 0
    sum_merged_candidate_count = 0
    sum_selector_input_candidate_count = 0
    sum_llm_selected_ref_count = 0
    sum_selected_candidate_count = 0
    sum_selected_item_count = 0
    sum_llm_return_ratio_percent = 0
    sum_selected_candidate_ratio_percent = 0
    sum_duplicate_hit_count = 0
    sum_slot_skipped_count = 0
    sum_reserve_candidate_count = 0
    total_selected_collectors: dict[str, int] = {}
    total_selected_reasons: dict[str, int] = {}
    total_selected_slots: dict[str, int] = {}
    total_selector_input_collectors: dict[str, int] = {}
    for retrieval_run in ordered_runs:
        selected_json = retrieval_run["selected"]
        candidates_json = retrieval_run["candidates"]
        selector_summary = selected_json.get("selector_summary", {})
        selected_counts = selected_json["selected_counts"]
        selected_collector_counts = _count_map(selected_json.get("collector_counts"))
        selected_reason_counts = _count_map(selected_json.get("selected_reason_counts"))
        selected_item_total = sum(
            int(count)
            for count in selected_counts.values()
            if isinstance(count, int) and not isinstance(count, bool)
        )
        if selected_item_total == 0:
            empty_run_count += 1
        if int(selected_counts.get("relationship_items", 0)) > 0:
            relationship_selected_run_count += 1
        if _contains_any_key(selected_collector_counts, EXPLICIT_TIME_COLLECTORS) or _contains_any_key(
            selected_reason_counts,
            EXPLICIT_TIME_REASON_CODES,
        ):
            explicit_time_selected_run_count += 1
        selector_input_collectors = _count_map(
            candidates_json.get("selector_input_collector_counts")
        )
        if _contains_any_key(selector_input_collectors, EXPLICIT_TIME_COLLECTORS):
            explicit_time_input_run_count += 1
        if _contains_any_key(selected_collector_counts, THREAD_COLLECTORS):
            thread_selected_run_count += 1
        if _contains_any_key(selector_input_collectors, THREAD_COLLECTORS):
            thread_input_run_count += 1
        sum_raw_candidate_count += _int_metric(selector_summary, "raw_candidate_count")
        sum_merged_candidate_count += _int_metric(selector_summary, "merged_candidate_count")
        sum_selector_input_candidate_count += _int_metric(
            selector_summary,
            "selector_input_candidate_count",
        )
        sum_llm_selected_ref_count += _int_metric(selector_summary, "llm_selected_ref_count")
        sum_selected_candidate_count += _int_metric(
            selector_summary,
            "selected_candidate_count",
        )
        sum_selected_item_count += selected_item_total
        sum_llm_return_ratio_percent += _int_metric(
            selector_summary,
            "llm_return_ratio_percent",
        )
        sum_selected_candidate_ratio_percent += _int_metric(
            selector_summary,
            "selected_candidate_ratio_percent",
        )
        sum_duplicate_hit_count += _int_metric(selector_summary, "duplicate_hit_count")
        sum_slot_skipped_count += _int_metric(selector_summary, "slot_skipped_count")
        sum_reserve_candidate_count += _int_metric(
            selector_summary,
            "reserve_candidate_count",
        )
        _merge_counts(total_selected_collectors, selected_collector_counts)
        _merge_counts(total_selected_reasons, selected_reason_counts)
        _merge_counts(total_selected_slots, _selected_slot_counts(selected_counts))
        _merge_counts(total_selector_input_collectors, selector_input_collectors)
    return {
        "run_count": total_runs,
        "window": {
            "latest_created_at": created_at_values[0],
            "latest_created_at_utc_text": _utc_text(created_at_values[0]),
            "oldest_created_at": created_at_values[-1],
            "oldest_created_at_utc_text": _utc_text(created_at_values[-1]),
        },
        "overview": {
            "empty_run_count": empty_run_count,
            "empty_run_rate_percent": _ratio_percent(empty_run_count, total_runs),
            "avg_raw_candidate_count": _average(sum_raw_candidate_count, total_runs),
            "avg_merged_candidate_count": _average(sum_merged_candidate_count, total_runs),
            "avg_selector_input_candidate_count": _average(
                sum_selector_input_candidate_count,
                total_runs,
            ),
            "avg_selected_item_count": _average(sum_selected_item_count, total_runs),
        },
        "selector": {
            "avg_llm_selected_ref_count": _average(sum_llm_selected_ref_count, total_runs),
            "avg_selected_candidate_count": _average(
                sum_selected_candidate_count,
                total_runs,
            ),
            "avg_llm_return_ratio_percent": _average(
                sum_llm_return_ratio_percent,
                total_runs,
            ),
            "avg_selected_candidate_ratio_percent": _average(
                sum_selected_candidate_ratio_percent,
                total_runs,
            ),
            "avg_duplicate_hit_count": _average(sum_duplicate_hit_count, total_runs),
            "avg_slot_skipped_count": _average(sum_slot_skipped_count, total_runs),
            "avg_reserve_candidate_count": _average(
                sum_reserve_candidate_count,
                total_runs,
            ),
        },
        "coverage": {
            "explicit_time_selected_run_count": explicit_time_selected_run_count,
            "explicit_time_selected_run_rate_percent": _ratio_percent(
                explicit_time_selected_run_count,
                total_runs,
            ),
            "explicit_time_input_run_count": explicit_time_input_run_count,
            "explicit_time_input_run_rate_percent": _ratio_percent(
                explicit_time_input_run_count,
                total_runs,
            ),
            "thread_selected_run_count": thread_selected_run_count,
            "thread_selected_run_rate_percent": _ratio_percent(
                thread_selected_run_count,
                total_runs,
            ),
            "thread_input_run_count": thread_input_run_count,
            "thread_input_run_rate_percent": _ratio_percent(
                thread_input_run_count,
                total_runs,
            ),
            "relationship_selected_run_count": relationship_selected_run_count,
            "relationship_selected_run_rate_percent": _ratio_percent(
                relationship_selected_run_count,
                total_runs,
            ),
        },
        "top_selected_collectors": _top_counts(total_selected_collectors),
        "top_selected_reasons": _top_counts(total_selected_reasons),
        "top_selected_slots": _top_counts(total_selected_slots),
        "top_selector_input_collectors": _top_counts(total_selector_input_collectors),
    }


# Block: Eval report formatting
def format_retrieval_eval_report(report: dict[str, Any]) -> str:
    run_count = int(report.get("run_count", 0))
    if run_count == 0:
        return "retrieval eval: no retrieval runs"
    window = report["window"]
    overview = report["overview"]
    selector = report["selector"]
    coverage = report["coverage"]
    lines = [
        "retrieval eval",
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
        f"top selected collectors: {_format_count_map(report['top_selected_collectors'])}",
        f"top selected reasons: {_format_count_map(report['top_selected_reasons'])}",
        f"top selected slots: {_format_count_map(report['top_selected_slots'])}",
        (
            "top selector input collectors: "
            f"{_format_count_map(report['top_selector_input_collectors'])}"
        ),
    ]
    return "\n".join(lines)


# Block: Selected slot counts
def _selected_slot_counts(selected_counts: dict[str, Any]) -> dict[str, int]:
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
