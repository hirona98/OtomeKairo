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
    SettingDefinition("llm.temperature", "number", ("runtime", "next_boot"), min_value=0.0, max_value=2.0),
    SettingDefinition("llm.max_output_tokens", "integer", ("runtime", "next_boot"), min_value=256, max_value=8192),
    SettingDefinition("llm.reasoning_effort", "string", ("runtime", "next_boot"), min_length=0, max_length=32),
    SettingDefinition("llm.reply_web_search_enabled", "boolean", ("runtime", "next_boot")),
    SettingDefinition("llm.max_turns_window", "integer", ("runtime", "next_boot"), min_value=1, max_value=200),
    SettingDefinition("llm.image_model", "string", ("runtime", "next_boot"), min_length=1, max_length=256),
    SettingDefinition("llm.image_api_key", "string", ("runtime", "next_boot"), min_length=0, max_length=4096),
    SettingDefinition("llm.image_base_url", "string", ("runtime", "next_boot"), min_length=0, max_length=512),
    SettingDefinition("llm.max_output_tokens_vision", "integer", ("runtime", "next_boot"), min_value=256, max_value=8192),
    SettingDefinition("llm.image_timeout_seconds", "integer", ("runtime", "next_boot"), min_value=1, max_value=600),
    SettingDefinition("llm.embedding_model", "string", ("runtime", "next_boot"), min_length=1, max_length=256),
    SettingDefinition("llm.embedding_api_key", "string", ("runtime", "next_boot"), min_length=0, max_length=4096),
    SettingDefinition("llm.embedding_base_url", "string", ("runtime", "next_boot"), min_length=0, max_length=512),
    SettingDefinition("runtime.idle_tick_ms", "integer", ("runtime", "next_boot"), min_value=250, max_value=60000),
    SettingDefinition("runtime.long_cycle_min_interval_ms", "integer", ("runtime", "next_boot"), min_value=1000, max_value=300000),
    SettingDefinition("runtime.context_budget_tokens", "integer", ("runtime", "next_boot"), min_value=1024, max_value=32768),
    SettingDefinition("behavior.second_person_label", "string", ("runtime", "next_boot"), min_length=0, max_length=128),
    SettingDefinition("behavior.system_prompt", "string", ("runtime", "next_boot"), min_length=0, max_length=20000),
    SettingDefinition("behavior.addon_prompt", "string", ("runtime", "next_boot"), min_length=0, max_length=20000),
    SettingDefinition("behavior.response_pace", "string", ("runtime", "next_boot"), min_length=1, max_length=32),
    SettingDefinition("behavior.proactivity_level", "string", ("runtime", "next_boot"), min_length=1, max_length=32),
    SettingDefinition("behavior.browse_preference", "string", ("runtime", "next_boot"), min_length=1, max_length=32),
    SettingDefinition("behavior.notify_preference", "string", ("runtime", "next_boot"), min_length=1, max_length=32),
    SettingDefinition("behavior.speech_style", "string", ("runtime", "next_boot"), min_length=1, max_length=32),
    SettingDefinition("behavior.verbosity_bias", "string", ("runtime", "next_boot"), min_length=1, max_length=32),
    SettingDefinition("memory.embedding_dimension", "integer", ("runtime", "next_boot"), min_value=1, max_value=8192),
    SettingDefinition("memory.similar_episodes_limit", "integer", ("runtime", "next_boot"), min_value=1, max_value=512),
    SettingDefinition("memory.max_inject_tokens", "integer", ("runtime", "next_boot"), min_value=256, max_value=32768),
    SettingDefinition("sensors.camera.enabled", "boolean", ("runtime",)),
    SettingDefinition("sensors.microphone.enabled", "boolean", ("runtime",)),
    SettingDefinition("character.vrm_file_path", "string", ("runtime", "next_boot"), min_length=0, max_length=1024),
    SettingDefinition("character.material.convert_unlit_to_mtoon", "boolean", ("runtime", "next_boot")),
    SettingDefinition("character.material.enable_shadow_off", "boolean", ("runtime", "next_boot")),
    SettingDefinition("character.material.shadow_off_meshes", "string", ("runtime", "next_boot"), min_length=0, max_length=4096),
    SettingDefinition("speech.tts.enabled", "boolean", ("runtime", "next_boot")),
    SettingDefinition("speech.tts.provider", "string", ("runtime", "next_boot"), min_length=1, max_length=64),
    SettingDefinition("speech.tts.aivis_cloud.api_key", "string", ("runtime", "next_boot"), min_length=0, max_length=4096),
    SettingDefinition("speech.tts.aivis_cloud.endpoint_url", "string", ("runtime", "next_boot"), min_length=0, max_length=512),
    SettingDefinition("speech.tts.aivis_cloud.model_uuid", "string", ("runtime", "next_boot"), min_length=0, max_length=128),
    SettingDefinition("speech.tts.aivis_cloud.speaker_uuid", "string", ("runtime", "next_boot"), min_length=0, max_length=128),
    SettingDefinition("speech.tts.aivis_cloud.style_id", "integer", ("runtime", "next_boot"), min_value=0, max_value=999999),
    SettingDefinition("speech.tts.aivis_cloud.use_ssml", "boolean", ("runtime", "next_boot")),
    SettingDefinition("speech.tts.aivis_cloud.language", "string", ("runtime", "next_boot"), min_length=1, max_length=32),
    SettingDefinition("speech.tts.aivis_cloud.speaking_rate", "number", ("runtime", "next_boot"), min_value=0.25, max_value=4.0),
    SettingDefinition("speech.tts.aivis_cloud.emotional_intensity", "number", ("runtime", "next_boot"), min_value=0.0, max_value=2.0),
    SettingDefinition("speech.tts.aivis_cloud.tempo_dynamics", "number", ("runtime", "next_boot"), min_value=0.0, max_value=2.0),
    SettingDefinition("speech.tts.aivis_cloud.pitch", "number", ("runtime", "next_boot"), min_value=-1.0, max_value=1.0),
    SettingDefinition("speech.tts.aivis_cloud.volume", "number", ("runtime", "next_boot"), min_value=0.0, max_value=2.0),
    SettingDefinition("speech.tts.aivis_cloud.output_format", "string", ("runtime", "next_boot"), min_length=1, max_length=16),
    SettingDefinition("speech.tts.voicevox.endpoint_url", "string", ("runtime", "next_boot"), min_length=0, max_length=512),
    SettingDefinition("speech.tts.voicevox.speaker_id", "integer", ("runtime", "next_boot"), min_value=0, max_value=999999),
    SettingDefinition("speech.tts.voicevox.speed_scale", "number", ("runtime", "next_boot"), min_value=0.5, max_value=2.0),
    SettingDefinition("speech.tts.voicevox.pitch_scale", "number", ("runtime", "next_boot"), min_value=-0.15, max_value=0.15),
    SettingDefinition("speech.tts.voicevox.intonation_scale", "number", ("runtime", "next_boot"), min_value=0.0, max_value=2.0),
    SettingDefinition("speech.tts.voicevox.volume_scale", "number", ("runtime", "next_boot"), min_value=0.0, max_value=2.0),
    SettingDefinition("speech.tts.voicevox.pre_phoneme_length", "number", ("runtime", "next_boot"), min_value=0.0, max_value=1.5),
    SettingDefinition("speech.tts.voicevox.post_phoneme_length", "number", ("runtime", "next_boot"), min_value=0.0, max_value=1.5),
    SettingDefinition("speech.tts.voicevox.output_sampling_rate", "integer", ("runtime", "next_boot"), min_value=8000, max_value=48000),
    SettingDefinition("speech.tts.voicevox.output_stereo", "boolean", ("runtime", "next_boot")),
    SettingDefinition("speech.tts.style_bert_vits2.endpoint_url", "string", ("runtime", "next_boot"), min_length=0, max_length=512),
    SettingDefinition("speech.tts.style_bert_vits2.model_name", "string", ("runtime", "next_boot"), min_length=0, max_length=128),
    SettingDefinition("speech.tts.style_bert_vits2.model_id", "integer", ("runtime", "next_boot"), min_value=0, max_value=999999),
    SettingDefinition("speech.tts.style_bert_vits2.speaker_name", "string", ("runtime", "next_boot"), min_length=0, max_length=128),
    SettingDefinition("speech.tts.style_bert_vits2.speaker_id", "integer", ("runtime", "next_boot"), min_value=0, max_value=999999),
    SettingDefinition("speech.tts.style_bert_vits2.style", "string", ("runtime", "next_boot"), min_length=1, max_length=128),
    SettingDefinition("speech.tts.style_bert_vits2.style_weight", "number", ("runtime", "next_boot"), min_value=0.0, max_value=10.0),
    SettingDefinition("speech.tts.style_bert_vits2.sdp_ratio", "number", ("runtime", "next_boot"), min_value=0.0, max_value=1.0),
    SettingDefinition("speech.tts.style_bert_vits2.noise", "number", ("runtime", "next_boot"), min_value=0.0, max_value=10.0),
    SettingDefinition("speech.tts.style_bert_vits2.noise_w", "number", ("runtime", "next_boot"), min_value=0.0, max_value=10.0),
    SettingDefinition("speech.tts.style_bert_vits2.length", "number", ("runtime", "next_boot"), min_value=0.25, max_value=4.0),
    SettingDefinition("speech.tts.style_bert_vits2.language", "string", ("runtime", "next_boot"), min_length=1, max_length=32),
    SettingDefinition("speech.tts.style_bert_vits2.auto_split", "boolean", ("runtime", "next_boot")),
    SettingDefinition("speech.tts.style_bert_vits2.split_interval", "number", ("runtime", "next_boot"), min_value=0.0, max_value=30.0),
    SettingDefinition("speech.tts.style_bert_vits2.assist_text", "string", ("runtime", "next_boot"), min_length=0, max_length=4096),
    SettingDefinition("speech.tts.style_bert_vits2.assist_text_weight", "number", ("runtime", "next_boot"), min_value=0.0, max_value=10.0),
    SettingDefinition("speech.stt.enabled", "boolean", ("runtime", "next_boot")),
    SettingDefinition("speech.stt.provider", "string", ("runtime", "next_boot"), min_length=1, max_length=64),
    SettingDefinition("speech.stt.wake_word", "string", ("runtime", "next_boot"), min_length=0, max_length=1024),
    SettingDefinition("speech.stt.language", "string", ("runtime", "next_boot"), min_length=0, max_length=32),
    SettingDefinition("speech.stt.amivoice.profile_id", "string", ("runtime", "next_boot"), min_length=0, max_length=256),
    SettingDefinition("speech.stt.amivoice.api_key", "string", ("runtime", "next_boot"), min_length=0, max_length=4096),
    SettingDefinition("motion.posture_change_loop_count_standing", "integer", ("runtime", "next_boot"), min_value=1, max_value=9999),
    SettingDefinition("motion.posture_change_loop_count_sitting_floor", "integer", ("runtime", "next_boot"), min_value=1, max_value=9999),
    SettingDefinition("integrations.notify_route", "string", ("runtime", "next_boot"), min_length=1, max_length=64),
    SettingDefinition("integrations.sns.enabled", "boolean", ("runtime",)),
    SettingDefinition("integrations.discord.bot_token", "string", ("runtime", "next_boot"), min_length=0, max_length=4096),
    SettingDefinition("integrations.discord.channel_id", "string", ("runtime", "next_boot"), min_length=0, max_length=256),
)


