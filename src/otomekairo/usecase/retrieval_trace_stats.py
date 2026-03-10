"""Shared trace statistics helpers for retrieval artifacts."""

from __future__ import annotations

from typing import Any


# Block: Collector counts
def collector_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        collector_names = entry.get("collector_names")
        if not isinstance(collector_names, list):
            continue
        for collector_name in collector_names:
            if not isinstance(collector_name, str) or not collector_name:
                continue
            counts[collector_name] = counts.get(collector_name, 0) + 1
    return counts


# Block: Reason counts
def reason_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        reason_codes = entry.get("reason_codes")
        if not isinstance(reason_codes, list):
            continue
        for reason_code in reason_codes:
            if not isinstance(reason_code, str) or not reason_code:
                continue
            counts[reason_code] = counts.get(reason_code, 0) + 1
    return counts


# Block: Slot counts
def slot_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        slot_name = entry.get("slot")
        if not isinstance(slot_name, str) or not slot_name:
            continue
        counts[slot_name] = counts.get(slot_name, 0) + 1
    return counts
