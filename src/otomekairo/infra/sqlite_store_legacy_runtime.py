"""Legacy settings migration and runtime helper functions for the SQLite state store."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from otomekairo.schema.settings import (
    build_character_preset_setting_keys,
    build_default_settings,
    build_default_settings_editor_presets,
    build_settings_editor_system_keys,
)


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
LEGACY_OUTPUT_PRESET_SETTING_KEYS = build_character_preset_setting_keys() + (
    "integrations.notify_route",
    "integrations.discord.bot_token",
    "integrations.discord.channel_id",
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


# Block: Legacy schema v5 editor seed
def _legacy_settings_editor_state_seed_v5(default_settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "active_behavior_preset_id": "preset_behavior_default",
        "active_llm_preset_id": "preset_llm_default",
        "active_memory_preset_id": "preset_memory_default",
        "active_output_preset_id": "preset_output_default",
        "system_values_json": {
            key: default_settings[key]
            for key in build_settings_editor_system_keys()
        },
        "revision": 1,
    }


# Block: Legacy output payload seed
def _build_legacy_output_preset_payload(default_settings: dict[str, Any]) -> dict[str, Any]:
    payload = {
        key: default_settings[key]
        for key in LEGACY_OUTPUT_PRESET_SETTING_KEYS
        if key in default_settings
    }
    payload["integrations.notify_route"] = str(default_settings["integrations.notify_route"])
    payload["integrations.discord.bot_token"] = str(default_settings["integrations.discord.bot_token"])
    payload["integrations.discord.channel_id"] = str(default_settings["integrations.discord.channel_id"])
    return payload


# Block: Legacy preset seed export
def _legacy_settings_preset_seeds_from_defaults(default_settings: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return (
        {
            "preset_id": "preset_behavior_default",
            "preset_kind": "behavior",
            "preset_name": "標準",
            "payload": {
                "behavior.second_person_label": str(default_settings["behavior.second_person_label"]),
                "behavior.system_prompt": str(default_settings["behavior.system_prompt"]),
                "behavior.addon_prompt": str(default_settings["behavior.addon_prompt"]),
                "behavior.response_pace": str(default_settings["behavior.response_pace"]),
                "behavior.proactivity_level": str(default_settings["behavior.proactivity_level"]),
                "behavior.browse_preference": str(default_settings["behavior.browse_preference"]),
                "behavior.notify_preference": str(default_settings["behavior.notify_preference"]),
                "behavior.speech_style": str(default_settings["behavior.speech_style"]),
                "behavior.verbosity_bias": str(default_settings["behavior.verbosity_bias"]),
            },
        },
        {
            "preset_id": "preset_llm_default",
            "preset_kind": "llm",
            "preset_name": "標準",
            "payload": {
                "llm.model": str(default_settings["llm.model"]),
                "llm.temperature": float(default_settings["llm.temperature"]),
                "llm.max_output_tokens": int(default_settings["llm.max_output_tokens"]),
                "llm.api_key": str(default_settings["llm.api_key"]),
                "llm.base_url": str(default_settings["llm.base_url"]),
            },
        },
        {
            "preset_id": "preset_memory_default",
            "preset_kind": "memory",
            "preset_name": "標準",
            "payload": {
                "llm.embedding_model": str(default_settings["llm.embedding_model"]),
                "llm.embedding_api_key": str(default_settings["llm.embedding_api_key"]),
                "llm.embedding_base_url": str(default_settings["llm.embedding_base_url"]),
                "runtime.context_budget_tokens": int(default_settings["runtime.context_budget_tokens"]),
                "retrieval_profile": {
                    "semantic_top_k": 8,
                    "recent_window_limit": 5,
                    "fact_bias": 0.7,
                    "summary_bias": 0.6,
                    "event_bias": 0.4,
                },
            },
        },
        {
            "preset_id": "preset_output_default",
            "preset_kind": "output",
            "preset_name": "新規キャラクター",
            "payload": _build_legacy_output_preset_payload(default_settings),
        },
    )


# Block: Active legacy output payload for migration
def _active_legacy_output_payload_for_migration(
    *,
    legacy_presets_by_kind: dict[str, list[sqlite3.Row]],
    active_output_preset_id: str | None,
    default_settings: dict[str, Any],
) -> dict[str, Any]:
    output_rows = legacy_presets_by_kind.get("output", [])
    target_row = None
    if active_output_preset_id is not None:
        target_row = next(
            (row for row in output_rows if str(row["preset_id"]) == active_output_preset_id),
            None,
        )
    if target_row is None and output_rows:
        target_row = output_rows[0]
    if target_row is None:
        return _build_legacy_output_preset_payload(default_settings)
    raw_payload = json.loads(target_row["payload_json"])
    if not isinstance(raw_payload, dict):
        return _build_legacy_output_preset_payload(default_settings)
    return _normalize_legacy_output_preset_payload(
        preset_kind="output",
        payload=raw_payload,
    )


# Block: V8 system values migration
def _build_v8_system_values_from_v7_row(
    *,
    legacy_editor_row: sqlite3.Row | None,
    active_output_payload: dict[str, Any],
    default_settings: dict[str, Any],
) -> dict[str, Any]:
    system_values = {
        key: default_settings[key]
        for key in build_settings_editor_system_keys()
    }
    if legacy_editor_row is not None:
        row_keys = set(legacy_editor_row.keys())
        if "system_values_json" in row_keys and legacy_editor_row["system_values_json"] is not None:
            raw_system_values = json.loads(legacy_editor_row["system_values_json"])
            if isinstance(raw_system_values, dict):
                for key in build_settings_editor_system_keys():
                    if key in raw_system_values:
                        system_values[key] = raw_system_values[key]
        if "direct_values_json" in row_keys and legacy_editor_row["direct_values_json"] is not None:
            raw_direct_values = json.loads(legacy_editor_row["direct_values_json"])
            if isinstance(raw_direct_values, dict):
                for key in build_settings_editor_system_keys():
                    if key in raw_direct_values:
                        system_values[key] = raw_direct_values[key]
    for key in (
        "integrations.notify_route",
        "integrations.discord.bot_token",
        "integrations.discord.channel_id",
    ):
        if key in active_output_payload:
            system_values[key] = active_output_payload[key]
    return system_values


# Block: Migrated preset insert
def _insert_migrated_editor_presets(
    *,
    connection: sqlite3.Connection,
    table_name: str,
    preset_rows: list[sqlite3.Row],
    payload_builder: Any,
    fallback_entries: tuple[dict[str, Any], ...],
    now_ms: int,
) -> None:
    if preset_rows:
        for preset_row in preset_rows:
            raw_payload = json.loads(preset_row["payload_json"])
            payload = payload_builder(raw_payload if isinstance(raw_payload, dict) else {})
            connection.execute(
                f"""
                INSERT INTO {table_name} (
                    preset_id,
                    preset_name,
                    payload_json,
                    archived,
                    sort_order,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(preset_row["preset_id"]),
                    str(preset_row["preset_name"]),
                    _json_text(payload),
                    int(preset_row["archived"]),
                    int(preset_row["sort_order"]),
                    int(preset_row["created_at"]),
                    int(preset_row["updated_at"]),
                ),
            )
        return
    for index, preset_entry in enumerate(fallback_entries):
        connection.execute(
            f"""
            INSERT INTO {table_name} (
                preset_id,
                preset_name,
                payload_json,
                archived,
                sort_order,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, 0, ?, ?, ?)
            """,
            (
                preset_entry["preset_id"],
                preset_entry["preset_name"],
                _json_text(preset_entry["payload"]),
                (index + 1) * 10,
                now_ms,
                now_ms,
            ),
        )


