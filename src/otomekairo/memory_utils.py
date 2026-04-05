from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
import hashlib
import json
from typing import Any


# Block: Json
def to_json_string(value: Any) -> str:
    # Block: Serialize
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def stable_json(value: Any) -> str:
    # Block: Hash
    return hashlib.sha256(
        to_json_string(value).encode("utf-8")
    ).hexdigest()


# Block: Time
def now_iso() -> str:
    # Block: Timestamp
    return datetime.now(UTC).isoformat()


def parse_iso(value: str) -> datetime:
    # Block: Normalize
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def hours_since(older_iso: str, newer_iso: str) -> float:
    # Block: Delta
    older = parse_iso(older_iso)
    newer = parse_iso(newer_iso)
    return max(0.0, (newer - older).total_seconds() / 3600.0)


def days_since(older_iso: str | None, newer_iso: str) -> int:
    # Block: Guard
    if not isinstance(older_iso, str) or not older_iso:
        return 0

    # Block: Delta
    older = parse_iso(older_iso)
    newer = parse_iso(newer_iso)
    delta = newer - older
    if delta <= timedelta(0):
        return 0
    return delta.days


def timestamp_sort_key(value: Any) -> float:
    # Block: Parse
    if not isinstance(value, str) or not value:
        return float("inf")
    return parse_iso(value).timestamp()


# Block: Scoring
def clamp_score(value: Any) -> float:
    # Block: Normalize
    if not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(float(value), 1.0))


# Block: Text
def normalized_text_list(values: list[Any], *, limit: int) -> list[str]:
    # Block: Normalize
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped and stripped not in normalized:
            normalized.append(stripped)
        if len(normalized) >= limit:
            break
    return normalized


def optional_text(value: Any) -> str | None:
    # Block: Normalize
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped


def display_scope_key(scope_key: str) -> str:
    # Block: TopicPrefix
    if scope_key.startswith("topic:"):
        return scope_key.split(":", 1)[1]

    # Block: Result
    return scope_key


# Block: Collections
def merged_event_ids(existing_event_ids: list[Any], new_event_ids: list[str]) -> list[str]:
    # Block: Merge
    merged: list[str] = []
    for event_id in existing_event_ids + new_event_ids:
        if isinstance(event_id, str) and event_id not in merged:
            merged.append(event_id)
    return merged


def merged_cycle_ids(existing_cycle_ids: list[Any], new_cycle_ids: list[str]) -> list[str]:
    # Block: Merge
    merged: list[str] = []
    for cycle_id in existing_cycle_ids + new_cycle_ids:
        if isinstance(cycle_id, str) and cycle_id not in merged:
            merged.append(cycle_id)
    return merged


def unique_memory_unit_ids(actions: list[dict[str, Any]]) -> list[str]:
    # Block: Collect
    unique_ids: list[str] = []
    for action in actions:
        memory_unit_id = action.get("memory_unit_id")
        if not isinstance(memory_unit_id, str):
            continue
        if memory_unit_id in unique_ids:
            continue
        unique_ids.append(memory_unit_id)

    # Block: Result
    return unique_ids


def action_counts(actions: list[dict[str, Any]]) -> dict[str, int]:
    # Block: Count
    counts = Counter(action["operation"] for action in actions)

    # Block: Result
    return dict(counts)
