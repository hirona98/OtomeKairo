from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


USAGE_INT_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "total_tokens",
)


def extract_usage(response: Any) -> dict[str, int]:
    raw = _usage_object(response)
    if raw is None:
        return {}
    payload: dict[str, int] = {}
    prompt_tokens = _int_field(raw, "prompt_tokens")
    completion_tokens = _int_field(raw, "completion_tokens")
    total_tokens = _int_field(raw, "total_tokens")
    cached_tokens = _nested_int(raw, "prompt_tokens_details", "cached_tokens")
    if cached_tokens is None:
        cached_tokens = _int_field(raw, "cached_tokens")
    reasoning_tokens = _nested_int(raw, "completion_tokens_details", "reasoning_tokens")
    if reasoning_tokens is None:
        reasoning_tokens = _int_field(raw, "reasoning_tokens")
    if prompt_tokens is not None:
        payload["prompt_tokens"] = prompt_tokens
    if completion_tokens is not None:
        payload["completion_tokens"] = completion_tokens
    if cached_tokens is not None:
        payload["cached_tokens"] = cached_tokens
    if reasoning_tokens is not None:
        payload["reasoning_tokens"] = reasoning_tokens
    if total_tokens is not None:
        payload["total_tokens"] = total_tokens
    elif prompt_tokens is not None or completion_tokens is not None:
        payload["total_tokens"] = (prompt_tokens or 0) + (completion_tokens or 0)
    return payload


def summarize_usage_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    totals = {key: 0 for key in USAGE_INT_KEYS}
    has_values = {key: False for key in USAGE_INT_KEYS}
    for event in events:
        if not isinstance(event, dict):
            continue
        call: dict[str, Any] = {}
        operation = event.get("operation")
        if isinstance(operation, str) and operation.strip():
            call["operation"] = operation.strip()
        for key in USAGE_INT_KEYS:
            value = event.get(key)
            if isinstance(value, int) and value >= 0:
                call[key] = value
                totals[key] += value
                has_values[key] = True
        if call:
            calls.append(call)
    summary: dict[str, Any] = {
        "call_count": len(calls),
        "calls": calls,
    }
    for key in USAGE_INT_KEYS:
        if has_values[key]:
            summary[key] = totals[key]
    return summary


def consume_client_usage(llm: Any, *, scope_id: str) -> dict[str, Any]:
    consume = getattr(llm, "consume_usage_scope", None)
    if not callable(consume):
        raise TypeError("llm.consume_usage_scope is required.")
    events = consume(scope_id)
    if not isinstance(events, list):
        raise TypeError("llm.consume_usage_scope must return a list.")
    return summarize_usage_events(events)


@contextmanager
def usage_scope(llm: Any, scope_id: str) -> Iterator[None]:
    push = getattr(llm, "push_usage_scope", None)
    has_scope = getattr(llm, "has_usage_scope", None)
    consume = getattr(llm, "consume_usage_scope", None)
    if not callable(push) or not callable(has_scope) or not callable(consume):
        raise TypeError("llm usage scope methods are required.")
    push(scope_id)
    try:
        yield
    finally:
        if has_scope(scope_id):
            consume(scope_id)


def merge_usage_summaries(*summaries: dict[str, Any] | None) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        calls = summary.get("calls")
        if isinstance(calls, list):
            events.extend(item for item in calls if isinstance(item, dict))
    return summarize_usage_events(events)


def _usage_object(response: Any) -> Any:
    if response is None:
        return None
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    return usage


def _int_field(source: Any, name: str) -> int | None:
    if isinstance(source, dict):
        value = source.get(name)
    else:
        value = getattr(source, name, None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _nested_int(source: Any, parent_name: str, field_name: str) -> int | None:
    if isinstance(source, dict):
        parent = source.get(parent_name)
    else:
        parent = getattr(source, parent_name, None)
    if parent is None:
        return None
    return _int_field(parent, field_name)
