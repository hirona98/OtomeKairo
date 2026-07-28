from __future__ import annotations

import math
from typing import Any

from otomekairo.capabilities import capability_manifests
from otomekairo.service.common import ServiceError
from otomekairo.service.config.constants import (
    CAMERA_CONNECTOR_KINDS,
    CAMERA_DEFAULT_CLIENT_ID,
    CAMERA_DEFAULT_CONNECTOR_KIND,
    PERSONA_INITIATIVE_BASELINES,
)

TTS_ENGINES = {"voicevox", "style-bert-vits2", "aivis-cloud"}
VOICEVOX_SAMPLING_RATES = {16000, 22050, 24000, 44100, 48000}


class ServiceConfigValidationMixin:
    def _validate_console_client_id(self, client_id: Any) -> str:
        if not isinstance(client_id, str) or not client_id.strip():
            raise ServiceError(400, "invalid_console_client_id", "client_id must be a non-empty string.")
        normalized = client_id.strip()
        if len(normalized) > 128:
            raise ServiceError(400, "invalid_console_client_id", "client_id must be at most 128 characters.")
        return normalized

    def _validate_console_client_settings(self, client_id: str, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(
                400,
                "invalid_console_client_settings",
                "console client settings must be an object.",
            )
        self._validate_exact_fields(
            definition,
            {
                "client_id",
                "process",
                "display",
                "desktop_capture",
                "avatar_presentations",
                "motion",
            },
            "console_client_settings",
        )
        if definition.get("client_id") != client_id:
            raise ServiceError(
                400,
                "console_client_id_mismatch",
                "console client settings.client_id must match the route client_id.",
            )
        self._validate_console_process_settings(definition.get("process"))
        self._validate_console_display_settings(definition.get("display"))
        self._validate_console_desktop_capture_settings(definition.get("desktop_capture"))
        self._validate_console_avatar_presentations(definition.get("avatar_presentations"))
        self._validate_console_motion_settings(definition.get("motion"))

    def _validate_console_process_settings(self, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_console_process_settings", "process must be an object.")
        self._validate_exact_fields(
            definition,
            {"console_api_port", "cocoro_shell_port", "conversation_input_enabled"},
            "console_process_settings",
        )
        for field_name in ("console_api_port", "cocoro_shell_port"):
            value = definition.get(field_name)
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
                raise ServiceError(
                    400,
                    "invalid_console_process_settings",
                    f"process.{field_name} must be an integer from 1 to 65535.",
                )
        if definition["console_api_port"] == definition["cocoro_shell_port"]:
            raise ServiceError(
                400,
                "invalid_console_process_settings",
                "process ports must be different.",
            )
        if not isinstance(definition.get("conversation_input_enabled"), bool):
            raise ServiceError(
                400,
                "invalid_console_process_settings",
                "process.conversation_input_enabled must be a boolean.",
            )

    def _validate_console_display_settings(self, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_console_display_settings", "display must be an object.")
        self._validate_exact_fields(
            definition,
            {
                "restore_window_position",
                "topmost",
                "escape_cursor",
                "escape_positions",
                "touch_virtual_key_enabled",
                "virtual_key",
                "auto_move",
                "show_message_window",
                "ambient_occlusion_enabled",
                "msaa_level",
                "avatar_shadow_mode",
                "avatar_shadow_resolution",
                "background_shadow_mode",
                "background_shadow_resolution",
                "avatar_window_size",
                "avatar_position_x",
                "avatar_position_y",
                "message_window",
                "window_placements",
            },
            "console_display_settings",
        )
        for field_name in (
            "restore_window_position",
            "topmost",
            "escape_cursor",
            "touch_virtual_key_enabled",
            "auto_move",
            "show_message_window",
            "ambient_occlusion_enabled",
        ):
            if not isinstance(definition.get(field_name), bool):
                raise ServiceError(
                    400,
                    "invalid_console_display_settings",
                    f"display.{field_name} must be a boolean.",
                )
        if not isinstance(definition.get("virtual_key"), str):
            raise ServiceError(
                400,
                "invalid_console_display_settings",
                "display.virtual_key must be a string.",
            )
        for field_name in (
            "msaa_level",
            "avatar_shadow_mode",
            "avatar_shadow_resolution",
            "background_shadow_mode",
            "background_shadow_resolution",
            "avatar_window_size",
        ):
            value = definition.get(field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ServiceError(
                    400,
                    "invalid_console_display_settings",
                    f"display.{field_name} must be an integer >= 0.",
                )
        for field_name in ("avatar_position_x", "avatar_position_y"):
            if not self._is_finite_number(definition.get(field_name)):
                raise ServiceError(
                    400,
                    "invalid_console_display_settings",
                    f"display.{field_name} must be finite.",
                )
        self._validate_console_escape_positions(definition.get("escape_positions"))
        self._validate_console_message_window(definition.get("message_window"))
        self._validate_console_window_placements(definition.get("window_placements"))

    def _validate_console_escape_positions(self, entries: Any) -> None:
        if not isinstance(entries, list):
            raise ServiceError(400, "invalid_console_display_settings", "display.escape_positions must be an array.")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ServiceError(400, "invalid_console_display_settings", "escape position must be an object.")
            self._validate_exact_fields(entry, {"x", "y", "enabled"}, "console_escape_position")
            if not self._is_finite_number(entry.get("x")) or not self._is_finite_number(entry.get("y")):
                raise ServiceError(400, "invalid_console_display_settings", "escape position coordinates must be finite.")
            if not isinstance(entry.get("enabled"), bool):
                raise ServiceError(400, "invalid_console_display_settings", "escape position.enabled must be a boolean.")

    def _validate_console_message_window(self, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_console_display_settings", "display.message_window must be an object.")
        fields = {
            "max_message_count",
            "max_total_characters",
            "min_window_size",
            "max_window_size",
            "font_size",
            "horizontal_offset",
            "vertical_offset",
        }
        self._validate_exact_fields(definition, fields, "console_message_window")
        for field_name in ("max_message_count", "max_total_characters"):
            value = definition.get(field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ServiceError(400, "invalid_console_display_settings", f"message_window.{field_name} is invalid.")
        for field_name in fields - {"max_message_count", "max_total_characters"}:
            if not self._is_finite_number(definition.get(field_name)):
                raise ServiceError(400, "invalid_console_display_settings", f"message_window.{field_name} is invalid.")
        if float(definition["min_window_size"]) <= 0 or float(definition["max_window_size"]) < float(
            definition["min_window_size"]
        ):
            raise ServiceError(400, "invalid_console_display_settings", "message window size range is invalid.")
        if float(definition["font_size"]) <= 0:
            raise ServiceError(400, "invalid_console_display_settings", "message_window.font_size must be positive.")

    def _validate_console_window_placements(self, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_console_display_settings", "display.window_placements must be an object.")
        for window_key, placement in definition.items():
            if not isinstance(window_key, str) or not window_key.strip() or not isinstance(placement, dict):
                raise ServiceError(400, "invalid_console_display_settings", "window placement is invalid.")
            self._validate_exact_fields(placement, {"left", "top"}, "console_window_placement")
            if not self._is_finite_number(placement.get("left")) or not self._is_finite_number(placement.get("top")):
                raise ServiceError(400, "invalid_console_display_settings", "window placement coordinates must be finite.")

    def _validate_console_desktop_capture_settings(self, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_console_desktop_capture_settings", "desktop_capture must be an object.")
        self._validate_exact_fields(
            definition,
            {"enabled", "capture_active_window_only", "idle_timeout_minutes", "exclude_patterns"},
            "console_desktop_capture_settings",
        )
        if not isinstance(definition.get("enabled"), bool) or not isinstance(
            definition.get("capture_active_window_only"), bool
        ):
            raise ServiceError(400, "invalid_console_desktop_capture_settings", "desktop capture flags are invalid.")
        idle_timeout = definition.get("idle_timeout_minutes")
        if not isinstance(idle_timeout, int) or isinstance(idle_timeout, bool) or idle_timeout < 0:
            raise ServiceError(
                400,
                "invalid_console_desktop_capture_settings",
                "desktop_capture.idle_timeout_minutes must be an integer >= 0.",
            )
        patterns = definition.get("exclude_patterns")
        if not isinstance(patterns, list) or any(not isinstance(pattern, str) for pattern in patterns):
            raise ServiceError(
                400,
                "invalid_console_desktop_capture_settings",
                "desktop_capture.exclude_patterns must be an array of strings.",
            )

    def _validate_console_avatar_presentations(self, entries: Any) -> None:
        if not isinstance(entries, list):
            raise ServiceError(
                400,
                "invalid_console_avatar_presentations",
                "avatar_presentations must be an array.",
            )
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ServiceError(400, "invalid_console_avatar_presentations", "avatar presentation is invalid.")
            self._validate_exact_fields(
                entry,
                {
                    "avatar_id",
                    "model",
                    "convert_unlit_to_mtoon",
                    "shadow_exclusion_enabled",
                    "shadow_excluded_mesh_names",
                },
                "console_avatar_presentation",
            )
            avatar_id = entry.get("avatar_id")
            if not isinstance(avatar_id, str) or not avatar_id.startswith("avatar:") or avatar_id in seen:
                raise ServiceError(400, "invalid_console_avatar_presentations", "avatar_id is invalid or duplicated.")
            seen.add(avatar_id)
            model = entry.get("model")
            if not isinstance(model, str) or not model.strip():
                raise ServiceError(400, "invalid_console_avatar_presentations", "avatar model is required.")
            if not isinstance(entry.get("convert_unlit_to_mtoon"), bool):
                raise ServiceError(400, "invalid_console_avatar_presentations", "convert_unlit_to_mtoon is invalid.")
            if not isinstance(entry.get("shadow_exclusion_enabled"), bool):
                raise ServiceError(
                    400,
                    "invalid_console_avatar_presentations",
                    "shadow_exclusion_enabled is invalid.",
                )
            mesh_names = entry.get("shadow_excluded_mesh_names")
            if not isinstance(mesh_names, list) or any(not isinstance(name, str) for name in mesh_names):
                raise ServiceError(
                    400,
                    "invalid_console_avatar_presentations",
                    "shadow_excluded_mesh_names must be an array of strings.",
                )

    def _validate_console_motion_settings(self, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_console_motion_settings", "motion must be an object.")
        self._validate_exact_fields(
            definition,
            {"selected_animation_set_id", "animation_sets"},
            "console_motion_settings",
        )
        sets = definition.get("animation_sets")
        if not isinstance(sets, list) or not sets:
            raise ServiceError(400, "invalid_console_motion_settings", "motion.animation_sets must not be empty.")
        seen: set[str] = set()
        for animation_set in sets:
            if not isinstance(animation_set, dict):
                raise ServiceError(400, "invalid_console_motion_settings", "animation set must be an object.")
            self._validate_exact_fields(
                animation_set,
                {
                    "animation_set_id",
                    "display_name",
                    "posture_change_loop_count_standing",
                    "posture_change_loop_count_sitting_floor",
                    "animations",
                },
                "console_animation_set",
            )
            set_id = animation_set.get("animation_set_id")
            if not isinstance(set_id, str) or not set_id.startswith("animation_set:") or set_id in seen:
                raise ServiceError(400, "invalid_console_motion_settings", "animation_set_id is invalid or duplicated.")
            seen.add(set_id)
            if not isinstance(animation_set.get("display_name"), str) or not animation_set["display_name"].strip():
                raise ServiceError(400, "invalid_console_motion_settings", "animation set display_name is required.")
            for field_name in (
                "posture_change_loop_count_standing",
                "posture_change_loop_count_sitting_floor",
            ):
                value = animation_set.get(field_name)
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    raise ServiceError(400, "invalid_console_motion_settings", f"{field_name} must be >= 1.")
            animations = animation_set.get("animations")
            if not isinstance(animations, list):
                raise ServiceError(400, "invalid_console_motion_settings", "animations must be an array.")
            for animation in animations:
                self._validate_console_animation(animation)
        if definition.get("selected_animation_set_id") not in seen:
            raise ServiceError(404, "animation_set_not_found", "selected_animation_set_id does not exist.")

    def _validate_console_animation(self, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_console_motion_settings", "animation must be an object.")
        self._validate_exact_fields(
            definition,
            {"display_name", "animation_type", "animation_name", "enabled"},
            "console_animation",
        )
        for field_name in ("display_name", "animation_name"):
            if not isinstance(definition.get(field_name), str) or not definition[field_name].strip():
                raise ServiceError(400, "invalid_console_motion_settings", f"animation.{field_name} is required.")
        if definition.get("animation_type") not in {0, 1} or not isinstance(definition.get("enabled"), bool):
            raise ServiceError(400, "invalid_console_motion_settings", "animation type or enabled is invalid.")

    @staticmethod
    def _is_finite_number(value: Any) -> bool:
        return type(value) in {int, float} and math.isfinite(float(value))

    def _normalize_avatar_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            **definition,
        }
        for field_name in ("avatar_id", "display_name"):
            value = normalized.get(field_name)
            if isinstance(value, str):
                normalized[field_name] = value.strip()
        return normalized

    def _validate_avatar_definition(self, avatar_id: str, definition: dict[str, Any]) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_avatar", "avatar must be an object.")
        if definition.get("avatar_id") != avatar_id or not avatar_id.startswith("avatar:"):
            raise ServiceError(400, "avatar_id_mismatch", "avatar_id must start with avatar: and match the entry id.")
        self._validate_exact_fields(
            definition,
            {"avatar_id", "display_name", "stt", "tts"},
            "avatar",
        )
        display_name = definition.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ServiceError(400, "invalid_avatar_display_name", "avatar.display_name is required.")
        self._validate_stt_definition(definition.get("stt"))
        self._validate_tts_definition(definition.get("tts"))

    def _validate_microphone_settings(self, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_microphone_settings", "microphone_settings must be an object.")
        self._validate_exact_fields(
            definition,
            {"input_threshold_db", "speaker_recognition_threshold"},
            "microphone_settings",
        )
        input_threshold = definition.get("input_threshold_db")
        if type(input_threshold) is not int or not -50 <= input_threshold <= 0:
            raise ServiceError(
                400,
                "invalid_microphone_settings",
                "microphone_settings.input_threshold_db must be an integer from -50 to 0.",
            )
        speaker_threshold = definition.get("speaker_recognition_threshold")
        if type(speaker_threshold) not in {int, float} or not 0.1 <= float(speaker_threshold) <= 0.9:
            raise ServiceError(
                400,
                "invalid_microphone_settings",
                "microphone_settings.speaker_recognition_threshold must be from 0.1 to 0.9.",
            )

    def _validate_stt_definition(self, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_stt_settings", "avatar.stt must be an object.")
        self._validate_exact_fields(
            definition,
            {"enabled", "engine", "wake_word", "profile_id", "api_key", "language"},
            "avatar.stt",
        )
        if not isinstance(definition.get("enabled"), bool):
            raise ServiceError(400, "invalid_stt_settings", "avatar.stt.enabled must be a boolean.")
        if definition.get("engine") != "amivoice":
            raise ServiceError(400, "unsupported_stt_engine", "avatar.stt.engine must be amivoice.")
        for field_name in ("wake_word", "profile_id", "api_key", "language"):
            if not isinstance(definition.get(field_name), str):
                raise ServiceError(
                    400,
                    "invalid_stt_settings",
                    f"avatar.stt.{field_name} must be a string.",
                )
        if not definition["language"].strip():
            raise ServiceError(400, "invalid_stt_settings", "avatar.stt.language is required.")

    def _validate_tts_definition(self, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_tts_settings", "avatar.tts must be an object.")
        self._validate_exact_fields(
            definition,
            {
                "enabled",
                "engine",
                "voicevox_config",
                "style_bert_vits2_config",
                "aivis_cloud_config",
            },
            "avatar.tts",
        )
        if not isinstance(definition.get("enabled"), bool):
            raise ServiceError(400, "invalid_tts_settings", "avatar.tts.enabled must be a boolean.")
        if definition.get("engine") not in TTS_ENGINES:
            raise ServiceError(400, "unsupported_tts_engine", "avatar.tts.engine is not supported.")
        self._validate_voicevox_config(definition.get("voicevox_config"))
        self._validate_style_bert_vits2_config(definition.get("style_bert_vits2_config"))
        self._validate_aivis_cloud_config(definition.get("aivis_cloud_config"))

    def _validate_voicevox_config(self, definition: Any) -> None:
        fields = {
            "endpoint_url",
            "speaker_id",
            "speed_scale",
            "pitch_scale",
            "intonation_scale",
            "volume_scale",
            "pre_phoneme_length",
            "post_phoneme_length",
            "output_sampling_rate",
            "output_stereo",
        }
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_voicevox_config", "voicevox_config must be an object.")
        self._validate_exact_fields(definition, fields, "voicevox_config")
        self._validate_non_empty_text(definition, "endpoint_url", "voicevox_config")
        self._validate_integer_range(definition, "speaker_id", "voicevox_config", minimum=0)
        self._validate_number_range(definition, "speed_scale", "voicevox_config", minimum=0.5, maximum=2.0)
        self._validate_number_range(definition, "pitch_scale", "voicevox_config", minimum=-0.15, maximum=0.15)
        self._validate_number_range(definition, "intonation_scale", "voicevox_config", minimum=0.0, maximum=2.0)
        self._validate_number_range(definition, "volume_scale", "voicevox_config", minimum=0.0, maximum=2.0)
        self._validate_number_range(definition, "pre_phoneme_length", "voicevox_config", minimum=0.0, maximum=1.5)
        self._validate_number_range(definition, "post_phoneme_length", "voicevox_config", minimum=0.0, maximum=1.5)
        if definition.get("output_sampling_rate") not in VOICEVOX_SAMPLING_RATES:
            raise ServiceError(
                400,
                "invalid_voicevox_config",
                "voicevox_config.output_sampling_rate is not supported.",
            )
        if not isinstance(definition.get("output_stereo"), bool):
            raise ServiceError(400, "invalid_voicevox_config", "voicevox_config.output_stereo must be a boolean.")

    def _validate_style_bert_vits2_config(self, definition: Any) -> None:
        text_fields = {"endpoint_url", "model_name", "speaker_name", "style", "language"}
        optional_text_fields = {"assist_text", "reference_audio_path"}
        number_fields = {
            "style_weight",
            "sdp_ratio",
            "noise",
            "noise_w",
            "length",
            "split_interval",
            "assist_text_weight",
        }
        fields = text_fields | optional_text_fields | number_fields | {"model_id", "speaker_id", "auto_split"}
        if not isinstance(definition, dict):
            raise ServiceError(
                400,
                "invalid_style_bert_vits2_config",
                "style_bert_vits2_config must be an object.",
            )
        self._validate_exact_fields(definition, fields, "style_bert_vits2_config")
        for field_name in text_fields:
            self._validate_non_empty_text(definition, field_name, "style_bert_vits2_config")
        for field_name in optional_text_fields:
            if not isinstance(definition.get(field_name), str):
                raise ServiceError(
                    400,
                    "invalid_style_bert_vits2_config",
                    f"style_bert_vits2_config.{field_name} must be a string.",
                )
        self._validate_integer_range(definition, "model_id", "style_bert_vits2_config", minimum=0)
        self._validate_integer_range(definition, "speaker_id", "style_bert_vits2_config", minimum=0)
        for field_name in number_fields:
            value = definition.get(field_name)
            if type(value) not in {int, float}:
                raise ServiceError(
                    400,
                    "invalid_style_bert_vits2_config",
                    f"style_bert_vits2_config.{field_name} must be a number.",
                )
        if not isinstance(definition.get("auto_split"), bool):
            raise ServiceError(
                400,
                "invalid_style_bert_vits2_config",
                "style_bert_vits2_config.auto_split must be a boolean.",
            )

    def _validate_aivis_cloud_config(self, definition: Any) -> None:
        fields = {
            "api_key",
            "endpoint_url",
            "model_uuid",
            "speaker_uuid",
            "style_id",
            "style_name",
            "use_ssml",
            "language",
            "speaking_rate",
            "emotional_intensity",
            "tempo_dynamics",
            "pitch",
            "volume",
            "output_format",
            "output_bitrate",
            "output_sampling_rate",
            "output_audio_channels",
        }
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_aivis_cloud_config", "aivis_cloud_config must be an object.")
        self._validate_exact_fields(definition, fields, "aivis_cloud_config")
        for field_name in (
            "api_key",
            "endpoint_url",
            "model_uuid",
            "speaker_uuid",
            "style_name",
            "language",
            "output_format",
            "output_audio_channels",
        ):
            if not isinstance(definition.get(field_name), str):
                raise ServiceError(
                    400,
                    "invalid_aivis_cloud_config",
                    f"aivis_cloud_config.{field_name} must be a string.",
                )
        for field_name in ("language", "output_format", "output_audio_channels"):
            if not definition[field_name].strip():
                raise ServiceError(
                    400,
                    "invalid_aivis_cloud_config",
                    f"aivis_cloud_config.{field_name} is required.",
                )
        if not isinstance(definition.get("use_ssml"), bool):
            raise ServiceError(400, "invalid_aivis_cloud_config", "aivis_cloud_config.use_ssml must be a boolean.")
        self._validate_integer_range(definition, "style_id", "aivis_cloud_config", minimum=0)
        self._validate_integer_range(definition, "output_bitrate", "aivis_cloud_config", minimum=0)
        self._validate_integer_range(definition, "output_sampling_rate", "aivis_cloud_config", minimum=1)
        self._validate_number_range(definition, "speaking_rate", "aivis_cloud_config", minimum=0.5, maximum=2.0)
        self._validate_number_range(
            definition,
            "emotional_intensity",
            "aivis_cloud_config",
            minimum=0.0,
            maximum=2.0,
        )
        self._validate_number_range(definition, "tempo_dynamics", "aivis_cloud_config", minimum=0.0, maximum=2.0)
        self._validate_number_range(definition, "pitch", "aivis_cloud_config", minimum=-1.0, maximum=1.0)
        self._validate_number_range(definition, "volume", "aivis_cloud_config", minimum=0.0, maximum=2.0)

    def _validate_exact_fields(self, definition: dict[str, Any], fields: set[str], label: str) -> None:
        missing_fields = sorted(fields - set(definition))
        unsupported_fields = sorted(set(definition) - fields)
        if missing_fields or unsupported_fields:
            details = []
            if missing_fields:
                details.append(f"missing: {', '.join(missing_fields)}")
            if unsupported_fields:
                details.append(f"unsupported: {', '.join(unsupported_fields)}")
            raise ServiceError(
                400,
                f"invalid_{label.replace('.', '_')}_fields",
                f"{label} fields are invalid ({'; '.join(details)}).",
            )

    def _validate_non_empty_text(self, definition: dict[str, Any], key: str, label: str) -> None:
        value = definition.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ServiceError(400, f"invalid_{label}", f"{label}.{key} is required.")

    def _validate_integer_range(
        self,
        definition: dict[str, Any],
        key: str,
        label: str,
        *,
        minimum: int,
    ) -> None:
        value = definition.get(key)
        if type(value) is not int or value < minimum:
            raise ServiceError(400, f"invalid_{label}", f"{label}.{key} must be an integer >= {minimum}.")

    def _validate_number_range(
        self,
        definition: dict[str, Any],
        key: str,
        label: str,
        *,
        minimum: float,
        maximum: float,
    ) -> None:
        value = definition.get(key)
        if type(value) not in {int, float} or not minimum <= float(value) <= maximum:
            raise ServiceError(
                400,
                f"invalid_{label}",
                f"{label}.{key} must be from {minimum} to {maximum}.",
            )

    def _validate_thinking_speech_level(self, value: Any) -> None:
        if type(value) is not int or value < 1 or value > 10:
            raise ServiceError(
                400,
                "invalid_thinking_speech_level",
                "thinking_speech_level must be an integer from 1 to 10.",
            )

    def _validate_wake_policy(self, wake_policy: dict[str, Any]) -> None:
        if not isinstance(wake_policy, dict):
            raise ServiceError(400, "invalid_wake_policy", "wake_policy must be an object.")

        mode = wake_policy.get("mode")
        if mode not in {"disabled", "interval"}:
            raise ServiceError(400, "invalid_wake_policy_mode", "wake_policy.mode must be disabled or interval.")

        allowed_fields = {"mode", "interval_seconds", "observations"}
        interval_seconds = wake_policy.get("interval_seconds")
        if not isinstance(interval_seconds, int) or interval_seconds < 1:
            raise ServiceError(
                400,
                "invalid_wake_policy_interval_seconds",
                "wake_policy.interval_seconds must be an integer >= 1.",
            )

        if "observations" in wake_policy:
            self._validate_wake_policy_observations(wake_policy["observations"])

        unsupported_fields = sorted(set(wake_policy.keys()) - allowed_fields)
        if unsupported_fields:
            raise ServiceError(
                400,
                "unsupported_wake_policy_fields",
                f"wake_policy has unsupported fields: {', '.join(unsupported_fields)}.",
            )

    def _validate_wake_policy_observations(self, observations: Any) -> None:
        if not isinstance(observations, list):
            raise ServiceError(
                400,
                "invalid_wake_policy_observations",
                "wake_policy.observations must be an array.",
            )
        seen_ids: set[str] = set()
        manifests = capability_manifests()
        for index, observation in enumerate(observations):
            label = f"wake_policy.observations[{index}]"
            if not isinstance(observation, dict):
                raise ServiceError(
                    400,
                    "invalid_wake_policy_observation",
                    f"{label} must be an object.",
                )
            supported_fields = {"observation_id", "enabled", "capability_id", "input"}
            unsupported_fields = sorted(set(observation.keys()) - supported_fields)
            if unsupported_fields:
                raise ServiceError(
                    400,
                    "unsupported_wake_policy_observation_fields",
                    f"{label} has unsupported fields: {', '.join(unsupported_fields)}.",
                )
            observation_id = observation.get("observation_id")
            if not isinstance(observation_id, str) or not observation_id.strip():
                raise ServiceError(
                    400,
                    "invalid_wake_policy_observation_id",
                    f"{label}.observation_id must be a non-empty string.",
                )
            normalized_observation_id = observation_id.strip()
            if normalized_observation_id in seen_ids:
                raise ServiceError(
                    400,
                    "duplicate_wake_policy_observation_id",
                    f"{label}.observation_id is duplicated.",
                )
            seen_ids.add(normalized_observation_id)
            enabled = observation.get("enabled")
            if not isinstance(enabled, bool):
                raise ServiceError(
                    400,
                    "invalid_wake_policy_observation_enabled",
                    f"{label}.enabled must be a boolean.",
                )
            capability_id = observation.get("capability_id")
            if capability_id != "vision.capture":
                raise ServiceError(
                    400,
                    "unsupported_wake_policy_observation_capability",
                    f"{label}.capability_id must be vision.capture.",
                )
            input_payload = observation.get("input")
            if not isinstance(input_payload, dict):
                raise ServiceError(
                    400,
                    "invalid_wake_policy_observation_input",
                    f"{label}.input must be an object.",
                )
            try:
                self._validate_capability_payload(
                    payload=input_payload,
                    schema=manifests["vision.capture"].get("input_schema"),
                    label=f"{label}.input",
                )
            except ValueError as exc:
                raise ServiceError(
                    400,
                    "invalid_wake_policy_observation_input",
                    str(exc),
                ) from exc

    def _validate_persona_definition(self, persona_id: str, definition: dict[str, Any]) -> None:
        if definition.get("persona_id") != persona_id:
            raise ServiceError(400, "persona_id_mismatch", "persona_id must match the path.")
        unsupported_fields = sorted(
            set(definition.keys())
            - {
                "persona_id",
                "display_name",
                "initiative_baseline",
                "persona_prompt",
                "expression_addon",
            }
        )
        if unsupported_fields:
            raise ServiceError(
                400,
                "unsupported_persona_field",
                f"{unsupported_fields[0]} is not supported in persona definitions.",
            )
        display_name = definition.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ServiceError(400, "invalid_persona_display_name", "display_name is required.")
        persona_prompt = definition.get("persona_prompt")
        if not isinstance(persona_prompt, str) or not persona_prompt.strip():
            raise ServiceError(400, "invalid_persona_prompt", "persona_prompt is required.")
        initiative_baseline = definition.get("initiative_baseline")
        if initiative_baseline not in PERSONA_INITIATIVE_BASELINES:
            raise ServiceError(
                400,
                "invalid_initiative_baseline",
                "initiative_baseline must be low, medium, or high.",
            )
        expression_addon = definition.get("expression_addon")
        if expression_addon is not None and not isinstance(expression_addon, str):
            raise ServiceError(400, "invalid_expression_addon", "expression_addon must be a string.")

    def _validate_camera_source_definition(self, vision_source_id: str, definition: dict[str, Any]) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_camera_source", "camera_source must be an object.")
        if not isinstance(vision_source_id, str) or not vision_source_id.startswith("vision_source:"):
            raise ServiceError(
                400,
                "invalid_camera_source_field",
                "camera_source.vision_source_id must start with vision_source:.",
            )
        supported_fields = {
            "vision_source_id",
            "connector_kind",
            "client_id",
            "kind",
            "source_owner",
            "enabled",
            "display_name",
            "connection",
            "watcher",
        }
        unsupported_fields = sorted(set(definition.keys()) - supported_fields)
        if unsupported_fields:
            raise ServiceError(
                400,
                "unsupported_camera_source_field",
                f"camera_source has unsupported fields: {', '.join(unsupported_fields)}.",
            )
        connector_kind = definition.get("connector_kind")
        if connector_kind not in CAMERA_CONNECTOR_KINDS:
            raise ServiceError(
                400,
                "unsupported_camera_connector_kind",
                "camera_source.connector_kind is not supported.",
            )
        self._validate_required_text_field(definition, "client_id", "camera_source.client_id")
        enabled = definition.get("enabled")
        if not isinstance(enabled, bool):
            raise ServiceError(400, "invalid_camera_source_field", "camera_source.enabled must be a boolean.")
        self._validate_required_text_field(definition, "display_name", "camera_source.display_name")
        if definition.get("kind") != "camera":
            raise ServiceError(400, "invalid_camera_source_field", "camera_source.kind must be camera.")
        if definition.get("source_owner") != "self":
            raise ServiceError(400, "invalid_camera_source_field", "camera_source.source_owner must be self.")

        connection = definition.get("connection")
        if not isinstance(connection, dict):
            raise ServiceError(400, "invalid_camera_source_field", "camera_source.connection must be an object.")
        self._validate_required_text_field(connection, "host", "camera_source.connection.host")
        self._validate_required_text_field(
            connection,
            "camera_username",
            "camera_source.connection.camera_username",
        )
        self._validate_required_text_field(
            connection,
            "camera_password",
            "camera_source.connection.camera_password",
        )
        self._validate_allowed_fields(
            connection,
            {"host", "camera_username", "camera_password"},
            "camera_source.connection",
        )
        watcher = definition.get("watcher")
        if watcher is not None:
            self._validate_camera_source_watcher_definition(watcher)

    def _normalize_camera_source_definition(self, vision_source_id: str, definition: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            "vision_source_id": vision_source_id,
            "connector_kind": definition.get("connector_kind", CAMERA_DEFAULT_CONNECTOR_KIND),
            "client_id": definition.get("client_id", CAMERA_DEFAULT_CLIENT_ID),
            "kind": "camera",
            "source_owner": "self",
            "enabled": definition.get("enabled"),
            "display_name": definition.get("display_name"),
            "connection": definition.get("connection"),
        }
        normalized["watcher"] = self._normalize_camera_source_watcher_definition(
            definition.get("watcher") if "watcher" in definition else None,
            vision_source_id=vision_source_id,
        )
        for field_name in ("vision_source_id", "connector_kind", "client_id", "display_name"):
            value = normalized.get(field_name)
            if isinstance(value, str):
                normalized[field_name] = value.strip()

        connection = definition.get("connection")
        if isinstance(connection, dict):
            normalized["connection"] = self._normalize_text_fields(
                connection,
                ("host", "camera_username", "camera_password"),
            )
        return normalized

    def _normalize_camera_source_watcher_definition(
        self,
        watcher: Any,
        *,
        vision_source_id: str,
    ) -> dict[str, Any]:
        if watcher is None:
            return self._default_camera_source_watcher_definition(vision_source_id)
        if not isinstance(watcher, dict):
            return watcher
        normalized = dict(watcher)
        for field_name in ("kind",):
            value = normalized.get(field_name)
            if isinstance(value, str):
                normalized[field_name] = value.strip()
        normalized["watcher_id"] = self._default_camera_source_watcher_id(vision_source_id)
        normalized.setdefault("enabled", False)
        normalized.setdefault("kind", "tapo_c220_motion")
        normalized.setdefault("poll_interval_seconds", 10)
        normalized.setdefault("min_wake_interval_seconds", 30)
        normalized.setdefault("motion_ratio_threshold", 0.2)
        normalized.setdefault("pixel_diff_threshold", 10)
        normalized.setdefault("resize_width", 320)
        return normalized

    def _default_camera_source_watcher_definition(self, vision_source_id: str) -> dict[str, Any]:
        return {
            "enabled": False,
            "watcher_id": self._default_camera_source_watcher_id(vision_source_id),
            "kind": "tapo_c220_motion",
            "poll_interval_seconds": 10,
            "min_wake_interval_seconds": 30,
            "motion_ratio_threshold": 0.2,
            "pixel_diff_threshold": 10,
            "resize_width": 320,
        }

    def _default_camera_source_watcher_id(self, vision_source_id: str) -> str:
        source_id = (
            vision_source_id
            if isinstance(vision_source_id, str) and vision_source_id.strip()
            else "vision_source:camera"
        )
        safe_suffix = self._camera_source_identifier_suffix(source_id.removeprefix("vision_source:"))
        return f"watcher:{safe_suffix or 'camera'}"

    def _camera_source_id_from_display_name(self, display_name: Any) -> str:
        if not isinstance(display_name, str) or not display_name.strip():
            raise ServiceError(
                400,
                "invalid_camera_source_field",
                "camera_source.display_name must be a non-empty string.",
            )
        suffix = self._camera_source_identifier_suffix(display_name)
        return f"vision_source:{suffix or 'camera'}"

    def _camera_source_identifier_suffix(self, value: str) -> str:
        return "".join(
            character if character.isalnum() or character in "._-" else "_"
            for character in value.strip()
        ).strip("_")

    def _validate_camera_source_watcher_definition(self, watcher: Any) -> None:
        if not isinstance(watcher, dict):
            raise ServiceError(400, "invalid_camera_source_watcher", "camera_source.watcher must be an object.")
        supported_fields = {
            "enabled",
            "watcher_id",
            "kind",
            "poll_interval_seconds",
            "min_wake_interval_seconds",
            "motion_ratio_threshold",
            "pixel_diff_threshold",
            "resize_width",
        }
        unsupported_fields = sorted(set(watcher.keys()) - supported_fields)
        if unsupported_fields:
            raise ServiceError(
                400,
                "unsupported_camera_source_watcher_field",
                f"camera_source.watcher has unsupported fields: {', '.join(unsupported_fields)}.",
            )
        if not isinstance(watcher.get("enabled"), bool):
            raise ServiceError(400, "invalid_camera_source_watcher", "camera_source.watcher.enabled must be a boolean.")
        watcher_id = watcher.get("watcher_id")
        if not isinstance(watcher_id, str) or not watcher_id.strip() or not watcher_id.strip().startswith("watcher:"):
            raise ServiceError(
                400,
                "invalid_camera_source_watcher",
                "camera_source.watcher.watcher_id must start with watcher:.",
            )
        if watcher.get("kind") != "tapo_c220_motion":
            raise ServiceError(
                400,
                "unsupported_camera_source_watcher_kind",
                "camera_source.watcher.kind must be tapo_c220_motion.",
            )
        self._validate_positive_number_field(
            watcher,
            "poll_interval_seconds",
            "camera_source.watcher.poll_interval_seconds",
            minimum=0.2,
        )
        self._validate_positive_number_field(
            watcher,
            "min_wake_interval_seconds",
            "camera_source.watcher.min_wake_interval_seconds",
            minimum=1.0,
        )
        self._validate_ratio_field(
            watcher,
            "motion_ratio_threshold",
            "camera_source.watcher.motion_ratio_threshold",
        )
        pixel_diff_threshold = watcher.get("pixel_diff_threshold")
        if type(pixel_diff_threshold) is not int or pixel_diff_threshold < 1 or pixel_diff_threshold > 255:
            raise ServiceError(
                400,
                "invalid_camera_source_watcher",
                "camera_source.watcher.pixel_diff_threshold must be an integer from 1 to 255.",
            )
        resize_width = watcher.get("resize_width")
        if type(resize_width) is not int or resize_width < 64 or resize_width > 1920:
            raise ServiceError(
                400,
                "invalid_camera_source_watcher",
                "camera_source.watcher.resize_width must be an integer from 64 to 1920.",
            )
    def _validate_positive_number_field(
        self,
        definition: dict[str, Any],
        key: str,
        label: str,
        *,
        minimum: float,
    ) -> None:
        value = definition.get(key)
        if type(value) not in {int, float} or float(value) < minimum:
            raise ServiceError(400, "invalid_camera_source_watcher", f"{label} must be >= {minimum}.")

    def _validate_ratio_field(self, definition: dict[str, Any], key: str, label: str) -> None:
        value = definition.get(key)
        if type(value) not in {int, float} or float(value) <= 0.0 or float(value) > 1.0:
            raise ServiceError(400, "invalid_camera_source_watcher", f"{label} must be > 0 and <= 1.")

    def _validate_required_text_field(self, definition: dict[str, Any], key: str, label: str) -> None:
        value = definition.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ServiceError(400, "invalid_camera_source_field", f"{label} must be a non-empty string.")

    def _validate_allowed_fields(self, definition: dict[str, Any], allowed_fields: set[str], label: str) -> None:
        unsupported_fields = sorted(set(definition.keys()) - allowed_fields)
        if unsupported_fields:
            raise ServiceError(
                400,
                "unsupported_camera_source_field",
                f"{label} has unsupported fields: {', '.join(unsupported_fields)}.",
            )

    def _normalize_text_fields(self, definition: dict[str, Any], field_names: tuple[str, ...]) -> dict[str, Any]:
        normalized = dict(definition)
        for field_name in field_names:
            value = normalized.get(field_name)
            if isinstance(value, str):
                normalized[field_name] = value.strip()
        return normalized

    def _normalize_persona_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            **definition,
        }
        for field_name in ("display_name", "initiative_baseline", "persona_prompt", "expression_addon"):
            value = normalized.get(field_name)
            if not isinstance(value, str):
                continue
            normalized[field_name] = value.strip()
        return normalized

    def _validate_memory_set_definition(self, memory_set_id: Any, definition: dict[str, Any]) -> None:
        if not isinstance(memory_set_id, str) or not memory_set_id:
            raise ServiceError(400, "memory_set_id_mismatch", "memory_set_id must match the path.")
        if definition.get("memory_set_id") != memory_set_id:
            raise ServiceError(400, "memory_set_id_mismatch", "memory_set_id must match the path.")
        display_name = definition.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ServiceError(400, "invalid_memory_set_display_name", "display_name is required.")
        self._validate_embedding_definition("memory_set.embedding", definition.get("embedding"))

    def _validate_model_preset_definition(self, model_preset_id: str, definition: dict[str, Any]) -> None:
        allowed_fields = {
            "model_preset_id",
            "display_name",
            "prompt_window",
            "model",
            "api_base",
            "api_key",
            "reasoning_effort",
            "max_output_tokens",
            "timeout_seconds",
            "web_search_enabled",
        }
        unsupported_fields = sorted(set(definition) - allowed_fields)
        if unsupported_fields:
            raise ServiceError(
                400,
                "unsupported_model_preset_fields",
                f"model_preset has unsupported fields: {', '.join(unsupported_fields)}.",
            )
        if definition.get("model_preset_id") != model_preset_id:
            raise ServiceError(400, "model_preset_id_mismatch", "model_preset_id must match the path.")
        display_name = definition.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ServiceError(400, "invalid_model_preset_display_name", "display_name is required.")
        prompt_window = definition.get("prompt_window")
        self._validate_prompt_window(prompt_window)
        model = definition.get("model")
        api_base = definition.get("api_base")
        api_key = definition.get("api_key")
        reasoning_effort = definition.get("reasoning_effort")
        max_output_tokens = definition.get("max_output_tokens")
        timeout_seconds = definition.get("timeout_seconds")
        web_search_enabled = definition.get("web_search_enabled")

        if not isinstance(model, str) or not model.strip():
            raise ServiceError(400, "invalid_model_preset_model", "model_preset.model is required.")
        if api_base is not None and not isinstance(api_base, str):
            raise ServiceError(400, "invalid_model_preset_api_base", "model_preset.api_base must be a string.")
        if not isinstance(api_key, str):
            raise ServiceError(400, "invalid_model_preset_api_key", "model_preset.api_key must be a string.")
        if reasoning_effort is not None and (
            not isinstance(reasoning_effort, str) or not reasoning_effort.strip()
        ):
            raise ServiceError(400, "invalid_reasoning_effort", "reasoning_effort must be a non-empty string.")
        if type(max_output_tokens) is not int or max_output_tokens < 1:
            raise ServiceError(
                400,
                "invalid_max_output_tokens",
                "max_output_tokens must be an integer >= 1.",
            )
        if type(timeout_seconds) is not int or timeout_seconds < 1:
            raise ServiceError(
                400,
                "invalid_timeout_seconds",
                "timeout_seconds must be an integer >= 1.",
            )
        if not isinstance(web_search_enabled, bool):
            raise ServiceError(
                400,
                "invalid_web_search_enabled",
                "web_search_enabled must be a boolean.",
            )

    def _normalize_model_preset_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        known_fields = {
            "model_preset_id",
            "display_name",
            "prompt_window",
            "model",
            "api_base",
            "api_key",
            "reasoning_effort",
            "max_output_tokens",
            "timeout_seconds",
            "web_search_enabled",
        }
        # 未知fieldはvalidatorへ残し、旧roles構造を黙って受理しない。
        normalized: dict[str, Any] = {
            key: value for key, value in definition.items() if key not in known_fields
        }
        for field_name in ("model_preset_id", "display_name"):
            value = definition.get(field_name)
            normalized[field_name] = value.strip() if isinstance(value, str) else value
        display_name = normalized.get("display_name")
        if isinstance(display_name, str):
            normalized["display_name"] = display_name.strip()
        prompt_window = definition.get("prompt_window")
        if isinstance(prompt_window, dict):
            normalized["prompt_window"] = self._normalize_prompt_window(prompt_window)
        for field_name in ("model", "api_base", "api_key", "reasoning_effort"):
            if field_name not in definition:
                continue
            value = definition.get(field_name)
            if isinstance(value, str):
                value = value.strip()
                if field_name in {"api_base", "reasoning_effort"} and not value:
                    continue
            normalized[field_name] = value
        for field_name in ("max_output_tokens", "timeout_seconds", "web_search_enabled"):
            if field_name in definition:
                normalized[field_name] = definition[field_name]
        return normalized

    def _normalize_memory_set_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            **definition,
        }
        display_name = normalized.get("display_name")
        if isinstance(display_name, str):
            normalized["display_name"] = display_name.strip()

        embedding = definition.get("embedding")
        if isinstance(embedding, dict):
            normalized["embedding"] = self._normalize_embedding_definition(embedding)
        return normalized

    def _normalize_embedding_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for field_name in ("model", "api_base", "api_key"):
            if field_name not in definition:
                continue
            value = definition.get(field_name)
            if isinstance(value, str):
                trimmed_value = value.strip()
                if field_name == "api_base" and not trimmed_value:
                    continue
                normalized[field_name] = trimmed_value
            else:
                normalized[field_name] = value
        embedding_dimension = definition.get("embedding_dimension")
        if isinstance(embedding_dimension, int):
            normalized["embedding_dimension"] = embedding_dimension
        return normalized

    def _validate_prompt_window(self, prompt_window: Any) -> None:
        if not isinstance(prompt_window, dict):
            raise ServiceError(400, "invalid_prompt_window", "prompt_window must be an object.")

        recent_turn_limit = prompt_window.get("recent_turn_limit")
        recent_turn_minutes = prompt_window.get("recent_turn_minutes")
        if not isinstance(recent_turn_limit, int) or recent_turn_limit < 1:
            raise ServiceError(
                400,
                "invalid_recent_turn_limit",
                "prompt_window.recent_turn_limit must be an integer >= 1.",
            )
        if not isinstance(recent_turn_minutes, int) or recent_turn_minutes < 1:
            raise ServiceError(
                400,
                "invalid_recent_turn_minutes",
                "prompt_window.recent_turn_minutes must be an integer >= 1.",
            )

    def _normalize_prompt_window(self, prompt_window: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for field_name in ("recent_turn_limit", "recent_turn_minutes"):
            value = prompt_window.get(field_name)
            if isinstance(value, int):
                normalized[field_name] = value
        return normalized

    def _validate_embedding_definition(self, field_path: str, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_embedding_definition", f"{field_path} must be an object.")

        model = definition.get("model")
        api_base = definition.get("api_base")
        api_key = definition.get("api_key")
        embedding_dimension = definition.get("embedding_dimension")

        if not isinstance(model, str) or not model.strip():
            raise ServiceError(400, "invalid_embedding_model", f"{field_path}.model is required.")
        if api_base is not None and not isinstance(api_base, str):
            raise ServiceError(400, "invalid_embedding_api_base", f"{field_path}.api_base must be a string.")
        if not isinstance(api_key, str):
            raise ServiceError(400, "invalid_embedding_api_key", f"{field_path}.api_key must be a string.")
        if not isinstance(embedding_dimension, int) or embedding_dimension < 1:
            raise ServiceError(
                400,
                "invalid_embedding_dimension",
                f"{field_path}.embedding_dimension must be an integer >= 1.",
            )
