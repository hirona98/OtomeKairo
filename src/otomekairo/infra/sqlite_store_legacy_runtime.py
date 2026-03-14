"""Runtime settings and shared SQLite helper functions for the SQLite state store."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from otomekairo.schema.settings import build_default_settings


# Block: Legacy setting constants
LEGACY_SETTING_KEY_ALIASES = {
    "llm.model": "llm.default_model",
    "speech.tts.aivis_cloud.api_key": "speech.tts.api_key",
    "speech.tts.aivis_cloud.endpoint_url": "speech.tts.endpoint_url",
    "speech.tts.aivis_cloud.model_uuid": "speech.tts.model_uuid",
    "speech.tts.aivis_cloud.speaker_uuid": "speech.tts.speaker_uuid",
    "speech.tts.aivis_cloud.style_id": "speech.tts.style_id",
    "speech.tts.aivis_cloud.language": "speech.tts.language",
    "speech.tts.aivis_cloud.speaking_rate": "speech.tts.speaking_rate",
    "speech.tts.aivis_cloud.emotional_intensity": "speech.tts.emotional_intensity",
    "speech.tts.aivis_cloud.tempo_dynamics": "speech.tts.tempo_dynamics",
    "speech.tts.aivis_cloud.pitch": "speech.tts.pitch",
    "speech.tts.aivis_cloud.volume": "speech.tts.volume",
    "speech.tts.aivis_cloud.output_format": "speech.tts.output_format",
}
LEGACY_OPTIONAL_BASE_URL_DEFAULTS = {
    "llm.base_url": "https://openrouter.ai/api/v1",
    "llm.embedding_base_url": "https://openrouter.ai/api/v1",
}
LEGACY_AIVIS_RUNTIME_KEYS = tuple(
    legacy_key
    for current_key, legacy_key in LEGACY_SETTING_KEY_ALIASES.items()
    if current_key.startswith("speech.tts.aivis_cloud.")
)
# Block: Runtime settings helpers
def _merge_runtime_settings(default_settings: dict[str, Any], runtime_values: dict[str, Any]) -> dict[str, Any]:
    merged_settings = dict(default_settings)
    for key, value in runtime_values.items():
        if key in merged_settings:
            merged_settings[key] = value
    return merged_settings


def _normalize_runtime_settings_values(
    *,
    default_settings: dict[str, Any],
    runtime_values: dict[str, Any],
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in default_settings:
        if key in runtime_values:
            normalized[key] = _normalize_legacy_optional_base_url_value(
                key=key,
                value=runtime_values[key],
            )
            continue
        legacy_key = LEGACY_SETTING_KEY_ALIASES.get(key)
        if legacy_key is not None and legacy_key in runtime_values:
            normalized[key] = _normalize_legacy_optional_base_url_value(
                key=key,
                value=runtime_values[legacy_key],
            )
    if "speech.tts.provider" not in normalized:
        if any(legacy_key in runtime_values for legacy_key in LEGACY_AIVIS_RUNTIME_KEYS):
            normalized["speech.tts.provider"] = "aivis-cloud"
    return normalized


def _normalize_runtime_settings_updated_at(
    *,
    default_settings: dict[str, Any],
    current_updated_at: dict[str, Any],
    now_ms: int,
) -> dict[str, int]:
    normalized = _runtime_settings_seed_timestamps(now_ms)
    for key in default_settings:
        if key in current_updated_at:
            timestamp = current_updated_at[key]
        else:
            legacy_key = LEGACY_SETTING_KEY_ALIASES.get(key)
            timestamp = current_updated_at.get(legacy_key) if legacy_key is not None else None
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            continue
        normalized[key] = timestamp
    if "speech.tts.provider" not in current_updated_at:
        for legacy_key in LEGACY_AIVIS_RUNTIME_KEYS:
            timestamp = current_updated_at.get(legacy_key)
            if isinstance(timestamp, bool) or not isinstance(timestamp, int):
                continue
            normalized["speech.tts.provider"] = timestamp
            break
    return normalized


# Block: Legacy optional base URL normalization
def _normalize_legacy_optional_base_urls(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for key in LEGACY_OPTIONAL_BASE_URL_DEFAULTS:
        if key in normalized:
            normalized[key] = _normalize_legacy_optional_base_url_value(
                key=key,
                value=normalized[key],
            )
    return normalized


def _normalize_legacy_optional_base_url_value(*, key: str, value: Any) -> Any:
    legacy_default_value = LEGACY_OPTIONAL_BASE_URL_DEFAULTS.get(key)
    if (
        legacy_default_value is not None
        and isinstance(value, str)
        and value == legacy_default_value
    ):
        return ""
    return value


def _runtime_settings_seed_timestamps(now_ms: int) -> dict[str, int]:
    return {key: now_ms for key in build_default_settings()}


def _upsert_runtime_setting_value(
    *,
    connection: sqlite3.Connection,
    key: str,
    value: Any,
    applied_at: int,
) -> None:
    runtime_row = connection.execute(
        """
        SELECT values_json, value_updated_at_json
        FROM runtime_settings
        WHERE row_id = 1
        """
    ).fetchone()
    if runtime_row is None:
        raise RuntimeError("runtime_settings row is missing")
    values = json.loads(runtime_row["values_json"])
    value_updated_at = json.loads(runtime_row["value_updated_at_json"])
    values[key] = value
    value_updated_at[key] = applied_at
    connection.execute(
        """
        UPDATE runtime_settings
        SET values_json = ?,
            value_updated_at_json = ?,
            updated_at = ?
        WHERE row_id = 1
        """,
        (
            _json_text(values),
            _json_text(value_updated_at),
            applied_at,
        ),
    )


# Block: Generic helpers
def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _bounded_float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError("numeric value is required")
    return round(max(-1.0, min(1.0, float(value))), 2)


def _merged_unique_strings(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


def _opaque_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _quoted_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _preference_target_key(*, target_entity_ref: dict[str, Any]) -> str:
    target_key = target_entity_ref.get("target_key")
    if not isinstance(target_key, str) or not target_key:
        raise RuntimeError("preference target_entity_ref.target_key must be non-empty string")
    return target_key


def _normalized_target_entity_ref_json(target_entity_ref: dict[str, Any]) -> str:
    return json.dumps(
        target_entity_ref,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _string_list(value: Any, *, field_name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"{field_name} must be non-empty list")
    string_values: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise RuntimeError(f"{field_name} must contain only non-empty strings")
        string_values.append(item)
    return string_values


def _string_list_or_empty(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RuntimeError("string list value must be a list when present")
    string_values: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise RuntimeError("string list value must contain only non-empty strings")
        string_values.append(item)
    return string_values


def _now_ms() -> int:
    return int(time.time() * 1000)
