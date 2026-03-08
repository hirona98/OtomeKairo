"""Build normalized completion settings from effective runtime settings."""

from __future__ import annotations

from typing import Any


# Block: Completion 設定の正規化
def build_completion_settings(effective_settings: dict[str, Any]) -> dict[str, Any]:
    model = effective_settings.get("llm.model")
    api_key = effective_settings.get("llm.api_key")
    base_url = effective_settings.get("llm.base_url")
    temperature = effective_settings.get("llm.temperature")
    max_output_tokens = effective_settings.get("llm.max_output_tokens")
    if not isinstance(model, str) or not model:
        raise RuntimeError("llm.model must be a non-empty string")
    if not isinstance(api_key, str):
        raise RuntimeError("llm.api_key must be a string")
    if not isinstance(base_url, str):
        raise RuntimeError("llm.base_url must be a string")
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise RuntimeError("llm.temperature must be numeric")
    if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int):
        raise RuntimeError("llm.max_output_tokens must be integer")
    return {
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "temperature": float(temperature),
        "max_output_tokens": max_output_tokens,
    }