# Block: Registry index
SETTING_DEFINITION_MAP = {definition.key: definition for definition in SETTING_DEFINITIONS}


# Block: Editor constants
SETTINGS_EDITOR_SYSTEM_KEYS = (
    "runtime.idle_tick_ms",
    "runtime.long_cycle_min_interval_ms",
    "sensors.microphone.enabled",
    "sensors.camera.enabled",
    "integrations.sns.enabled",
    "integrations.notify_route",
    "integrations.discord.bot_token",
    "integrations.discord.channel_id",
)
CHARACTER_PRESET_SETTING_KEYS = (
    "character.vrm_file_path",
    "character.material.convert_unlit_to_mtoon",
    "character.material.enable_shadow_off",
    "character.material.shadow_off_meshes",
    "speech.tts.enabled",
    "speech.tts.provider",
    "speech.tts.aivis_cloud.api_key",
    "speech.tts.aivis_cloud.endpoint_url",
    "speech.tts.aivis_cloud.model_uuid",
    "speech.tts.aivis_cloud.speaker_uuid",
    "speech.tts.aivis_cloud.style_id",
    "speech.tts.aivis_cloud.use_ssml",
    "speech.tts.aivis_cloud.language",
    "speech.tts.aivis_cloud.speaking_rate",
    "speech.tts.aivis_cloud.emotional_intensity",
    "speech.tts.aivis_cloud.tempo_dynamics",
    "speech.tts.aivis_cloud.pitch",
    "speech.tts.aivis_cloud.volume",
    "speech.tts.aivis_cloud.output_format",
    "speech.tts.voicevox.endpoint_url",
    "speech.tts.voicevox.speaker_id",
    "speech.tts.voicevox.speed_scale",
    "speech.tts.voicevox.pitch_scale",
    "speech.tts.voicevox.intonation_scale",
    "speech.tts.voicevox.volume_scale",
    "speech.tts.voicevox.pre_phoneme_length",
    "speech.tts.voicevox.post_phoneme_length",
    "speech.tts.voicevox.output_sampling_rate",
    "speech.tts.voicevox.output_stereo",
    "speech.tts.style_bert_vits2.endpoint_url",
    "speech.tts.style_bert_vits2.model_name",
    "speech.tts.style_bert_vits2.model_id",
    "speech.tts.style_bert_vits2.speaker_name",
    "speech.tts.style_bert_vits2.speaker_id",
    "speech.tts.style_bert_vits2.style",
    "speech.tts.style_bert_vits2.style_weight",
    "speech.tts.style_bert_vits2.sdp_ratio",
    "speech.tts.style_bert_vits2.noise",
    "speech.tts.style_bert_vits2.noise_w",
    "speech.tts.style_bert_vits2.length",
    "speech.tts.style_bert_vits2.language",
    "speech.tts.style_bert_vits2.auto_split",
    "speech.tts.style_bert_vits2.split_interval",
    "speech.tts.style_bert_vits2.assist_text",
    "speech.tts.style_bert_vits2.assist_text_weight",
    "speech.stt.enabled",
    "speech.stt.provider",
    "speech.stt.wake_word",
    "speech.stt.language",
    "speech.stt.amivoice.profile_id",
    "speech.stt.amivoice.api_key",
)
BEHAVIOR_PRESET_SETTING_KEYS = (
    "behavior.second_person_label",
    "behavior.system_prompt",
    "behavior.addon_prompt",
    "behavior.response_pace",
    "behavior.proactivity_level",
    "behavior.browse_preference",
    "behavior.notify_preference",
    "behavior.speech_style",
    "behavior.verbosity_bias",
)
CONVERSATION_PRESET_SETTING_KEYS = (
    "llm.model",
    "llm.api_key",
    "llm.base_url",
    "llm.temperature",
    "llm.max_output_tokens",
    "llm.reasoning_effort",
    "llm.reply_web_search_enabled",
    "llm.max_turns_window",
    "llm.image_model",
    "llm.image_api_key",
    "llm.image_base_url",
    "llm.max_output_tokens_vision",
    "llm.image_timeout_seconds",
)
MEMORY_PRESET_SETTING_KEYS = (
    "llm.embedding_model",
    "llm.embedding_api_key",
    "llm.embedding_base_url",
    "runtime.context_budget_tokens",
    "memory.embedding_dimension",
    "memory.similar_episodes_limit",
    "memory.max_inject_tokens",
)
DEFAULT_SETTINGS_EDITOR_PRESET_IDS = {
    "character": "preset_character_default",
    "behavior": "preset_behavior_default",
    "conversation": "preset_conversation_default",
    "memory": "preset_memory_default",
    "motion": "preset_motion_default",
}
SUPPORTED_TTS_PROVIDERS = ("aivis-cloud", "voicevox", "style-bert-vits2")
SUPPORTED_STT_PROVIDERS = ("amivoice",)
SUPPORTED_NOTIFY_ROUTES = ("ui_only", "discord")
SUPPORTED_REASONING_EFFORTS = ("", "low", "medium", "high")
AIVIS_CLOUD_OUTPUT_FORMATS = ("wav", "mp3", "ogg", "aac", "flac")
MOTION_ANIMATION_TYPE_VALUES = (0, 1, 2)
BEHAVIOR_RESPONSE_PACE_VALUES = ("careful", "balanced", "quick")
BEHAVIOR_PROACTIVITY_LEVEL_VALUES = ("low", "medium", "high")
BEHAVIOR_BROWSE_PREFERENCE_VALUES = ("avoid", "balanced", "prefer")
BEHAVIOR_NOTIFY_PREFERENCE_VALUES = ("quiet", "balanced", "proactive")
BEHAVIOR_SPEECH_STYLE_VALUES = ("gentle", "neutral", "firm")
BEHAVIOR_VERBOSITY_BIAS_VALUES = ("short", "balanced", "detailed")


