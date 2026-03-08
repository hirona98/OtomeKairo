"""Project persona state into compact prompt-facing data."""

from __future__ import annotations

from typing import Any


# Block: Projection limits
BIAS_SALIENCE_THRESHOLD = 0.15
BIAS_NEUTRAL_THRESHOLD = 0.05
MAX_TRAIT_ITEMS = 7
MAX_PREFERENCE_ITEMS = 4
MAX_BIAS_ITEMS = 3
MAX_HABIT_ITEMS = 3


# Block: Label maps
TRAIT_DIRECTION_LABELS = {
    "sociability": ("社交的", "一人で整えやすい"),
    "caution": ("慎重", "即断寄り"),
    "curiosity": ("探索的", "既知優先"),
    "persistence": ("粘り強い", "切り替えが早い"),
    "warmth": ("親和的", "距離を保ちやすい"),
    "assertiveness": ("はっきり主張", "控えめ"),
    "novelty_preference": ("新規志向", "定番志向"),
}
BIAS_LABELS = {
    "caution_bias": "慎重化",
    "approach_bias": "接近",
    "avoidance_bias": "回避",
    "speech_intensity_bias": "発話強度",
    "task_progress_bias": "タスク継続",
    "exploration_bias": "探索",
    "maintenance_bias": "自己整備",
    "social_bias": "社会接近",
}


# Block: Public projection builder
def build_persona_prompt_projection(*, selection_profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "salient_traits": _project_traits(selection_profile["trait_values"]),
        "interaction_style": _project_interaction_style(selection_profile["interaction_style"]),
        "learned_preferences": _project_preferences(selection_profile["learned_preferences"]),
        "learned_aversions": _project_preferences(selection_profile["learned_aversions"]),
        "habit_biases": _project_habit_biases(selection_profile["habit_biases"]),
        "emotion_bias": _project_biases(selection_profile["emotion_bias"]),
        "drive_bias": _project_biases(selection_profile["drive_bias"]),
    }


# Block: Trait projection
def _project_traits(trait_values: Any) -> list[dict[str, Any]]:
    if not isinstance(trait_values, dict):
        raise RuntimeError("selection_profile.trait_values must be an object")
    projected = []
    for trait_name, raw_value in trait_values.items():
        value = _normalized_number(raw_value, field_name=f"trait_values.{trait_name}")
        projected.append(
            {
                "trait_name": str(trait_name),
                "value": round(value, 2),
                "direction_label": _trait_direction_label(trait_name=str(trait_name), value=value),
            }
        )
    projected.sort(key=lambda item: (-abs(float(item["value"])), str(item["trait_name"])))
    return projected[:MAX_TRAIT_ITEMS]


def _trait_direction_label(*, trait_name: str, value: float) -> str:
    labels = TRAIT_DIRECTION_LABELS.get(trait_name)
    if labels is None:
        return "中立寄り"
    if value > 0:
        return labels[0]
    if value < 0:
        return labels[1]
    return "中立寄り"


# Block: Interaction style projection
def _project_interaction_style(interaction_style: Any) -> dict[str, str]:
    if not isinstance(interaction_style, dict):
        raise RuntimeError("selection_profile.interaction_style must be an object")
    projected: dict[str, str] = {}
    for field_name in ("speech_tone", "distance_style", "confirmation_style", "response_pace"):
        raw_value = interaction_style.get(field_name)
        if not isinstance(raw_value, str) or not raw_value:
            raise RuntimeError(f"selection_profile.interaction_style.{field_name} must be string")
        projected[field_name] = raw_value
    return projected


# Block: Preference projection
def _project_preferences(preferences: Any) -> list[dict[str, Any]]:
    if not isinstance(preferences, list):
        raise RuntimeError("selection_profile preferences must be a list")
    projected = []
    for entry in preferences:
        if not isinstance(entry, dict):
            raise RuntimeError("selection_profile preferences must contain only objects")
        domain = entry.get("domain")
        target_key = entry.get("target_key")
        weight = entry.get("weight")
        evidence_count = entry.get("evidence_count")
        if (
            not isinstance(domain, str)
            or not domain
            or not isinstance(target_key, str)
            or not target_key
            or not isinstance(evidence_count, int)
            or isinstance(evidence_count, bool)
        ):
            raise RuntimeError("selection_profile preference entry is invalid")
        projected.append(
            {
                "domain": domain,
                "target_key": target_key,
                "weight": round(_normalized_number(weight, field_name=f"{domain}.{target_key}.weight"), 2),
                "evidence_count": evidence_count,
            }
        )
    projected.sort(
        key=lambda item: (-abs(float(item["weight"])), -int(item["evidence_count"]), str(item["domain"]), str(item["target_key"]))
    )
    return projected[:MAX_PREFERENCE_ITEMS]


# Block: Habit projection
def _project_habit_biases(habit_biases: Any) -> dict[str, list[str]]:
    if not isinstance(habit_biases, dict):
        raise RuntimeError("selection_profile.habit_biases must be an object")
    return {
        "preferred_action_types": _string_list(
            habit_biases.get("preferred_action_types"),
            field_name="selection_profile.habit_biases.preferred_action_types",
        )[:MAX_HABIT_ITEMS],
        "preferred_observation_kinds": _string_list(
            habit_biases.get("preferred_observation_kinds"),
            field_name="selection_profile.habit_biases.preferred_observation_kinds",
        )[:MAX_HABIT_ITEMS],
        "avoided_action_styles": _string_list(
            habit_biases.get("avoided_action_styles"),
            field_name="selection_profile.habit_biases.avoided_action_styles",
        )[:MAX_HABIT_ITEMS],
    }


# Block: Bias projection
def _project_biases(biases: Any) -> list[dict[str, Any]]:
    if not isinstance(biases, dict):
        raise RuntimeError("selection_profile biases must be an object")
    projected = []
    for bias_name, raw_value in biases.items():
        value = _normalized_number(raw_value, field_name=f"selection_profile.{bias_name}")
        projected.append(
            {
                "bias_name": str(bias_name),
                "value": round(value, 2),
                "label": BIAS_LABELS.get(str(bias_name), str(bias_name)),
            }
        )
    projected.sort(key=lambda item: (-abs(float(item["value"])), str(item["bias_name"])))
    salient = [
        item
        for item in projected
        if abs(float(item["value"])) >= BIAS_SALIENCE_THRESHOLD
    ][:MAX_BIAS_ITEMS]
    if salient:
        return salient
    if projected and abs(float(projected[0]["value"])) >= BIAS_NEUTRAL_THRESHOLD:
        return projected[:1]
    return []


# Block: Primitive validators
def _normalized_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{field_name} must be numeric")
    normalized = float(value)
    if normalized < -1.0 or normalized > 1.0:
        raise RuntimeError(f"{field_name} must be within -1.0..1.0")
    return normalized


def _string_list(value: Any, *, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise RuntimeError(f"{field_name} must be a list")
    projected: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise RuntimeError(f"{field_name} must contain only non-empty strings")
        projected.append(item)
    return projected
