from __future__ import annotations

from typing import Any

from otomekairo.capabilities import capability_manifests
from otomekairo.service.common import REQUIRED_MODEL_ROLE_NAMES, ServiceError
from otomekairo.service.config.constants import PERSONA_INITIATIVE_BASELINES


class ServiceConfigValidationMixin:
    def _validate_wake_policy(self, wake_policy: dict[str, Any]) -> None:
        if not isinstance(wake_policy, dict):
            raise ServiceError(400, "invalid_wake_policy", "wake_policy must be an object.")

        mode = wake_policy.get("mode")
        if mode not in {"disabled", "interval"}:
            raise ServiceError(400, "invalid_wake_policy_mode", "wake_policy.mode must be disabled or interval.")

        allowed_fields = {"mode", "observations", "desktop_scene_similarity_threshold"}
        if mode == "interval":
            allowed_fields.add("interval_seconds")
            interval_seconds = wake_policy.get("interval_seconds")
            if not isinstance(interval_seconds, int) or interval_seconds < 1:
                raise ServiceError(
                    400,
                    "invalid_wake_policy_interval_seconds",
                    "wake_policy.interval_seconds must be an integer >= 1.",
                )

        if "desktop_scene_similarity_threshold" in wake_policy:
            threshold = wake_policy["desktop_scene_similarity_threshold"]
            if isinstance(threshold, bool) or not isinstance(threshold, int | float) or not 0 <= threshold <= 1:
                raise ServiceError(
                    400,
                    "invalid_wake_policy_desktop_scene_similarity_threshold",
                    "wake_policy.desktop_scene_similarity_threshold must be a number between 0 and 1.",
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
            - {"persona_id", "display_name", "initiative_baseline", "persona_prompt", "expression_addon"}
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
        if definition.get("model_preset_id") != model_preset_id:
            raise ServiceError(400, "model_preset_id_mismatch", "model_preset_id must match the path.")
        display_name = definition.get("display_name")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ServiceError(400, "invalid_model_preset_display_name", "display_name is required.")
        prompt_window = definition.get("prompt_window")
        self._validate_prompt_window(prompt_window)
        roles = definition.get("roles")
        if not isinstance(roles, dict):
            raise ServiceError(400, "invalid_model_preset_roles", "roles must be an object.")

        for role_name in REQUIRED_MODEL_ROLE_NAMES:
            if role_name not in roles:
                raise ServiceError(400, "missing_model_role", f"{role_name} is required.")
            role_definition = roles[role_name]
            self._validate_model_role_definition(role_name, role_definition)

    def _validate_model_role_definition(self, role_name: str, definition: Any) -> None:
        if not isinstance(definition, dict):
            raise ServiceError(400, "invalid_model_role", f"{role_name} must be an object.")

        model = definition.get("model")
        api_base = definition.get("api_base")
        api_key = definition.get("api_key")
        reasoning_effort = definition.get("reasoning_effort")
        max_output_tokens = definition.get("max_output_tokens")
        timeout_seconds = definition.get("timeout_seconds")
        web_search_enabled = definition.get("web_search_enabled")

        if not isinstance(model, str) or not model.strip():
            raise ServiceError(400, "invalid_model_role_model", f"{role_name}.model is required.")
        if api_base is not None and not isinstance(api_base, str):
            raise ServiceError(400, "invalid_model_role_api_base", f"{role_name}.api_base must be a string.")
        if not isinstance(api_key, str):
            raise ServiceError(400, "invalid_model_role_api_key", f"{role_name}.api_key must be a string.")
        if reasoning_effort is not None and not isinstance(reasoning_effort, str):
            raise ServiceError(400, "invalid_reasoning_effort", f"{role_name}.reasoning_effort must be a string.")
        if not isinstance(max_output_tokens, int) or max_output_tokens < 1:
            raise ServiceError(
                400,
                "invalid_max_output_tokens",
                f"{role_name}.max_output_tokens must be an integer >= 1.",
            )
        if (
            timeout_seconds is not None
            and (
                isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, (int, float))
                or timeout_seconds <= 0
            )
        ):
            raise ServiceError(
                400,
                "invalid_timeout_seconds",
                f"{role_name}.timeout_seconds must be a positive number when specified.",
            )
        if not isinstance(web_search_enabled, bool):
            raise ServiceError(
                400,
                "invalid_web_search_enabled",
                f"{role_name}.web_search_enabled must be a boolean.",
            )

    def _normalize_model_preset_definition(self, definition: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            **definition,
        }
        display_name = normalized.get("display_name")
        if isinstance(display_name, str):
            normalized["display_name"] = display_name.strip()
        prompt_window = definition.get("prompt_window")
        if isinstance(prompt_window, dict):
            normalized["prompt_window"] = self._normalize_prompt_window(prompt_window)

        roles = definition.get("roles")
        if not isinstance(roles, dict):
            return normalized

        normalized_roles: dict[str, Any] = {}
        for role_name, role_definition in roles.items():
            if not isinstance(role_definition, dict):
                normalized_roles[role_name] = role_definition
                continue
            normalized_role: dict[str, Any] = {}
            for field_name in ("model", "api_base", "api_key"):
                if field_name not in role_definition:
                    continue
                value = role_definition.get(field_name)
                if isinstance(value, str):
                    trimmed_value = value.strip()
                    if field_name == "api_base" and not trimmed_value:
                        continue
                    normalized_role[field_name] = trimmed_value
                else:
                    normalized_role[field_name] = value
            reasoning_effort = role_definition.get("reasoning_effort")
            if isinstance(reasoning_effort, str):
                trimmed_reasoning_effort = reasoning_effort.strip()
                if trimmed_reasoning_effort:
                    normalized_role["reasoning_effort"] = trimmed_reasoning_effort
            max_output_tokens = role_definition.get("max_output_tokens")
            if isinstance(max_output_tokens, int):
                normalized_role["max_output_tokens"] = max_output_tokens
            timeout_seconds = role_definition.get("timeout_seconds")
            if isinstance(timeout_seconds, bool):
                pass
            elif isinstance(timeout_seconds, (int, float)):
                normalized_role["timeout_seconds"] = timeout_seconds
            web_search_enabled = role_definition.get("web_search_enabled")
            if isinstance(web_search_enabled, bool):
                normalized_role["web_search_enabled"] = web_search_enabled
            normalized_roles[role_name] = normalized_role

        normalized["roles"] = normalized_roles
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
