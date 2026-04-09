from __future__ import annotations

import json
from typing import Any

from otomekairo.llm_contracts import LLMError, validate_recall_hint_contract


# RecallHint payload を validator 付きで解析する。
def parse_recall_hint_payload(content: str) -> dict[str, Any]:
    payload = parse_json_object(content)
    validate_recall_hint_contract(payload)
    return payload


# response から本文 text を取り出す。
def extract_response_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMError("LiteLLM response did not include choices.")

    message = getattr(choices[0], "message", None)
    if message is None and isinstance(choices[0], dict):
        message = choices[0].get("message")
    if message is None:
        raise LLMError("LiteLLM response did not include message.")

    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _flatten_content_parts(content)
    raise LLMError("LiteLLM response content was empty.")


def _flatten_content_parts(content: list[Any]) -> str:
    text_parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            text_parts.append(part)
            continue
        if not isinstance(part, dict):
            continue
        if isinstance(part.get("text"), str):
            text_parts.append(part["text"])
            continue
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            text_parts.append(part["text"])
            continue
    result = "".join(text_parts).strip()
    if not result:
        raise LLMError("LiteLLM response content parts were empty.")
    return result


# JSON object だけを返す応答を緩めに解析する。
def parse_json_object(content: str) -> dict[str, Any]:
    stripped = content.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = None

    if payload is None:
        normalized = stripped.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError:
            payload = None

    if payload is None:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(stripped[start : end + 1])
            except json.JSONDecodeError as exc:
                raise LLMError(f"LiteLLM JSON parse failed: {exc}") from exc

    if not isinstance(payload, dict):
        raise LLMError("LiteLLM did not return a JSON object.")
    return payload


# embedding response を検証しつつ vector に正規化する。
def extract_embedding_vectors(
    response: Any,
    *,
    expected_count: int,
    expected_dimension: int | None = None,
    source_label: str = "LiteLLM",
) -> list[list[float]]:
    data = getattr(response, "data", None)
    if data is None and isinstance(response, dict):
        data = response.get("data")
    if not isinstance(data, list) or len(data) != expected_count:
        raise LLMError(f"{source_label} embedding response did not include expected data.")

    vectors: list[list[float]] = []
    for item in data:
        vector = getattr(item, "embedding", None)
        if vector is None and isinstance(item, dict):
            vector = item.get("embedding")
        if not isinstance(vector, list) or not vector:
            raise LLMError(f"{source_label} embedding item did not include embedding.")
        parsed = [float(value) for value in vector]
        if expected_dimension is not None and len(parsed) != expected_dimension:
            raise LLMError(
                f"{source_label} embedding dimension mismatch: expected {expected_dimension}, got {len(parsed)}."
            )
        vectors.append(parsed)
    return vectors


# HTTP error body からユーザー向け detail を取り出す。
def extract_http_error_detail(error_body: str) -> str:
    stripped = error_body.strip()
    if not stripped:
        return "unknown_error"

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        error_value = payload.get("error")
        if isinstance(error_value, dict):
            message = error_value.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        if isinstance(error_value, str) and error_value.strip():
            return error_value.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()

    return stripped
