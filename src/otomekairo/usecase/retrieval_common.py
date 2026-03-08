"""Shared helpers for retrieval planning and candidate selection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# Block: Query helpers
def append_unique_text(target: list[str], text: str) -> None:
    normalized = text.strip()
    if not normalized:
        return
    if normalized not in target:
        target.append(normalized)


def observation_query_hint(current_observation: dict[str, Any]) -> str | None:
    if current_observation["input_kind"] != "network_result":
        return None
    query = current_observation.get("query")
    if not isinstance(query, str) or not query:
        raise ValueError("network_result query must be non-empty string")
    return query


def observation_text_hints(current_observation: dict[str, Any]) -> list[str]:
    observation_text = current_observation["observation_text"]
    if not isinstance(observation_text, str) or not observation_text:
        raise ValueError("current_observation.observation_text must be non-empty string")
    hints: list[str] = [observation_text]
    for token in text_hint_tokens(observation_text):
        if token not in hints:
            hints.append(token)
    query_hint = observation_query_hint(current_observation)
    if query_hint is not None and query_hint not in hints:
        hints.append(query_hint)
    return hints


def text_hint_tokens(text: str) -> list[str]:
    normalized_text = text
    for separator in (
        "　",
        "\n",
        "\t",
        ",",
        "、",
        ".",
        "。",
        "!",
        "！",
        "?",
        "？",
        ":",
        "：",
        ";",
        "；",
        "(",
        ")",
        "（",
        "）",
        "[",
        "]",
        "「",
        "」",
        "『",
        "』",
        "/",
        "／",
    ):
        normalized_text = normalized_text.replace(separator, " ")
    tokens: list[str] = []
    for raw_token in normalized_text.split(" "):
        token = raw_token.strip()
        if len(token) < 2:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def explicit_years_from_observation(current_observation: dict[str, Any]) -> list[int]:
    years: list[int] = []
    for token in text_hint_tokens(str(current_observation["observation_text"])):
        if len(token) != 4 or not token.isdigit():
            continue
        year = int(token)
        if 1900 <= year <= 2100 and year not in years:
            years.append(year)
    return years


# Block: Payload helpers
def payload_contains_text_hint(*, payload: dict[str, Any], text_hint: str) -> bool:
    return any(text_hint in text_part for text_part in payload_text_values(payload))


def payload_text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        text_values: list[str] = []
        for nested_value in value.values():
            text_values.extend(payload_text_values(nested_value))
        return text_values
    if isinstance(value, list):
        text_values: list[str] = []
        for nested_value in value:
            text_values.extend(payload_text_values(nested_value))
        return text_values
    return []


# Block: Trace helpers
def append_reason(reason_codes: list[str], reason_code: str) -> None:
    if reason_code not in reason_codes:
        reason_codes.append(reason_code)


def item_ref_for_slot(*, slot_name: str, item: dict[str, Any]) -> str:
    if slot_name == "recent_event_window":
        return f"event:{item['event_id']}"
    memory_kind = str(item["memory_kind"])
    memory_state_id = str(item["memory_state_id"])
    if memory_kind == "episodic_event":
        return f"event:{memory_state_id}"
    if memory_kind == "event_affect":
        return f"event_affect:{memory_state_id}"
    if memory_kind == "preference":
        return f"preference:{memory_state_id}"
    return f"memory_state:{memory_state_id}"


# Block: Time helpers
def utc_text(unix_ms: int) -> str:
    return datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def local_text(unix_ms: int) -> str:
    local_dt = datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc).astimezone()
    timezone_name = local_dt.tzname() or "UTC"
    return local_dt.strftime(f"%Y-%m-%d %H:%M:%S {timezone_name}")


def relative_time_text(now_ms: int, past_ms: int) -> str:
    delta_seconds = max(0, (now_ms - past_ms) // 1000)
    if delta_seconds < 60:
        return f"{delta_seconds}秒前"
    delta_minutes = delta_seconds // 60
    if delta_minutes < 60:
        return f"{delta_minutes}分前"
    delta_hours = delta_minutes // 60
    if delta_hours < 24:
        return f"{delta_hours}時間前"
    delta_days = delta_hours // 24
    return f"{delta_days}日前"
