"""Setting registry and validation rules."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


# Block: Validation errors
class SettingsValidationError(ValueError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


# Block: Setting definition
@dataclass(frozen=True, slots=True)
class SettingDefinition:
    key: str
    value_type: str
    apply_scopes: tuple[str, ...]
    min_value: int | float | None = None
    max_value: int | float | None = None
    min_length: int | None = None
    max_length: int | None = None


# Block: Registry source
SETTING_DEFINITIONS: tuple[SettingDefinition, ...] = (
    SettingDefinition("llm.model", "string", ("runtime", "next_boot"), min_length=1, max_length=256),
    SettingDefinition("llm.api_key", "string", ("runtime", "next_boot"), min_length=0, max_length=4096),
    SettingDefinition("llm.base_url", "string", ("runtime", "next_boot"), min_length=0, max_length=512),
    SettingDefinition("llm.embedding_model", "string", ("runtime", "next_boot"), min_length=1, max_length=256),
    SettingDefinition("llm.embedding_api_key", "string", ("runtime", "next_boot"), min_length=0, max_length=4096),
    SettingDefinition("llm.embedding_base_url", "string", ("runtime", "next_boot"), min_length=0, max_length=512),
    SettingDefinition("llm.temperature", "number", ("runtime", "next_boot"), min_value=0.0, max_value=2.0),
    SettingDefinition("llm.max_output_tokens", "integer", ("runtime", "next_boot"), min_value=256, max_value=8192),
    SettingDefinition("runtime.idle_tick_ms", "integer", ("runtime", "next_boot"), min_value=250, max_value=60000),
    SettingDefinition("runtime.long_cycle_min_interval_ms", "integer", ("runtime", "next_boot"), min_value=1000, max_value=300000),
    SettingDefinition("runtime.context_budget_tokens", "integer", ("runtime", "next_boot"), min_value=1024, max_value=32768),
    SettingDefinition("sensors.camera.enabled", "boolean", ("runtime",)),
    SettingDefinition("sensors.microphone.enabled", "boolean", ("runtime",)),
    SettingDefinition("output.tts.enabled", "boolean", ("runtime",)),
    SettingDefinition("output.tts.voice", "string", ("runtime", "next_boot"), min_length=1, max_length=128),
    SettingDefinition("output.mode", "string", ("runtime", "next_boot"), min_length=1, max_length=64),
    SettingDefinition("integrations.notify_route", "string", ("runtime", "next_boot"), min_length=1, max_length=64),
    SettingDefinition("integrations.sns.enabled", "boolean", ("runtime",)),
    SettingDefinition("integrations.discord.bot_token", "string", ("runtime", "next_boot"), min_length=0, max_length=4096),
    SettingDefinition("integrations.discord.channel_id", "string", ("runtime", "next_boot"), min_length=0, max_length=256),
)


# Block: Registry index
SETTING_DEFINITION_MAP = {definition.key: definition for definition in SETTING_DEFINITIONS}


# Block: Editor setting constants
SETTINGS_EDITOR_PRESET_KINDS = ("behavior", "llm", "memory", "output")
SETTINGS_EDITOR_SYSTEM_KEYS = (
    "runtime.idle_tick_ms",
    "runtime.long_cycle_min_interval_ms",
    "sensors.microphone.enabled",
    "sensors.camera.enabled",
    "output.tts.enabled",
    "integrations.sns.enabled",
)


# Block: Editor preset identifiers
DEFAULT_SETTINGS_EDITOR_PRESET_IDS = {
    "behavior": "preset_behavior_balanced",
    "llm": "preset_llm_default",
    "memory": "preset_memory_balanced",
    "output": "preset_output_default",
}


# Block: Public registry helpers
def get_setting_definition(key: str) -> SettingDefinition:
    definition = SETTING_DEFINITION_MAP.get(key)
    if definition is None:
        raise SettingsValidationError("unknown_settings_key", f"unknown settings key: {key}")
    return definition


# Block: Defaults export
def build_default_settings() -> dict[str, Any]:
    return dict(_read_default_settings_from_config())


# Block: System key export
def build_settings_editor_system_keys() -> tuple[str, ...]:
    return SETTINGS_EDITOR_SYSTEM_KEYS


# Block: Preset kind export
def build_settings_editor_preset_kinds() -> tuple[str, ...]:
    return SETTINGS_EDITOR_PRESET_KINDS


# Block: Editor state seed
def build_default_settings_editor_state(default_settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "active_behavior_preset_id": DEFAULT_SETTINGS_EDITOR_PRESET_IDS["behavior"],
        "active_llm_preset_id": DEFAULT_SETTINGS_EDITOR_PRESET_IDS["llm"],
        "active_memory_preset_id": DEFAULT_SETTINGS_EDITOR_PRESET_IDS["memory"],
        "active_output_preset_id": DEFAULT_SETTINGS_EDITOR_PRESET_IDS["output"],
        "active_camera_connection_id": None,
        "system_values_json": {
            key: default_settings[key]
            for key in SETTINGS_EDITOR_SYSTEM_KEYS
        },
        "revision": 1,
    }


# Block: Preset seed export
def build_default_settings_presets(default_settings: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    return (
        {
            "preset_id": "preset_behavior_balanced",
            "preset_kind": "behavior",
            "preset_name": "標準",
            "payload": {
                "response_pace": "normal",
                "proactivity_level": "medium",
                "browse_preference": "balanced",
                "notify_preference": "balanced",
                "speech_style": "neutral",
                "verbosity_bias": "balanced",
            },
        },
        {
            "preset_id": "preset_behavior_quiet",
            "preset_kind": "behavior",
            "preset_name": "静かめ",
            "payload": {
                "response_pace": "calm",
                "proactivity_level": "low",
                "browse_preference": "avoid",
                "notify_preference": "quiet",
                "speech_style": "soft",
                "verbosity_bias": "short",
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
            "preset_id": "preset_llm_precise",
            "preset_kind": "llm",
            "preset_name": "低温度",
            "payload": {
                "llm.model": str(default_settings["llm.model"]),
                "llm.temperature": 0.3,
                "llm.max_output_tokens": 1536,
                "llm.api_key": str(default_settings["llm.api_key"]),
                "llm.base_url": str(default_settings["llm.base_url"]),
            },
        },
        {
            "preset_id": "preset_memory_balanced",
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
            "preset_id": "preset_memory_dense",
            "preset_kind": "memory",
            "preset_name": "深め",
            "payload": {
                "llm.embedding_model": str(default_settings["llm.embedding_model"]),
                "llm.embedding_api_key": str(default_settings["llm.embedding_api_key"]),
                "llm.embedding_base_url": str(default_settings["llm.embedding_base_url"]),
                "runtime.context_budget_tokens": 12288,
                "retrieval_profile": {
                    "semantic_top_k": 12,
                    "recent_window_limit": 6,
                    "fact_bias": 0.85,
                    "summary_bias": 0.55,
                    "event_bias": 0.35,
                },
            },
        },
        {
            "preset_id": "preset_output_default",
            "preset_kind": "output",
            "preset_name": "標準",
            "payload": {
                "output.tts.voice": str(default_settings["output.tts.voice"]),
                "output.mode": str(default_settings["output.mode"]),
                "integrations.notify_route": str(default_settings["integrations.notify_route"]),
                "integrations.discord.bot_token": str(default_settings["integrations.discord.bot_token"]),
                "integrations.discord.channel_id": str(default_settings["integrations.discord.channel_id"]),
            },
        },
        {
            "preset_id": "preset_output_quiet",
            "preset_kind": "output",
            "preset_name": "UIのみ",
            "payload": {
                "output.tts.voice": str(default_settings["output.tts.voice"]),
                "output.mode": "ui_only",
                "integrations.notify_route": "ui_only",
                "integrations.discord.bot_token": str(default_settings["integrations.discord.bot_token"]),
                "integrations.discord.channel_id": str(default_settings["integrations.discord.channel_id"]),
            },
        },
    )


# Block: Camera connection seed export
def build_default_camera_connections() -> tuple[dict[str, Any], ...]:
    return ()


# Block: Editor payload normalization
def normalize_settings_editor_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise SettingsValidationError("invalid_settings_editor_document", "settings editor payload must be an object")
    expected_keys = {"editor_state", "preset_catalogs"}
    if set(document) != expected_keys:
        raise SettingsValidationError(
            "invalid_settings_editor_document",
            "settings editor payload keys do not match fixed shape",
        )
    editor_state = _normalize_editor_state(document.get("editor_state"))
    preset_catalogs = _normalize_preset_catalogs(document.get("preset_catalogs"))
    camera_connections = _normalize_camera_connections(document.get("camera_connections"))
    _validate_active_preset_ids(editor_state=editor_state, preset_catalogs=preset_catalogs)
    _validate_active_camera_connection_id(
        editor_state=editor_state,
        camera_connections=camera_connections,
    )
    return {
        "editor_state": editor_state,
        "preset_catalogs": preset_catalogs,
        "camera_connections": camera_connections,
    }


# Block: Value normalization
def normalize_requested_value(key: str, requested_value: Any, apply_scope: str) -> dict[str, Any]:
    definition = get_setting_definition(key)
    if apply_scope not in definition.apply_scopes:
        raise SettingsValidationError(
            "invalid_settings_scope",
            f"invalid apply_scope for {key}: {apply_scope}",
        )
    _validate_type(definition, requested_value)
    _validate_range(definition, requested_value)
    _validate_length(definition, requested_value)
    return {"value_type": definition.value_type, "value": requested_value}


# Block: Normalized value decode
def decode_requested_value(key: str, requested_value_json: dict[str, Any]) -> Any:
    if not isinstance(requested_value_json, dict):
        raise SettingsValidationError("invalid_settings_value", f"{key} payload must be object")
    if "value_type" not in requested_value_json or "value" not in requested_value_json:
        raise SettingsValidationError("invalid_settings_value", f"{key} payload is incomplete")
    definition = get_setting_definition(key)
    if requested_value_json["value_type"] != definition.value_type:
        raise SettingsValidationError("invalid_settings_value", f"{key} payload type does not match definition")
    requested_value = requested_value_json["value"]
    _validate_type(definition, requested_value)
    _validate_range(definition, requested_value)
    _validate_length(definition, requested_value)
    return requested_value


# Block: Type validation
def _validate_type(definition: SettingDefinition, requested_value: Any) -> None:
    value_type = definition.value_type
    if value_type == "string":
        if not isinstance(requested_value, str):
            raise SettingsValidationError("invalid_settings_value", f"{definition.key} must be string")
        return
    if value_type == "boolean":
        if not isinstance(requested_value, bool):
            raise SettingsValidationError("invalid_settings_value", f"{definition.key} must be boolean")
        return
    if value_type == "integer":
        if isinstance(requested_value, bool) or not isinstance(requested_value, int):
            raise SettingsValidationError("invalid_settings_value", f"{definition.key} must be integer")
        return
    if value_type == "number":
        if isinstance(requested_value, bool) or not isinstance(requested_value, (int, float)):
            raise SettingsValidationError("invalid_settings_value", f"{definition.key} must be number")
        return
    raise SettingsValidationError("invalid_settings_value", f"unsupported value_type for {definition.key}")


# Block: Numeric range validation
def _validate_range(definition: SettingDefinition, requested_value: Any) -> None:
    if definition.value_type not in {"integer", "number"}:
        return
    numeric_value = float(requested_value)
    if definition.min_value is not None and numeric_value < float(definition.min_value):
        raise SettingsValidationError("invalid_settings_value", f"{definition.key} is below minimum")
    if definition.max_value is not None and numeric_value > float(definition.max_value):
        raise SettingsValidationError("invalid_settings_value", f"{definition.key} is above maximum")


# Block: String length validation
def _validate_length(definition: SettingDefinition, requested_value: Any) -> None:
    if definition.value_type != "string":
        return
    if definition.min_length is not None and len(requested_value) < definition.min_length:
        raise SettingsValidationError("invalid_settings_value", f"{definition.key} is too short")
    if definition.max_length is not None and len(requested_value) > definition.max_length:
        raise SettingsValidationError("invalid_settings_value", f"{definition.key} is too long")


# Block: Editor state normalization
def _normalize_editor_state(editor_state: Any) -> dict[str, Any]:
    if not isinstance(editor_state, dict):
        raise SettingsValidationError("invalid_settings_editor_document", "editor_state must be an object")
    normalized_revision = editor_state.get("revision")
    if isinstance(normalized_revision, bool) or not isinstance(normalized_revision, int):
        raise SettingsValidationError("invalid_settings_editor_document", "editor_state.revision must be integer")
    system_values = _normalize_system_values(editor_state.get("system_values"))
    normalized_editor_state = {
        "revision": normalized_revision,
        "active_behavior_preset_id": _required_string(
            editor_state.get("active_behavior_preset_id"),
            "editor_state.active_behavior_preset_id",
        ),
        "active_llm_preset_id": _required_string(
            editor_state.get("active_llm_preset_id"),
            "editor_state.active_llm_preset_id",
        ),
        "active_memory_preset_id": _required_string(
            editor_state.get("active_memory_preset_id"),
            "editor_state.active_memory_preset_id",
        ),
        "active_output_preset_id": _required_string(
            editor_state.get("active_output_preset_id"),
            "editor_state.active_output_preset_id",
        ),
        "active_camera_connection_id": _optional_string(
            editor_state.get("active_camera_connection_id"),
            "editor_state.active_camera_connection_id",
        ),
        "system_values": system_values,
    }
    return normalized_editor_state


# Block: System values normalization
def _normalize_system_values(system_values: Any) -> dict[str, Any]:
    if not isinstance(system_values, dict):
        raise SettingsValidationError("invalid_settings_editor_document", "editor_state.system_values must be an object")
    expected_keys = set(SETTINGS_EDITOR_SYSTEM_KEYS)
    actual_keys = set(system_values)
    if actual_keys != expected_keys:
        raise SettingsValidationError(
            "invalid_settings_editor_document",
            "editor_state.system_values keys do not match system key set",
        )
    normalized: dict[str, Any] = {}
    for key in SETTINGS_EDITOR_SYSTEM_KEYS:
        definition = get_setting_definition(key)
        value = system_values[key]
        _validate_type(definition, value)
        _validate_range(definition, value)
        _validate_length(definition, value)
        normalized[key] = value
    return normalized


# Block: Camera connections normalization
def _normalize_camera_connections(camera_connections: Any) -> list[dict[str, Any]]:
    if not isinstance(camera_connections, list):
        raise SettingsValidationError("invalid_settings_editor_document", "camera_connections must be an array")
    normalized_connections: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for camera_connection in camera_connections:
        if not isinstance(camera_connection, dict):
            raise SettingsValidationError("invalid_settings_editor_document", "camera_connections entries must be objects")
        camera_connection_id = _required_string(
            camera_connection.get("camera_connection_id"),
            "camera_connections.camera_connection_id",
        )
        if camera_connection_id in seen_ids:
            raise SettingsValidationError("invalid_settings_editor_document", "camera_connections contains duplicate camera_connection_id")
        seen_ids.add(camera_connection_id)
        sort_order = camera_connection.get("sort_order")
        updated_at = camera_connection.get("updated_at")
        if isinstance(sort_order, bool) or not isinstance(sort_order, int):
            raise SettingsValidationError("invalid_settings_editor_document", "camera_connections.sort_order must be integer")
        if isinstance(updated_at, bool) or not isinstance(updated_at, int):
            raise SettingsValidationError("invalid_settings_editor_document", "camera_connections.updated_at must be integer")
        normalized_connections.append(
            {
                "camera_connection_id": camera_connection_id,
                "display_name": _required_string(
                    camera_connection.get("display_name"),
                    "camera_connections.display_name",
                ),
                "host": _string_value(
                    camera_connection.get("host"),
                    "camera_connections.host",
                ),
                "username": _string_value(
                    camera_connection.get("username"),
                    "camera_connections.username",
                ),
                "password": _string_value(
                    camera_connection.get("password"),
                    "camera_connections.password",
                ),
                "sort_order": sort_order,
                "updated_at": updated_at,
            }
        )
    return normalized_connections


# Block: Preset catalog normalization
def _normalize_preset_catalogs(preset_catalogs: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(preset_catalogs, dict):
        raise SettingsValidationError("invalid_settings_editor_document", "preset_catalogs must be an object")
    expected_kinds = set(SETTINGS_EDITOR_PRESET_KINDS)
    actual_kinds = set(preset_catalogs)
    if actual_kinds != expected_kinds:
        raise SettingsValidationError(
            "invalid_settings_editor_document",
            "preset_catalogs kinds do not match preset kinds",
        )
    normalized_catalogs: dict[str, list[dict[str, Any]]] = {}
    for preset_kind in SETTINGS_EDITOR_PRESET_KINDS:
        preset_entries = preset_catalogs[preset_kind]
        if not isinstance(preset_entries, list) or not preset_entries:
            raise SettingsValidationError(
                "invalid_settings_editor_document",
                f"preset_catalogs.{preset_kind} must be a non-empty array",
            )
        normalized_entries: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for preset_entry in preset_entries:
            normalized_entry = _normalize_preset_entry(
                preset_kind=preset_kind,
                preset_entry=preset_entry,
            )
            preset_id = str(normalized_entry["preset_id"])
            if preset_id in seen_ids:
                raise SettingsValidationError(
                    "invalid_settings_editor_document",
                    f"preset_catalogs.{preset_kind} contains duplicate preset_id",
                )
            seen_ids.add(preset_id)
            normalized_entries.append(normalized_entry)
        normalized_catalogs[preset_kind] = normalized_entries
    return normalized_catalogs


# Block: Preset entry normalization
def _normalize_preset_entry(*, preset_kind: str, preset_entry: Any) -> dict[str, Any]:
    if not isinstance(preset_entry, dict):
        raise SettingsValidationError(
            "invalid_settings_editor_document",
            f"preset_catalogs.{preset_kind} entries must be objects",
        )
    archived = preset_entry.get("archived")
    sort_order = preset_entry.get("sort_order")
    updated_at = preset_entry.get("updated_at")
    if not isinstance(archived, bool):
        raise SettingsValidationError(
            "invalid_settings_editor_document",
            f"preset_catalogs.{preset_kind}.archived must be boolean",
        )
    if isinstance(sort_order, bool) or not isinstance(sort_order, int):
        raise SettingsValidationError(
            "invalid_settings_editor_document",
            f"preset_catalogs.{preset_kind}.sort_order must be integer",
        )
    if isinstance(updated_at, bool) or not isinstance(updated_at, int):
        raise SettingsValidationError(
            "invalid_settings_editor_document",
            f"preset_catalogs.{preset_kind}.updated_at must be integer",
        )
    payload = _normalize_preset_payload(
        preset_kind=preset_kind,
        payload=preset_entry.get("payload"),
    )
    return {
        "preset_id": _required_string(
            preset_entry.get("preset_id"),
            f"preset_catalogs.{preset_kind}.preset_id",
        ),
        "preset_name": _required_string(
            preset_entry.get("preset_name"),
            f"preset_catalogs.{preset_kind}.preset_name",
        ),
        "archived": archived,
        "sort_order": sort_order,
        "updated_at": updated_at,
        "payload": payload,
    }


# Block: Preset payload normalization
def _normalize_preset_payload(*, preset_kind: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SettingsValidationError(
            "invalid_settings_editor_document",
            f"{preset_kind} preset payload must be an object",
        )
    if preset_kind == "behavior":
        return _normalize_behavior_preset_payload(payload)
    if preset_kind == "llm":
        return _normalize_keyed_preset_payload(
            payload=payload,
            required_keys=(
                "llm.model",
                "llm.temperature",
                "llm.max_output_tokens",
                "llm.api_key",
                "llm.base_url",
            ),
        )
    if preset_kind == "memory":
        return _normalize_memory_preset_payload(payload)
    if preset_kind == "output":
        return _normalize_output_preset_payload(payload)
    raise SettingsValidationError("invalid_settings_editor_document", f"unsupported preset_kind: {preset_kind}")


# Block: Behavior preset normalization
def _normalize_behavior_preset_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed_value_sets = {
        "response_pace": {"calm", "normal", "quick"},
        "proactivity_level": {"low", "medium", "high"},
        "browse_preference": {"avoid", "balanced", "prefer"},
        "notify_preference": {"quiet", "balanced", "proactive"},
        "speech_style": {"soft", "neutral", "formal"},
        "verbosity_bias": {"short", "balanced", "detailed"},
    }
    if set(payload) != set(allowed_value_sets):
        raise SettingsValidationError("invalid_settings_editor_document", "behavior preset keys do not match fixed shape")
    normalized: dict[str, Any] = {}
    for key, allowed_values in allowed_value_sets.items():
        value = _required_string(payload.get(key), f"behavior payload {key}")
        if value not in allowed_values:
            raise SettingsValidationError("invalid_settings_editor_document", f"behavior payload {key} is invalid")
        normalized[key] = value
    return normalized


# Block: Memory preset normalization
def _normalize_memory_preset_payload(payload: dict[str, Any]) -> dict[str, Any]:
    required_keys = {
        "llm.embedding_model",
        "llm.embedding_api_key",
        "llm.embedding_base_url",
        "runtime.context_budget_tokens",
        "retrieval_profile",
    }
    if set(payload) != required_keys:
        raise SettingsValidationError("invalid_settings_editor_document", "memory preset keys do not match fixed shape")
    normalized = _normalize_keyed_preset_payload(
        payload={
            key: value
            for key, value in payload.items()
            if key != "retrieval_profile"
        },
        required_keys=(
            "llm.embedding_model",
            "llm.embedding_api_key",
            "llm.embedding_base_url",
            "runtime.context_budget_tokens",
        ),
    )
    normalized["retrieval_profile"] = _normalize_retrieval_profile(payload["retrieval_profile"])
    return normalized


# Block: Output preset normalization
def _normalize_output_preset_payload(payload: dict[str, Any]) -> dict[str, Any]:
    required_keys = {
        "output.tts.voice",
        "output.mode",
        "integrations.notify_route",
        "integrations.discord.bot_token",
        "integrations.discord.channel_id",
    }
    if set(payload) != required_keys:
        raise SettingsValidationError("invalid_settings_editor_document", "output preset keys do not match fixed shape")
    normalized = _normalize_keyed_preset_payload(
        payload=payload,
        required_keys=(
            "output.tts.voice",
            "output.mode",
            "integrations.notify_route",
            "integrations.discord.bot_token",
            "integrations.discord.channel_id",
        ),
    )
    if normalized["output.mode"] not in {"ui_only", "ui_and_tts"}:
        raise SettingsValidationError("invalid_settings_editor_document", "output.mode is invalid")
    if normalized["integrations.notify_route"] not in {"ui_only", "discord"}:
        raise SettingsValidationError("invalid_settings_editor_document", "integrations.notify_route is invalid")
    if normalized["integrations.notify_route"] == "discord":
        if not normalized["integrations.discord.bot_token"] or not normalized["integrations.discord.channel_id"]:
            raise SettingsValidationError("invalid_settings_editor_document", "discord route requires discord credentials")
    return normalized


# Block: Retrieval profile normalization
def _normalize_retrieval_profile(retrieval_profile: Any) -> dict[str, Any]:
    if not isinstance(retrieval_profile, dict):
        raise SettingsValidationError("invalid_settings_editor_document", "retrieval_profile must be an object")
    required_keys = {
        "semantic_top_k",
        "recent_window_limit",
        "fact_bias",
        "summary_bias",
        "event_bias",
    }
    if set(retrieval_profile) != required_keys:
        raise SettingsValidationError("invalid_settings_editor_document", "retrieval_profile keys do not match fixed shape")
    semantic_top_k = retrieval_profile["semantic_top_k"]
    recent_window_limit = retrieval_profile["recent_window_limit"]
    if isinstance(semantic_top_k, bool) or not isinstance(semantic_top_k, int) or semantic_top_k < 1 or semantic_top_k > 64:
        raise SettingsValidationError("invalid_settings_editor_document", "semantic_top_k must be 1..64")
    if isinstance(recent_window_limit, bool) or not isinstance(recent_window_limit, int) or recent_window_limit < 1 or recent_window_limit > 20:
        raise SettingsValidationError("invalid_settings_editor_document", "recent_window_limit must be 1..20")
    normalized = {
        "semantic_top_k": semantic_top_k,
        "recent_window_limit": recent_window_limit,
    }
    for key in ("fact_bias", "summary_bias", "event_bias"):
        value = retrieval_profile[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsValidationError("invalid_settings_editor_document", f"{key} must be number")
        normalized_value = float(value)
        if normalized_value < 0.0 or normalized_value > 1.0:
            raise SettingsValidationError("invalid_settings_editor_document", f"{key} must be 0.0..1.0")
        normalized[key] = normalized_value
    return normalized


# Block: Keyed preset normalization
def _normalize_keyed_preset_payload(*, payload: dict[str, Any], required_keys: tuple[str, ...]) -> dict[str, Any]:
    if set(payload) != set(required_keys):
        raise SettingsValidationError("invalid_settings_editor_document", "preset payload keys do not match fixed shape")
    normalized: dict[str, Any] = {}
    for key in required_keys:
        definition = get_setting_definition(key)
        value = payload.get(key)
        _validate_type(definition, value)
        _validate_range(definition, value)
        _validate_length(definition, value)
        normalized[key] = value
    return normalized


# Block: Active preset validation
def _validate_active_preset_ids(
    *,
    editor_state: dict[str, Any],
    preset_catalogs: dict[str, list[dict[str, Any]]],
) -> None:
    for preset_kind, active_key in (
        ("behavior", "active_behavior_preset_id"),
        ("llm", "active_llm_preset_id"),
        ("memory", "active_memory_preset_id"),
        ("output", "active_output_preset_id"),
    ):
        active_preset_id = str(editor_state[active_key])
        known_ids = {
            str(entry["preset_id"])
            for entry in preset_catalogs[preset_kind]
        }
        if active_preset_id not in known_ids:
            raise SettingsValidationError(
                "invalid_settings_editor_document",
                f"{active_key} does not exist in preset_catalogs.{preset_kind}",
            )


# Block: Active camera validation
def _validate_active_camera_connection_id(
    *,
    editor_state: dict[str, Any],
    camera_connections: list[dict[str, Any]],
) -> None:
    active_camera_connection_id = editor_state["active_camera_connection_id"]
    if not camera_connections:
        if active_camera_connection_id is not None:
            raise SettingsValidationError(
                "invalid_settings_editor_document",
                "active_camera_connection_id must be null when camera_connections is empty",
            )
        return
    if active_camera_connection_id is None:
        raise SettingsValidationError(
            "invalid_settings_editor_document",
            "active_camera_connection_id must be set when camera_connections exists",
        )
    known_ids = {
        str(camera_connection["camera_connection_id"])
        for camera_connection in camera_connections
    }
    if active_camera_connection_id not in known_ids:
        raise SettingsValidationError(
            "invalid_settings_editor_document",
            "active_camera_connection_id does not exist in camera_connections",
        )


# Block: Required string helper
def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise SettingsValidationError("invalid_settings_editor_document", f"{field_name} must be non-empty string")
    return value


# Block: Optional string helper
def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise SettingsValidationError("invalid_settings_editor_document", f"{field_name} must be null or non-empty string")
    return value


# Block: String value helper
def _string_value(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise SettingsValidationError("invalid_settings_editor_document", f"{field_name} must be string")
    return value


# Block: Config defaults
@lru_cache(maxsize=1)
def _read_default_settings_from_config() -> dict[str, Any]:
    config_path = _settings_config_path()
    loaded_value = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(loaded_value, dict):
        raise RuntimeError("config/default_settings.json must be an object")
    expected_keys = set(SETTING_DEFINITION_MAP)
    actual_keys = set(loaded_value)
    if actual_keys != expected_keys:
        missing_keys = sorted(expected_keys - actual_keys)
        extra_keys = sorted(actual_keys - expected_keys)
        raise RuntimeError(
            "config/default_settings.json keys do not match registry: "
            f"missing={missing_keys}, extra={extra_keys}"
        )
    normalized_defaults: dict[str, Any] = {}
    for definition in SETTING_DEFINITIONS:
        requested_value = loaded_value[definition.key]
        _validate_type(definition, requested_value)
        _validate_range(definition, requested_value)
        _validate_length(definition, requested_value)
        normalized_defaults[definition.key] = requested_value
    return normalized_defaults


# Block: Config path
def _settings_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "default_settings.json"
