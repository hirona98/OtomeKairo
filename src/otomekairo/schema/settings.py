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
    SettingDefinition("llm.default_model", "string", ("runtime", "next_boot"), min_length=1, max_length=256),
    SettingDefinition("llm.embedding_model", "string", ("runtime", "next_boot"), min_length=1, max_length=256),
    SettingDefinition("llm.temperature", "number", ("runtime", "next_boot"), min_value=0.0, max_value=2.0),
    SettingDefinition("llm.max_output_tokens", "integer", ("runtime", "next_boot"), min_value=256, max_value=8192),
    SettingDefinition("runtime.idle_tick_ms", "integer", ("runtime", "next_boot"), min_value=250, max_value=60000),
    SettingDefinition("runtime.long_cycle_min_interval_ms", "integer", ("runtime", "next_boot"), min_value=1000, max_value=300000),
    SettingDefinition("runtime.context_budget_tokens", "integer", ("runtime", "next_boot"), min_value=1024, max_value=32768),
    SettingDefinition("sensors.camera.enabled", "boolean", ("runtime",)),
    SettingDefinition("sensors.microphone.enabled", "boolean", ("runtime",)),
    SettingDefinition("output.tts.enabled", "boolean", ("runtime",)),
    SettingDefinition("output.tts.voice", "string", ("runtime", "next_boot"), min_length=1, max_length=128),
    SettingDefinition("integrations.sns.enabled", "boolean", ("runtime",)),
    SettingDefinition("integrations.line.enabled", "boolean", ("runtime",)),
)


# Block: Registry index
SETTING_DEFINITION_MAP = {definition.key: definition for definition in SETTING_DEFINITIONS}


# Block: Public registry helpers
def get_setting_definition(key: str) -> SettingDefinition:
    definition = SETTING_DEFINITION_MAP.get(key)
    if definition is None:
        raise SettingsValidationError("unknown_settings_key", f"unknown settings key: {key}")
    return definition


# Block: Defaults export
def build_default_settings() -> dict[str, Any]:
    return dict(_read_default_settings_from_config())


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