# Block: Payload migration helpers
def _migrate_output_payload_to_character_payload(
    *,
    legacy_payload: dict[str, Any],
    default_settings: dict[str, Any],
) -> dict[str, Any]:
    normalized_legacy_payload = _normalize_legacy_output_preset_payload(
        preset_kind="output",
        payload=legacy_payload,
    )
    character_payload = {
        key: default_settings[key]
        for key in build_character_preset_setting_keys()
    }
    for key in character_payload:
        if key in normalized_legacy_payload:
            character_payload[key] = normalized_legacy_payload[key]
    return character_payload


def _migrate_behavior_payload_to_v8(
    *,
    legacy_payload: dict[str, Any],
    default_settings: dict[str, Any],
) -> dict[str, Any]:
    return _normalize_legacy_behavior_preset_payload(
        preset_kind="behavior",
        payload=legacy_payload,
        default_settings=default_settings,
    )


def _migrate_llm_payload_to_conversation_payload(
    *,
    legacy_payload: dict[str, Any],
    default_settings: dict[str, Any],
) -> dict[str, Any]:
    payload = _deep_copy_jsonable(
        build_default_settings_editor_presets(default_settings)["conversation_presets"][0]["payload"]
    )
    normalized_legacy_payload = _normalize_legacy_optional_base_urls(legacy_payload)
    for key in (
        "llm.model",
        "llm.api_key",
        "llm.base_url",
        "llm.temperature",
        "llm.max_output_tokens",
    ):
        if key in normalized_legacy_payload:
            payload[key] = normalized_legacy_payload[key]
    return payload


