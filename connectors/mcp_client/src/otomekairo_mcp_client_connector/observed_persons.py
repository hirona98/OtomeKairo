from __future__ import annotations

import json
from typing import Any

MCP_PERSON_OBJECT_KEYS = {"actor", "author"}


def observed_persons_from_mcp_result(
    *,
    mcp_server_id: str,
    content: list[Any],
    structured_content: dict[str, Any] | None,
) -> list[dict[str, str]]:
    server_id = mcp_server_id.strip()
    if not server_id:
        raise ValueError("mcp_server_id must be a non-empty string.")
    roots: list[Any] = []
    if isinstance(structured_content, dict):
        roots.append(structured_content)
    for item in content:
        payload = item
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump(by_alias=True, mode="json")
        if not isinstance(payload, dict) or payload.get("type") != "text":
            continue
        text = payload.get("text")
        parsed = _parse_json_value(text)
        if parsed is not None:
            roots.append(parsed)
    seen: set[str] = set()
    persons: list[dict[str, str]] = []
    for root in roots:
        _collect_observed_persons(root, server_id=server_id, seen=seen, persons=persons)
    return persons


def content_summary(content: list[Any], structured_content: dict[str, Any] | None = None) -> str:
    text_parts: list[str] = []
    for item in content:
        payload = item
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump(by_alias=True, mode="json")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "text" and isinstance(payload.get("text"), str):
            text_parts.append(payload["text"].strip())
    if text_parts:
        return " ".join(part for part in text_parts if part)
    if isinstance(structured_content, dict) and structured_content:
        return json.dumps(structured_content, ensure_ascii=False)
    if content:
        return json.dumps(content, ensure_ascii=False)
    return ""


def _collect_observed_persons(
    value: Any,
    *,
    server_id: str,
    seen: set[str],
    persons: list[dict[str, str]],
) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_observed_persons(item, server_id=server_id, seen=seen, persons=persons)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if key in MCP_PERSON_OBJECT_KEYS and isinstance(item, dict):
            person = _person_from_actor_object(item, server_id=server_id)
            if person is not None and person["person_ref"] not in seen:
                seen.add(person["person_ref"])
                persons.append(person)
        _collect_observed_persons(item, server_id=server_id, seen=seen, persons=persons)


def _person_from_actor_object(payload: dict[str, Any], *, server_id: str) -> dict[str, str] | None:
    handle = payload.get("handle")
    display_name = payload.get("display_name")
    if not isinstance(handle, str) or not handle.strip():
        return None
    if not isinstance(display_name, str) or not display_name.strip():
        return None
    return {
        "person_ref": f"person:mcp:{server_id}:{handle.strip()}",
        "display_name": display_name.strip(),
    }


def _parse_json_value(value: Any) -> Any | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, (dict, list)):
        return parsed
    return None