# Block: Enumerated setting values
ENUM_SETTING_ALLOWED_VALUES: dict[str, tuple[str, ...]] = {
    "llm.reasoning_effort": SUPPORTED_REASONING_EFFORTS,
    "behavior.response_pace": BEHAVIOR_RESPONSE_PACE_VALUES,
    "behavior.proactivity_level": BEHAVIOR_PROACTIVITY_LEVEL_VALUES,
    "behavior.browse_preference": BEHAVIOR_BROWSE_PREFERENCE_VALUES,
    "behavior.notify_preference": BEHAVIOR_NOTIFY_PREFERENCE_VALUES,
    "behavior.speech_style": BEHAVIOR_SPEECH_STYLE_VALUES,
    "behavior.verbosity_bias": BEHAVIOR_VERBOSITY_BIAS_VALUES,
    "speech.tts.provider": SUPPORTED_TTS_PROVIDERS,
    "speech.tts.aivis_cloud.output_format": AIVIS_CLOUD_OUTPUT_FORMATS,
    "speech.stt.provider": SUPPORTED_STT_PROVIDERS,
    "integrations.notify_route": SUPPORTED_NOTIFY_ROUTES,
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


# Block: Character key export
def build_character_preset_setting_keys() -> tuple[str, ...]:
    return CHARACTER_PRESET_SETTING_KEYS


# Block: Editor state seed
def build_default_settings_editor_state(default_settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "active_character_preset_id": DEFAULT_SETTINGS_EDITOR_PRESET_IDS["character"],
        "active_behavior_preset_id": DEFAULT_SETTINGS_EDITOR_PRESET_IDS["behavior"],
        "active_conversation_preset_id": DEFAULT_SETTINGS_EDITOR_PRESET_IDS["conversation"],
        "active_memory_preset_id": DEFAULT_SETTINGS_EDITOR_PRESET_IDS["memory"],
        "active_motion_preset_id": DEFAULT_SETTINGS_EDITOR_PRESET_IDS["motion"],
        "system_values_json": {
            key: default_settings[key]
            for key in SETTINGS_EDITOR_SYSTEM_KEYS
        },
        "revision": 1,
    }


# Block: Character preset payload seed
def build_default_character_preset_payload(default_settings: dict[str, Any]) -> dict[str, Any]:
    return {
        key: default_settings[key]
        for key in CHARACTER_PRESET_SETTING_KEYS
    }


# Block: Motion preset payload seed
def build_default_motion_preset_payload(default_settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "motion.posture_change_loop_count_standing": int(default_settings["motion.posture_change_loop_count_standing"]),
        "motion.posture_change_loop_count_sitting_floor": int(default_settings["motion.posture_change_loop_count_sitting_floor"]),
        "animations": [],
    }


# Block: Preset seed export
def build_default_settings_editor_presets(default_settings: dict[str, Any]) -> dict[str, tuple[dict[str, Any], ...]]:
    return {
        "character_presets": (
            {
                "preset_id": "preset_character_default",
                "preset_name": "新規キャラクター",
                "payload": build_default_character_preset_payload(default_settings),
            },
        ),
        "behavior_presets": (
            {
                "preset_id": "preset_behavior_default",
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
                "preset_id": "preset_behavior_quiet",
                "preset_name": "静かめ",
                "payload": {
                    "behavior.second_person_label": str(default_settings["behavior.second_person_label"]),
                    "behavior.system_prompt": str(default_settings["behavior.system_prompt"]),
                    "behavior.addon_prompt": str(default_settings["behavior.addon_prompt"]),
                    "behavior.response_pace": "careful",
                    "behavior.proactivity_level": "low",
                    "behavior.browse_preference": "avoid",
                    "behavior.notify_preference": "quiet",
                    "behavior.speech_style": "gentle",
                    "behavior.verbosity_bias": "short",
                },
            },
        ),
        "conversation_presets": (
            {
                "preset_id": "preset_conversation_default",
                "preset_name": "標準",
                "payload": {
                    "llm.model": str(default_settings["llm.model"]),
                    "llm.api_key": str(default_settings["llm.api_key"]),
                    "llm.base_url": str(default_settings["llm.base_url"]),
                    "llm.temperature": float(default_settings["llm.temperature"]),
                    "llm.max_output_tokens": int(default_settings["llm.max_output_tokens"]),
                    "llm.reasoning_effort": str(default_settings["llm.reasoning_effort"]),
                    "llm.reply_web_search_enabled": bool(default_settings["llm.reply_web_search_enabled"]),
                    "llm.max_turns_window": int(default_settings["llm.max_turns_window"]),
                    "llm.image_model": str(default_settings["llm.image_model"]),
                    "llm.image_api_key": str(default_settings["llm.image_api_key"]),
                    "llm.image_base_url": str(default_settings["llm.image_base_url"]),
                    "llm.max_output_tokens_vision": int(default_settings["llm.max_output_tokens_vision"]),
                    "llm.image_timeout_seconds": int(default_settings["llm.image_timeout_seconds"]),
                },
            },
            {
                "preset_id": "preset_conversation_precise",
                "preset_name": "低温度",
                "payload": {
                    "llm.model": str(default_settings["llm.model"]),
                    "llm.api_key": str(default_settings["llm.api_key"]),
                    "llm.base_url": str(default_settings["llm.base_url"]),
                    "llm.temperature": 0.3,
                    "llm.max_output_tokens": 1536,
                    "llm.reasoning_effort": str(default_settings["llm.reasoning_effort"]),
                    "llm.reply_web_search_enabled": bool(default_settings["llm.reply_web_search_enabled"]),
                    "llm.max_turns_window": int(default_settings["llm.max_turns_window"]),
                    "llm.image_model": str(default_settings["llm.image_model"]),
                    "llm.image_api_key": str(default_settings["llm.image_api_key"]),
                    "llm.image_base_url": str(default_settings["llm.image_base_url"]),
                    "llm.max_output_tokens_vision": int(default_settings["llm.max_output_tokens_vision"]),
                    "llm.image_timeout_seconds": int(default_settings["llm.image_timeout_seconds"]),
                },
            },
        ),
        "memory_presets": (
            {
                "preset_id": "preset_memory_default",
                "preset_name": "標準",
                "payload": {
                    "llm.embedding_model": str(default_settings["llm.embedding_model"]),
                    "llm.embedding_api_key": str(default_settings["llm.embedding_api_key"]),
                    "llm.embedding_base_url": str(default_settings["llm.embedding_base_url"]),
                    "runtime.context_budget_tokens": int(default_settings["runtime.context_budget_tokens"]),
                    "memory.embedding_dimension": int(default_settings["memory.embedding_dimension"]),
                    "memory.similar_episodes_limit": int(default_settings["memory.similar_episodes_limit"]),
                    "memory.max_inject_tokens": int(default_settings["memory.max_inject_tokens"]),
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
                "preset_name": "深め",
                "payload": {
                    "llm.embedding_model": str(default_settings["llm.embedding_model"]),
                    "llm.embedding_api_key": str(default_settings["llm.embedding_api_key"]),
                    "llm.embedding_base_url": str(default_settings["llm.embedding_base_url"]),
                    "runtime.context_budget_tokens": 12288,
                    "memory.embedding_dimension": int(default_settings["memory.embedding_dimension"]),
                    "memory.similar_episodes_limit": int(default_settings["memory.similar_episodes_limit"]),
                    "memory.max_inject_tokens": int(default_settings["memory.max_inject_tokens"]),
                    "retrieval_profile": {
                        "semantic_top_k": 12,
                        "recent_window_limit": 6,
                        "fact_bias": 0.85,
                        "summary_bias": 0.55,
                        "event_bias": 0.35,
                    },
                },
            },
        ),
        "motion_presets": (
            {
                "preset_id": "preset_motion_default",
                "preset_name": "デフォルト",
                "payload": build_default_motion_preset_payload(default_settings),
            },
        ),
    }


# Block: Camera connection seed export
def build_default_camera_connections() -> tuple[dict[str, Any], ...]:
    return ()


# Block: Editor payload normalization
def normalize_settings_editor_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise SettingsValidationError("invalid_settings_editor_document", "settings editor payload must be an object")
    expected_keys = {
        "editor_state",
        "character_presets",
        "behavior_presets",
        "conversation_presets",
        "memory_presets",
        "motion_presets",
        "camera_connections",
    }
    if set(document) != expected_keys:
        raise SettingsValidationError(
            "invalid_settings_editor_document",
            "settings editor payload keys do not match fixed shape",
        )
    editor_state = _normalize_editor_state(document.get("editor_state"))
    character_presets = _normalize_character_presets(document.get("character_presets"))
    behavior_presets = _normalize_behavior_presets(document.get("behavior_presets"))
    conversation_presets = _normalize_conversation_presets(document.get("conversation_presets"))
    memory_presets = _normalize_memory_presets(document.get("memory_presets"))
    motion_presets = _normalize_motion_presets(document.get("motion_presets"))
    camera_connections = _normalize_camera_connections(document.get("camera_connections"))
    _validate_active_preset_ids(
        editor_state=editor_state,
        character_presets=character_presets,
        behavior_presets=behavior_presets,
        conversation_presets=conversation_presets,
        memory_presets=memory_presets,
        motion_presets=motion_presets,
    )
    return {
        "editor_state": editor_state,
        "character_presets": character_presets,
        "behavior_presets": behavior_presets,
        "conversation_presets": conversation_presets,
        "memory_presets": memory_presets,
        "motion_presets": motion_presets,
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
    _validate_enumeration(definition, requested_value)
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
    _validate_enumeration(definition, requested_value)
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


# Block: Enumerated string validation
def _validate_enumeration(definition: SettingDefinition, requested_value: Any) -> None:
    allowed_values = ENUM_SETTING_ALLOWED_VALUES.get(definition.key)
    if allowed_values is None:
        return
    if requested_value not in allowed_values:
        raise SettingsValidationError("invalid_settings_value", f"{definition.key} is invalid")


# Block: Editor state normalization
def _normalize_editor_state(editor_state: Any) -> dict[str, Any]:
    if not isinstance(editor_state, dict):
        raise SettingsValidationError("invalid_settings_editor_document", "editor_state must be an object")
    expected_keys = {
        "revision",
        "active_character_preset_id",
        "active_behavior_preset_id",
        "active_conversation_preset_id",
        "active_memory_preset_id",
        "active_motion_preset_id",
        "system_values",
    }
    if set(editor_state) != expected_keys:
        raise SettingsValidationError("invalid_settings_editor_document", "editor_state keys do not match fixed shape")
    revision = editor_state.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise SettingsValidationError("invalid_settings_editor_document", "editor_state.revision must be integer")
    system_values = _normalize_system_values(editor_state.get("system_values"))
    return {
        "revision": revision,
        "active_character_preset_id": _required_string(
            editor_state.get("active_character_preset_id"),
            "editor_state.active_character_preset_id",
        ),
        "active_behavior_preset_id": _required_string(
            editor_state.get("active_behavior_preset_id"),
            "editor_state.active_behavior_preset_id",
        ),
        "active_conversation_preset_id": _required_string(
            editor_state.get("active_conversation_preset_id"),
            "editor_state.active_conversation_preset_id",
        ),
        "active_memory_preset_id": _required_string(
            editor_state.get("active_memory_preset_id"),
            "editor_state.active_memory_preset_id",
        ),
        "active_motion_preset_id": _required_string(
            editor_state.get("active_motion_preset_id"),
            "editor_state.active_motion_preset_id",
        ),
        "system_values": system_values,
    }


# Block: System values normalization
def _normalize_system_values(system_values: Any) -> dict[str, Any]:
    if not isinstance(system_values, dict):
        raise SettingsValidationError("invalid_settings_editor_document", "editor_state.system_values must be an object")
    expected_keys = set(SETTINGS_EDITOR_SYSTEM_KEYS)
    if set(system_values) != expected_keys:
        raise SettingsValidationError(
            "invalid_settings_editor_document",
            "editor_state.system_values keys do not match system key set",
        )
    normalized = _normalize_keyed_values(system_values, SETTINGS_EDITOR_SYSTEM_KEYS)
    notify_route = str(normalized["integrations.notify_route"])
    if notify_route not in SUPPORTED_NOTIFY_ROUTES:
        raise SettingsValidationError("invalid_settings_editor_document", "integrations.notify_route is invalid")
    if notify_route == "discord":
        if not normalized["integrations.discord.bot_token"] or not normalized["integrations.discord.channel_id"]:
            raise SettingsValidationError(
                "invalid_settings_editor_document",
                "discord route requires discord credentials",
            )
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
        expected_keys = {
            "camera_connection_id",
            "is_enabled",
            "display_name",
            "host",
            "username",
            "password",
            "sort_order",
            "updated_at",
        }
        if set(camera_connection) != expected_keys:
            raise SettingsValidationError("invalid_settings_editor_document", "camera_connections entry keys do not match fixed shape")
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
                "is_enabled": _required_boolean(
                    camera_connection.get("is_enabled"),
                    "camera_connections.is_enabled",
                ),
                "display_name": _required_string(
                    camera_connection.get("display_name"),
                    "camera_connections.display_name",
                ),
                "host": _string_value(camera_connection.get("host"), "camera_connections.host"),
                "username": _string_value(camera_connection.get("username"), "camera_connections.username"),
                "password": _string_value(camera_connection.get("password"), "camera_connections.password"),
                "sort_order": sort_order,
                "updated_at": updated_at,
            }
        )
        _validate_camera_connection_entry(normalized_connections[-1])
    return normalized_connections


# Block: Character presets normalization
def _normalize_character_presets(character_presets: Any) -> list[dict[str, Any]]:
    return _normalize_preset_entries(
        preset_entries=character_presets,
        field_name="character_presets",
        payload_normalizer=_normalize_character_preset_payload,
    )


# Block: Behavior presets normalization
def _normalize_behavior_presets(behavior_presets: Any) -> list[dict[str, Any]]:
    return _normalize_preset_entries(
        preset_entries=behavior_presets,
        field_name="behavior_presets",
        payload_normalizer=_normalize_behavior_preset_payload,
    )


# Block: Conversation presets normalization
def _normalize_conversation_presets(conversation_presets: Any) -> list[dict[str, Any]]:
    return _normalize_preset_entries(
        preset_entries=conversation_presets,
        field_name="conversation_presets",
        payload_normalizer=_normalize_conversation_preset_payload,
    )


# Block: Memory presets normalization
def _normalize_memory_presets(memory_presets: Any) -> list[dict[str, Any]]:
    return _normalize_preset_entries(
        preset_entries=memory_presets,
        field_name="memory_presets",
        payload_normalizer=_normalize_memory_preset_payload,
    )


# Block: Motion presets normalization
def _normalize_motion_presets(motion_presets: Any) -> list[dict[str, Any]]:
    return _normalize_preset_entries(
        preset_entries=motion_presets,
        field_name="motion_presets",
        payload_normalizer=_normalize_motion_preset_payload,
    )


# Block: Preset entry normalization
def _normalize_preset_entries(
    *,
    preset_entries: Any,
    field_name: str,
    payload_normalizer: Any,
) -> list[dict[str, Any]]:
    if not isinstance(preset_entries, list) or not preset_entries:
        raise SettingsValidationError(
            "invalid_settings_editor_document",
            f"{field_name} must be a non-empty array",
        )
    normalized_entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for preset_entry in preset_entries:
        if not isinstance(preset_entry, dict):
            raise SettingsValidationError(
                "invalid_settings_editor_document",
                f"{field_name} entries must be objects",
            )
        archived = preset_entry.get("archived")
        sort_order = preset_entry.get("sort_order")
        updated_at = preset_entry.get("updated_at")
        if not isinstance(archived, bool):
            raise SettingsValidationError(
                "invalid_settings_editor_document",
                f"{field_name}.archived must be boolean",
            )
        if isinstance(sort_order, bool) or not isinstance(sort_order, int):
            raise SettingsValidationError(
                "invalid_settings_editor_document",
                f"{field_name}.sort_order must be integer",
            )
        if isinstance(updated_at, bool) or not isinstance(updated_at, int):
            raise SettingsValidationError(
                "invalid_settings_editor_document",
                f"{field_name}.updated_at must be integer",
            )
        preset_id = _required_string(preset_entry.get("preset_id"), f"{field_name}.preset_id")
        if preset_id in seen_ids:
            raise SettingsValidationError(
                "invalid_settings_editor_document",
                f"{field_name} contains duplicate preset_id",
            )
        seen_ids.add(preset_id)
        normalized_entries.append(
            {
                "preset_id": preset_id,
                "preset_name": _required_string(preset_entry.get("preset_name"), f"{field_name}.preset_name"),
                "archived": archived,
                "sort_order": sort_order,
                "updated_at": updated_at,
                "payload": payload_normalizer(preset_entry.get("payload")),
            }
        )
    return normalized_entries


# Block: Character preset normalization
def _normalize_character_preset_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SettingsValidationError("invalid_settings_editor_document", "character preset payload must be an object")
    normalized = _normalize_keyed_values(payload, CHARACTER_PRESET_SETTING_KEYS)
    tts_provider = str(normalized["speech.tts.provider"])
    if tts_provider not in SUPPORTED_TTS_PROVIDERS:
        raise SettingsValidationError("invalid_settings_editor_document", "speech.tts.provider is invalid")
    if normalized["speech.tts.aivis_cloud.output_format"] not in AIVIS_CLOUD_OUTPUT_FORMATS:
        raise SettingsValidationError("invalid_settings_editor_document", "speech.tts.aivis_cloud.output_format is invalid")
    if bool(normalized["speech.tts.enabled"]):
        _validate_enabled_tts_provider_settings(normalized)
    stt_provider = str(normalized["speech.stt.provider"])
    if stt_provider not in SUPPORTED_STT_PROVIDERS:
        raise SettingsValidationError("invalid_settings_editor_document", "speech.stt.provider is invalid")
    if bool(normalized["speech.stt.enabled"]):
        _validate_enabled_stt_provider_settings(normalized)
    return normalized


# Block: Behavior preset normalization
def _normalize_behavior_preset_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SettingsValidationError("invalid_settings_editor_document", "behavior preset payload must be an object")
    normalized = _normalize_keyed_values(payload, BEHAVIOR_PRESET_SETTING_KEYS)

    # Block: Behavior enumeration validation
    allowed_value_sets = {
        "behavior.response_pace": BEHAVIOR_RESPONSE_PACE_VALUES,
        "behavior.proactivity_level": BEHAVIOR_PROACTIVITY_LEVEL_VALUES,
        "behavior.browse_preference": BEHAVIOR_BROWSE_PREFERENCE_VALUES,
        "behavior.notify_preference": BEHAVIOR_NOTIFY_PREFERENCE_VALUES,
        "behavior.speech_style": BEHAVIOR_SPEECH_STYLE_VALUES,
        "behavior.verbosity_bias": BEHAVIOR_VERBOSITY_BIAS_VALUES,
    }
    for key, allowed_values in allowed_value_sets.items():
        if str(normalized[key]) not in allowed_values:
            raise SettingsValidationError("invalid_settings_editor_document", f"{key} is invalid")
    return normalized


# Block: Conversation preset normalization
def _normalize_conversation_preset_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SettingsValidationError("invalid_settings_editor_document", "conversation preset payload must be an object")
    normalized = _normalize_keyed_values(payload, CONVERSATION_PRESET_SETTING_KEYS)
    reasoning_effort = str(normalized["llm.reasoning_effort"])
    if reasoning_effort not in SUPPORTED_REASONING_EFFORTS:
        raise SettingsValidationError("invalid_settings_editor_document", "llm.reasoning_effort is invalid")
    return normalized


# Block: Memory preset normalization
def _normalize_memory_preset_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SettingsValidationError("invalid_settings_editor_document", "memory preset payload must be an object")
    required_keys = set(MEMORY_PRESET_SETTING_KEYS) | {"retrieval_profile"}
    if set(payload) != required_keys:
        raise SettingsValidationError("invalid_settings_editor_document", "memory preset keys do not match fixed shape")
    normalized = _normalize_keyed_values(
        {key: value for key, value in payload.items() if key != "retrieval_profile"},
        MEMORY_PRESET_SETTING_KEYS,
    )
    normalized["retrieval_profile"] = _normalize_retrieval_profile(payload.get("retrieval_profile"))
    return normalized


# Block: Motion preset normalization
def _normalize_motion_preset_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SettingsValidationError("invalid_settings_editor_document", "motion preset payload must be an object")
    expected_keys = {
        "motion.posture_change_loop_count_standing",
        "motion.posture_change_loop_count_sitting_floor",
        "animations",
    }
    if set(payload) != expected_keys:
        raise SettingsValidationError("invalid_settings_editor_document", "motion preset keys do not match fixed shape")
    normalized = _normalize_keyed_values(
        {
            "motion.posture_change_loop_count_standing": payload.get("motion.posture_change_loop_count_standing"),
            "motion.posture_change_loop_count_sitting_floor": payload.get("motion.posture_change_loop_count_sitting_floor"),
        },
        (
            "motion.posture_change_loop_count_standing",
            "motion.posture_change_loop_count_sitting_floor",
        ),
    )
    animations = payload.get("animations")
    if not isinstance(animations, list):
        raise SettingsValidationError("invalid_settings_editor_document", "motion preset animations must be array")
    normalized["animations"] = [_normalize_animation_config(animation) for animation in animations]
    return normalized


# Block: Enabled TTS provider validation
def _validate_enabled_tts_provider_settings(normalized: dict[str, Any]) -> None:
    tts_provider = str(normalized["speech.tts.provider"])
    if tts_provider == "aivis-cloud":
        required_keys = (
            "speech.tts.aivis_cloud.api_key",
            "speech.tts.aivis_cloud.endpoint_url",
            "speech.tts.aivis_cloud.model_uuid",
            "speech.tts.aivis_cloud.speaker_uuid",
        )
        for key in required_keys:
            if not normalized[key]:
                raise SettingsValidationError(
                    "invalid_settings_editor_document",
                    f"{key} is required when speech.tts.enabled is true",
                )
        return
    if tts_provider == "voicevox":
        if not normalized["speech.tts.voicevox.endpoint_url"]:
            raise SettingsValidationError(
                "invalid_settings_editor_document",
                "speech.tts.voicevox.endpoint_url is required when speech.tts.enabled is true",
            )
        return
    if tts_provider == "style-bert-vits2":
        if not normalized["speech.tts.style_bert_vits2.endpoint_url"]:
            raise SettingsValidationError(
                "invalid_settings_editor_document",
                "speech.tts.style_bert_vits2.endpoint_url is required when speech.tts.enabled is true",
            )
        return
    raise SettingsValidationError("invalid_settings_editor_document", "speech.tts.provider is invalid")


# Block: Enabled STT provider validation
def _validate_enabled_stt_provider_settings(normalized: dict[str, Any]) -> None:
    if str(normalized["speech.stt.provider"]) != "amivoice":
        raise SettingsValidationError("invalid_settings_editor_document", "speech.stt.provider is invalid")
    if not normalized["speech.stt.amivoice.api_key"]:
        raise SettingsValidationError(
            "invalid_settings_editor_document",
            "speech.stt.amivoice.api_key is required when speech.stt.enabled is true",
        )


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


# Block: Motion animation normalization
def _normalize_animation_config(animation: Any) -> dict[str, Any]:
    if not isinstance(animation, dict):
        raise SettingsValidationError("invalid_settings_editor_document", "motion preset animation must be object")
    expected_keys = {"display_name", "animation_type", "animation_name", "is_enabled"}
    if set(animation) != expected_keys:
        raise SettingsValidationError("invalid_settings_editor_document", "motion preset animation keys do not match fixed shape")
    animation_type = animation.get("animation_type")
    if isinstance(animation_type, bool) or not isinstance(animation_type, int):
        raise SettingsValidationError("invalid_settings_editor_document", "motion preset animation_type must be integer")
    if animation_type not in MOTION_ANIMATION_TYPE_VALUES:
        raise SettingsValidationError("invalid_settings_editor_document", "motion preset animation_type is invalid")
    is_enabled = animation.get("is_enabled")
    if not isinstance(is_enabled, bool):
        raise SettingsValidationError("invalid_settings_editor_document", "motion preset is_enabled must be boolean")
    return {
        "display_name": _required_string(animation.get("display_name"), "motion animation display_name"),
        "animation_type": animation_type,
        "animation_name": _required_string(animation.get("animation_name"), "motion animation animation_name"),
        "is_enabled": is_enabled,
    }


# Block: Keyed values normalization
def _normalize_keyed_values(values: dict[str, Any], required_keys: tuple[str, ...] | list[str] | set[str]) -> dict[str, Any]:
    if set(values) != set(required_keys):
        raise SettingsValidationError("invalid_settings_editor_document", "settings keys do not match fixed shape")
    normalized: dict[str, Any] = {}
    for key in required_keys:
        definition = get_setting_definition(str(key))
        value = values.get(str(key))
        _validate_type(definition, value)
        _validate_range(definition, value)
        _validate_length(definition, value)
        normalized[str(key)] = value
    return normalized


# Block: Active preset validation
def _validate_active_preset_ids(
    *,
    editor_state: dict[str, Any],
    character_presets: list[dict[str, Any]],
    behavior_presets: list[dict[str, Any]],
    conversation_presets: list[dict[str, Any]],
    memory_presets: list[dict[str, Any]],
    motion_presets: list[dict[str, Any]],
) -> None:
    mapping = (
        ("active_character_preset_id", character_presets, "character_presets"),
        ("active_behavior_preset_id", behavior_presets, "behavior_presets"),
        ("active_conversation_preset_id", conversation_presets, "conversation_presets"),
        ("active_memory_preset_id", memory_presets, "memory_presets"),
        ("active_motion_preset_id", motion_presets, "motion_presets"),
    )
    for active_key, preset_entries, field_name in mapping:
        active_preset_id = str(editor_state[active_key])
        known_ids = {str(entry["preset_id"]) for entry in preset_entries}
        if active_preset_id not in known_ids:
            raise SettingsValidationError(
                "invalid_settings_editor_document",
                f"{active_key} does not exist in {field_name}",
            )


# Block: Required string helper
def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise SettingsValidationError("invalid_settings_editor_document", f"{field_name} must be non-empty string")
    return value


# Block: Required boolean helper
def _required_boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise SettingsValidationError("invalid_settings_editor_document", f"{field_name} must be boolean")
    return value


# Block: Camera connection validation
def _validate_camera_connection_entry(camera_connection: dict[str, Any]) -> None:
    if bool(camera_connection["is_enabled"]) is not True:
        return
    for field_name in ("host", "username", "password"):
        if not camera_connection[field_name]:
            raise SettingsValidationError(
                "invalid_settings_editor_document",
                f"enabled camera_connections.{field_name} must be non-empty string",
            )


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