def _migrate_memory_payload_to_v8(
    *,
    legacy_payload: dict[str, Any],
    default_settings: dict[str, Any],
) -> dict[str, Any]:
    payload = _deep_copy_jsonable(
        build_default_settings_editor_presets(default_settings)["memory_presets"][0]["payload"]
    )
    normalized_legacy_payload = _normalize_legacy_optional_base_urls(legacy_payload)
    for key in (
        "llm.embedding_model",
        "llm.embedding_api_key",
        "llm.embedding_base_url",
        "runtime.context_budget_tokens",
    ):
        if key in normalized_legacy_payload:
            payload[key] = normalized_legacy_payload[key]
    retrieval_profile = normalized_legacy_payload.get("retrieval_profile")
    if isinstance(retrieval_profile, dict):
        payload["retrieval_profile"] = retrieval_profile
    return payload


def _deep_copy_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value))


# Block: Behavior preset migration helper
def _normalize_legacy_behavior_preset_payload(
    *,
    preset_kind: str,
    payload: dict[str, Any],
    default_settings: dict[str, Any],
) -> dict[str, Any]:
    if preset_kind != "behavior":
        return payload
    normalized = {
        "behavior.second_person_label": str(default_settings["behavior.second_person_label"]),
        "behavior.system_prompt": str(default_settings["behavior.system_prompt"]),
        "behavior.addon_prompt": str(default_settings["behavior.addon_prompt"]),
        "behavior.response_pace": str(default_settings["behavior.response_pace"]),
        "behavior.proactivity_level": str(default_settings["behavior.proactivity_level"]),
        "behavior.browse_preference": str(default_settings["behavior.browse_preference"]),
        "behavior.notify_preference": str(default_settings["behavior.notify_preference"]),
        "behavior.speech_style": str(default_settings["behavior.speech_style"]),
        "behavior.verbosity_bias": str(default_settings["behavior.verbosity_bias"]),
    }
    if set(payload) == set(normalized):
        return payload
    current_key_map = {
        "behavior.second_person_label": "behavior.second_person_label",
        "behavior.system_prompt": "behavior.system_prompt",
        "behavior.addon_prompt": "behavior.addon_prompt",
        "behavior.response_pace": "behavior.response_pace",
        "behavior.proactivity_level": "behavior.proactivity_level",
        "behavior.browse_preference": "behavior.browse_preference",
        "behavior.notify_preference": "behavior.notify_preference",
        "behavior.speech_style": "behavior.speech_style",
        "behavior.verbosity_bias": "behavior.verbosity_bias",
        "response_pace": "behavior.response_pace",
        "proactivity_level": "behavior.proactivity_level",
        "browse_preference": "behavior.browse_preference",
        "notify_preference": "behavior.notify_preference",
        "speech_style": "behavior.speech_style",
        "verbosity_bias": "behavior.verbosity_bias",
        "second_person_label": "behavior.second_person_label",
        "system_prompt": "behavior.system_prompt",
        "addon_prompt": "behavior.addon_prompt",
    }
    for source_key, target_key in current_key_map.items():
        if source_key in payload:
            normalized[target_key] = payload[source_key]
    response_pace_map = {
        "calm": "careful",
        "normal": "balanced",
    }
    speech_style_map = {
        "soft": "gentle",
        "formal": "firm",
    }
    response_pace = normalized["behavior.response_pace"]
    if response_pace in response_pace_map:
        normalized["behavior.response_pace"] = response_pace_map[response_pace]
    speech_style = normalized["behavior.speech_style"]
    if speech_style in speech_style_map:
        normalized["behavior.speech_style"] = speech_style_map[speech_style]
    return normalized


