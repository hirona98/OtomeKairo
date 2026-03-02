"""Setting registry and validation rules."""

from __future__ import annotations

from dataclasses import dataclass
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
    default_value: Any
    min_value: int | float | None = None
    max_value: int | float | None = None
    min_length: int | None = None
    max_length: int | None = None


# Block: Registry source
SETTING_DEFINITIONS: tuple[SettingDefinition, ...] = (
    SettingDefinition("llm.default_model", "string", ("runtime", "next_boot"), "openrouter/default-model", min_length=1, max_length=256),
    SettingDefinition("llm.embedding_model", "string", ("runtime", "next_boot"), "openrouter/default-embedding", min_length=1, max_length=256),
    SettingDefinition("llm.temperature", "number", ("runtime", "next_boot"), 0.7, min_value=0.0, max_value=2.0),
    SettingDefinition("llm.max_output_tokens", "integer", ("runtime", "next_boot"), 2048, min_value=256, max_value=8192),
    SettingDefinition("runtime.idle_tick_ms", "integer", ("runtime", "next_boot"), 1000, min_value=250, max_value=60000),
    SettingDefinition("runtime.long_cycle_min_interval_ms", "integer", ("runtime", "next_boot"), 10000, min_value=1000, max_value=300000),
    SettingDefinition("runtime.context_budget_tokens", "integer", ("runtime", "next_boot"), 8192, min_value=1024, max_value=32768),
    SettingDefinition("sensors.camera.enabled", "boolean", ("runtime",), True),
    SettingDefinition("sensors.microphone.enabled", "boolean", ("runtime",), True),
    SettingDefinition("output.tts.enabled", "boolean", ("runtime",), True),
    SettingDefinition("output.tts.voice", "string", ("runtime", "next_boot"), "default", min_length=1, max_length=128),
    SettingDefinition("integrations.sns.enabled", "boolean", ("runtime",), False),
    SettingDefinition("integrations.line.enabled", "boolean", ("runtime",), False),
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
def build_effective_settings() -> dict[str, Any]:
    return {definition.key: definition.default_value for definition in SETTING_DEFINITIONS}


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
