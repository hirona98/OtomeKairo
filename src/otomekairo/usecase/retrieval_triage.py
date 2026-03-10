"""retrieval_runs から manual review 用 triage report を構築する。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from otomekairo.usecase.retrieval_public_view import build_public_retrieval_detail


# Block: 定数
TRIAGE_REPORT_SCHEMA_VERSION = 1
TRACE_ITEM_LIMIT = 3
THREAD_COLLECTORS = frozenset({"reply_chain", "context_threads"})
EXPLICIT_TIME_COLLECTORS = frozenset({"explicit_time"})
EXPLICIT_TIME_REASON_CODES = frozenset(
    {"matched_explicit_date", "matched_explicit_year", "matched_life_stage"}
)
FLAG_PRIORITY = {
    "empty_selection": 100,
    "explicit_time_dropped": 90,
    "thread_dropped": 70,
    "relationship_dropped": 60,
    "low_adopt_ratio": 40,
    "slot_pressure": 30,
    "reserve_heavy": 20,
    "duplicate_heavy": 10,
}
FLAG_NOTE = {
    "empty_selection": "最終採用が 0 件",
    "explicit_time_dropped": "explicit_time が selector input にあるのに最終採用へ残っていない",
    "thread_dropped": "thread 系候補が input にあるのに最終採用へ残っていない",
    "relationship_dropped": "relationship_focus 候補が input にあるのに relationship_items が 0 件",
    "low_adopt_ratio": "selector input に対して採用率が低い",
    "slot_pressure": "slot 上限で見送りが多い",
    "reserve_heavy": "reserve 候補が多い",
    "duplicate_heavy": "collector 重複で merge 圧縮が大きい",
}


# Block: Public builder
def build_retrieval_triage_report(
    retrieval_runs: list[dict[str, Any]],
    *,
    max_packets: int,
    only_flagged: bool,
) -> dict[str, Any]:
    if max_packets <= 0:
        raise ValueError("max_packets must be positive")
    ordered_runs = sorted(
        retrieval_runs,
        key=lambda retrieval_run: int(retrieval_run["created_at"]),
        reverse=True,
    )
    triage_flag_counts: dict[str, int] = {}
    mode_counts: dict[str, int] = {}
    flagged_packets: list[dict[str, Any]] = []
    flagged_run_count = 0
    for retrieval_run in ordered_runs:
        review_packet = _build_review_packet(retrieval_run)
        mode_name = str(review_packet["mode"])
        mode_counts[mode_name] = mode_counts.get(mode_name, 0) + 1
        flag_codes = list(review_packet["flag_codes"])
        if flag_codes:
            flagged_run_count += 1
            for flag_code in flag_codes:
                triage_flag_counts[flag_code] = triage_flag_counts.get(flag_code, 0) + 1
        if only_flagged and not flag_codes:
            continue
        flagged_packets.append(review_packet)
    ordered_packets = sorted(
        flagged_packets,
        key=lambda review_packet: (
            int(review_packet["review_priority"]),
            int(review_packet["created_at"]),
            str(review_packet["cycle_id"]),
        ),
        reverse=True,
    )
    return {
        "report_schema_version": TRIAGE_REPORT_SCHEMA_VERSION,
        "run_count": len(ordered_runs),
        "flagged_run_count": flagged_run_count,
        "returned_packet_count": min(len(ordered_packets), max_packets),
        "only_flagged": only_flagged,
        "max_packets": max_packets,
        "triage_flag_counts": _ordered_count_map(triage_flag_counts),
        "mode_counts": _ordered_count_map(mode_counts),
        "review_packets": ordered_packets[:max_packets],
    }


# Block: Text formatter
def format_retrieval_triage_report(report: dict[str, Any]) -> str:
    run_count = _required_int(report, "run_count")
    if run_count == 0:
        return "retrieval triage: no retrieval runs"
    flagged_run_count = _required_int(report, "flagged_run_count")
    returned_packet_count = _required_int(report, "returned_packet_count")
    only_flagged = _required_bool(report, "only_flagged")
    lines = [
        "retrieval triage",
        (
            "summary: "
            f"flagged {flagged_run_count}/{run_count}, "
            f"returned {returned_packet_count}, "
            f"only_flagged={'yes' if only_flagged else 'no'}"
        ),
        f"flags: {_format_count_map(_required_count_map(report, 'triage_flag_counts'))}",
        f"modes: {_format_count_map(_required_count_map(report, 'mode_counts'))}",
    ]
    review_packets = report.get("review_packets")
    if not isinstance(review_packets, list):
        raise RuntimeError("review_packets must be list")
    if not review_packets:
        lines.append("packets: none")
        return "\n".join(lines)
    lines.append("packets:")
    for review_packet in review_packets:
        lines.extend(_format_review_packet(review_packet))
    return "\n".join(lines)


# Block: Review packet builder
def _build_review_packet(retrieval_run: dict[str, Any]) -> dict[str, Any]:
    public_detail = build_public_retrieval_detail(retrieval_run)
    selector_summary = _required_object(public_detail, "selector_summary")
    selected_counts = _required_non_negative_count_map(public_detail, "selected_counts")
    selector_input_collectors = _count_map(public_detail.get("selector_input_collector_counts"))
    selected_collectors = _count_map(public_detail.get("collector_counts"))
    selected_reason_counts = _count_map(public_detail.get("selected_reason_counts"))
    selected_item_count = sum(selected_counts.values())
    flag_codes = _flag_codes(
        selected_item_count=selected_item_count,
        selector_summary=selector_summary,
        selector_input_collectors=selector_input_collectors,
        selected_collectors=selected_collectors,
        selected_reason_counts=selected_reason_counts,
        selected_counts=selected_counts,
    )
    return {
        "cycle_id": _required_str(public_detail, "cycle_id"),
        "created_at": _required_int(public_detail, "created_at"),
        "created_at_utc_text": _utc_text(_required_int(public_detail, "created_at")),
        "mode": _required_str(public_detail, "mode"),
        "queries": _required_string_list(public_detail, "queries"),
        "flag_codes": flag_codes,
        "focus_notes": [FLAG_NOTE[flag_code] for flag_code in flag_codes],
        "review_priority": sum(FLAG_PRIORITY[flag_code] for flag_code in flag_codes),
        "summary": {
            "selected_item_count": selected_item_count,
            "selector_input_candidate_count": _int_metric(
                selector_summary,
                "selector_input_candidate_count",
            ),
            "selected_candidate_count": _int_metric(
                selector_summary,
                "selected_candidate_count",
            ),
            "llm_selected_ref_count": _int_metric(selector_summary, "llm_selected_ref_count"),
            "selected_candidate_ratio_percent": _int_metric(
                selector_summary,
                "selected_candidate_ratio_percent",
            ),
            "slot_skipped_count": _int_metric(selector_summary, "slot_skipped_count"),
            "reserve_candidate_count": _int_metric(
                selector_summary,
                "reserve_candidate_count",
            ),
            "duplicate_hit_count": _int_metric(selector_summary, "duplicate_hit_count"),
        },
        "selected_items": _trace_preview(public_detail.get("selection_trace")),
        "slot_skipped_items": _trace_preview(public_detail.get("slot_skipped_trace")),
        "reserve_items": _trace_preview(public_detail.get("reserve_trace")),
    }


# Block: Flag 判定
def _flag_codes(
    *,
    selected_item_count: int,
    selector_summary: dict[str, Any],
    selector_input_collectors: dict[str, int],
    selected_collectors: dict[str, int],
    selected_reason_counts: dict[str, int],
    selected_counts: dict[str, int],
) -> list[str]:
    flag_codes: list[str] = []
    if selected_item_count == 0:
        flag_codes.append("empty_selection")
    if _contains_any_key(selector_input_collectors, EXPLICIT_TIME_COLLECTORS) and not (
        _contains_any_key(selected_collectors, EXPLICIT_TIME_COLLECTORS)
        or _contains_any_key(selected_reason_counts, EXPLICIT_TIME_REASON_CODES)
    ):
        flag_codes.append("explicit_time_dropped")
    if _contains_any_key(selector_input_collectors, THREAD_COLLECTORS) and not _contains_any_key(
        selected_collectors,
        THREAD_COLLECTORS,
    ):
        flag_codes.append("thread_dropped")
    if int(selector_input_collectors.get("relationship_focus", 0)) > 0 and int(
        selected_counts.get("relationship_items", 0)
    ) == 0:
        flag_codes.append("relationship_dropped")
    selector_input_candidate_count = _int_metric(selector_summary, "selector_input_candidate_count")
    selected_candidate_ratio_percent = _int_metric(
        selector_summary,
        "selected_candidate_ratio_percent",
    )
    if selector_input_candidate_count >= 4 and selected_candidate_ratio_percent <= 25:
        flag_codes.append("low_adopt_ratio")
    if _int_metric(selector_summary, "slot_skipped_count") >= 2:
        flag_codes.append("slot_pressure")
    if _int_metric(selector_summary, "reserve_candidate_count") >= 4:
        flag_codes.append("reserve_heavy")
    if _int_metric(selector_summary, "duplicate_hit_count") >= 4:
        flag_codes.append("duplicate_heavy")
    return flag_codes


# Block: Trace preview
def _trace_preview(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RuntimeError("trace preview source must be list")
    return [
        _trace_preview_entry(trace_entry)
        for trace_entry in value[:TRACE_ITEM_LIMIT]
    ]


# Block: Trace preview entry
def _trace_preview_entry(trace_entry: Any) -> dict[str, Any]:
    if not isinstance(trace_entry, dict):
        raise RuntimeError("trace entry must be object")
    payload = {
        "item_ref": _required_str(trace_entry, "item_ref"),
        "slot": _required_str(trace_entry, "slot"),
        "score": _required_number(trace_entry, "score"),
        "collector_names": _required_string_list(trace_entry, "collector_names"),
        "reason_codes": _required_string_list(trace_entry, "reason_codes"),
        "text": _required_str(trace_entry, "text"),
        "relative_time_text": _required_str(trace_entry, "relative_time_text"),
    }
    duplicate_hits = trace_entry.get("duplicate_hits")
    if isinstance(duplicate_hits, int) and not isinstance(duplicate_hits, bool):
        payload["duplicate_hits"] = duplicate_hits
    selection_rank = trace_entry.get("selection_rank")
    if isinstance(selection_rank, int) and not isinstance(selection_rank, bool):
        payload["selection_rank"] = selection_rank
    memory_kind = trace_entry.get("memory_kind")
    if isinstance(memory_kind, str) and memory_kind:
        payload["memory_kind"] = memory_kind
    about_time_hint_text = trace_entry.get("about_time_hint_text")
    if isinstance(about_time_hint_text, str) and about_time_hint_text:
        payload["about_time_hint_text"] = about_time_hint_text
    return payload


# Block: Review packet formatting
def _format_review_packet(review_packet: dict[str, Any]) -> list[str]:
    summary = _required_object(review_packet, "summary")
    lines = [
        (
            f"- {review_packet['cycle_id']} "
            f"{review_packet['created_at_utc_text']} "
            f"mode={review_packet['mode']} "
            f"priority={review_packet['review_priority']}"
        ),
        (
            "  flags: "
            + (
                ", ".join(_required_string_list(review_packet, "flag_codes"))
                if review_packet["flag_codes"]
                else "none"
            )
        ),
        f"  queries: {' / '.join(_required_string_list(review_packet, 'queries'))}",
        (
            "  summary: "
            f"selected_items={summary['selected_item_count']}, "
            f"selector_input={summary['selector_input_candidate_count']}, "
            f"selected_candidates={summary['selected_candidate_count']}, "
            f"adopt={summary['selected_candidate_ratio_percent']}%, "
            f"skip={summary['slot_skipped_count']}, "
            f"reserve={summary['reserve_candidate_count']}, "
            f"duplicate={summary['duplicate_hit_count']}"
        ),
    ]
    focus_notes = _required_string_list(review_packet, "focus_notes")
    if focus_notes:
        lines.append(f"  notes: {' / '.join(focus_notes)}")
    for label, field_name in (
        ("selected", "selected_items"),
        ("skipped", "slot_skipped_items"),
        ("reserve", "reserve_items"),
    ):
        trace_entries = review_packet.get(field_name)
        if not isinstance(trace_entries, list):
            raise RuntimeError(f"{field_name} must be list")
        if trace_entries:
            lines.append(f"  {label}: {_format_trace_entries(trace_entries)}")
    return lines


# Block: Trace entries formatting
def _format_trace_entries(trace_entries: list[dict[str, Any]]) -> str:
    return " | ".join(
        _format_trace_entry(trace_entry)
        for trace_entry in trace_entries
    )


# Block: Trace entry formatting
def _format_trace_entry(trace_entry: dict[str, Any]) -> str:
    rank_text = ""
    selection_rank = trace_entry.get("selection_rank")
    if isinstance(selection_rank, int) and selection_rank > 0:
        rank_text = f"#{selection_rank} "
    collector_text = ",".join(_required_string_list(trace_entry, "collector_names"))
    reason_text = ",".join(_required_string_list(trace_entry, "reason_codes"))
    text = _trim_text(_required_str(trace_entry, "text"), max_length=28)
    return (
        f"{rank_text}{trace_entry['slot']} "
        f"[{collector_text}] "
        f"[{reason_text}] "
        f"{text} "
        f"({trace_entry['relative_time_text']})"
    )


# Block: Ordered count map
def _ordered_count_map(counts: dict[str, int]) -> dict[str, int]:
    return {
        key: value
        for key, value in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    }


# Block: Count map
def _count_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): int(entry_value)
        for key, entry_value in value.items()
        if isinstance(entry_value, int) and not isinstance(entry_value, bool) and entry_value > 0
    }


# Block: Required object
def _required_object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise RuntimeError(f"{key} must be object")
    return value


# Block: Required count map
def _required_count_map(payload: dict[str, Any], key: str) -> dict[str, int]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise RuntimeError(f"{key} must be object")
    counts = _count_map(value)
    if len(counts) != len(value):
        raise RuntimeError(f"{key} must contain only positive integer values")
    return counts


# Block: Required non-negative count map
def _required_non_negative_count_map(payload: dict[str, Any], key: str) -> dict[str, int]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise RuntimeError(f"{key} must be object")
    counts = {
        str(entry_key): int(entry_value)
        for entry_key, entry_value in value.items()
        if isinstance(entry_value, int) and not isinstance(entry_value, bool) and entry_value >= 0
    }
    if len(counts) != len(value):
        raise RuntimeError(f"{key} must contain only non-negative integer values")
    return counts


# Block: Required string
def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{key} must be non-empty string")
    return value


# Block: Required int
def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeError(f"{key} must be integer")
    return value


# Block: Required bool
def _required_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise RuntimeError(f"{key} must be bool")
    return value


# Block: Required number
def _required_number(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise RuntimeError(f"{key} must be number")
    return round(float(value), 3)


# Block: Required string list
def _required_string_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise RuntimeError(f"{key} must be list")
    string_list = [
        str(entry_value)
        for entry_value in value
        if isinstance(entry_value, str) and entry_value
    ]
    if len(string_list) != len(value):
        raise RuntimeError(f"{key} must contain non-empty strings only")
    return string_list


# Block: Int metric
def _int_metric(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        return 0
    return value


# Block: Key membership
def _contains_any_key(counts: dict[str, int], expected_keys: frozenset[str]) -> bool:
    return any(key in counts for key in expected_keys)


# Block: Count map formatting
def _format_count_map(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(
        f"{key}={value}"
        for key, value in counts.items()
    )


# Block: Text trim
def _trim_text(value: str, *, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3] + "..."


# Block: UTC text
def _utc_text(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=timezone.utc,
    ).strftime("%Y-%m-%d %H:%M:%S UTC")