# Block: Output preset migration helper
def _normalize_legacy_output_preset_payload(
    *,
    preset_kind: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if preset_kind != "output":
        return payload
    if set(payload) == set(LEGACY_OUTPUT_PRESET_SETTING_KEYS):
        return payload
    default_settings = build_default_settings()
    normalized = _build_legacy_output_preset_payload(default_settings)
    for key in LEGACY_OUTPUT_PRESET_SETTING_KEYS:
        value = payload.get(key)
        if value is not None:
            normalized[key] = value
    legacy_aivis_key_map = {
        "speech.tts.api_key": "speech.tts.aivis_cloud.api_key",
        "speech.tts.endpoint_url": "speech.tts.aivis_cloud.endpoint_url",
        "speech.tts.model_uuid": "speech.tts.aivis_cloud.model_uuid",
        "speech.tts.speaker_uuid": "speech.tts.aivis_cloud.speaker_uuid",
        "speech.tts.style_id": "speech.tts.aivis_cloud.style_id",
        "speech.tts.language": "speech.tts.aivis_cloud.language",
        "speech.tts.speaking_rate": "speech.tts.aivis_cloud.speaking_rate",
        "speech.tts.emotional_intensity": "speech.tts.aivis_cloud.emotional_intensity",
        "speech.tts.tempo_dynamics": "speech.tts.aivis_cloud.tempo_dynamics",
        "speech.tts.pitch": "speech.tts.aivis_cloud.pitch",
        "speech.tts.volume": "speech.tts.aivis_cloud.volume",
        "speech.tts.output_format": "speech.tts.aivis_cloud.output_format",
    }
    saw_legacy_aivis_key = False
    for legacy_key, normalized_key in legacy_aivis_key_map.items():
        if legacy_key in payload:
            normalized[normalized_key] = payload[legacy_key]
            saw_legacy_aivis_key = True
    if "speech.tts.enabled" in payload:
        normalized["speech.tts.enabled"] = payload["speech.tts.enabled"]
    if saw_legacy_aivis_key:
        normalized["speech.tts.provider"] = "aivis-cloud"
    required_tts_keys = (
        "speech.tts.aivis_cloud.api_key",
        "speech.tts.aivis_cloud.endpoint_url",
        "speech.tts.aivis_cloud.model_uuid",
        "speech.tts.aivis_cloud.speaker_uuid",
    )
    legacy_output_mode = payload.get("output.mode")
    if legacy_output_mode == "ui_only":
        normalized["speech.tts.enabled"] = False
    elif legacy_output_mode == "ui_and_tts":
        normalized["speech.tts.enabled"] = all(
            isinstance(normalized[key], str) and normalized[key].strip()
            for key in required_tts_keys
        )
        normalized["speech.tts.provider"] = "aivis-cloud"
    legacy_notify_route = payload.get("integrations.notify_route")
    if legacy_notify_route in {"ui_only", "discord"}:
        normalized["integrations.notify_route"] = legacy_notify_route
    legacy_discord_token = payload.get("integrations.discord.bot_token")
    if isinstance(legacy_discord_token, str):
        normalized["integrations.discord.bot_token"] = legacy_discord_token
    legacy_discord_channel = payload.get("integrations.discord.channel_id")
    if isinstance(legacy_discord_channel, str):
        normalized["integrations.discord.channel_id"] = legacy_discord_channel
    if normalized["speech.tts.enabled"] is True:
        if not all(
            isinstance(normalized[key], str) and normalized[key].strip()
            for key in required_tts_keys
        ):
            normalized["speech.tts.enabled"] = False
    return normalized


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
