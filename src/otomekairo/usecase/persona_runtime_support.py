"""Build persona-aware short-cycle support objects."""

from __future__ import annotations

from typing import Any


# Block: Attention weights
ATTENTION_URGENCY_WEIGHT = 0.24
ATTENTION_TASK_CONTINUITY_WEIGHT = 0.20
ATTENTION_RELATIONSHIP_SALIENCE_WEIGHT = 0.18
ATTENTION_PERSONALITY_FIT_WEIGHT = 0.16
ATTENTION_EXPERIENCE_BIAS_WEIGHT = 0.12
ATTENTION_EXPLICITNESS_WEIGHT = 0.07
ATTENTION_NOVELTY_WEIGHT = 0.03

# Block: Self initiated weights
SELF_INITIATED_TASK_PROGRESS_WEIGHT = 0.30
SELF_INITIATED_RELATIONSHIP_CARE_WEIGHT = 0.22
SELF_INITIATED_MAINTENANCE_WEIGHT = 0.20
SELF_INITIATED_CURIOSITY_WEIGHT = 0.15
SELF_INITIATED_HABIT_WEIGHT = 0.08
SELF_INITIATED_NOVELTY_WEIGHT = 0.05

# Block: Runtime thresholds
HIGH_PRIORITY_TASK_THRESHOLD = 0.80
SELF_INITIATED_ALLOWED_SOURCES = {
    "idle_tick",
    "post_action_followup",
    "self_initiated",
}

# Block: Fixed mappings
OBSERVATION_KIND_BY_INPUT_KIND = {
    "chat_message": "dialogue_turn",
    "camera_observation": "camera_scene",
    "network_result": "web_search",
}
ACTION_STYLE_BY_TYPE = {
    "speak": "conversational_response",
    "browse": "external_lookup",
    "notify": "push_notice",
    "look": "viewpoint_adjustment",
    "wait": "defer_action",
}
ACTION_TYPE_BY_OBSERVATION_KIND = {
    "camera_scene": "look",
    "web_search": "browse",
}
INITIATIVE_KIND_BY_ACTION_TYPE = {
    "browse": "unexplored_check",
    "look": "unexplored_check",
    "notify": "task_progress",
    "speak": "skill_rehearsal",
    "wait": "self_maintenance",
}
INITIATIVE_KINDS = (
    "task_progress",
    "unexplored_check",
    "self_maintenance",
    "skill_rehearsal",
)


# Block: Attention breakdown builder
def build_attention_score_breakdown(
    *,
    selection_profile: dict[str, Any],
    attention_snapshot: dict[str, Any],
    task_snapshot: dict[str, Any],
    memory_bundle: dict[str, Any],
    current_observation: dict[str, Any],
) -> dict[str, Any]:
    focus_ref = _focus_ref(
        attention_snapshot=attention_snapshot,
        current_observation=current_observation,
    )
    urgency_score = _attention_urgency_score(current_observation=current_observation)
    task_continuity_score = _attention_task_continuity_score(
        task_snapshot=task_snapshot,
        current_observation=current_observation,
    )
    relationship_salience_score = _attention_relationship_salience_score(
        selection_profile=selection_profile,
        current_observation=current_observation,
    )
    personality_fit_score = _attention_personality_fit_score(
        selection_profile=selection_profile,
        current_observation=current_observation,
    )
    experience_bias_score = _attention_experience_bias_score(
        selection_profile=selection_profile,
        memory_bundle=memory_bundle,
        current_observation=current_observation,
    )
    explicitness_score = _attention_explicitness_score(
        current_observation=current_observation,
    )
    novelty_score = _attention_novelty_score(
        memory_bundle=memory_bundle,
        current_observation=current_observation,
    )
    total_score = _normalized_score(
        urgency_score * ATTENTION_URGENCY_WEIGHT
        + task_continuity_score * ATTENTION_TASK_CONTINUITY_WEIGHT
        + relationship_salience_score * ATTENTION_RELATIONSHIP_SALIENCE_WEIGHT
        + personality_fit_score * ATTENTION_PERSONALITY_FIT_WEIGHT
        + experience_bias_score * ATTENTION_EXPERIENCE_BIAS_WEIGHT
        + explicitness_score * ATTENTION_EXPLICITNESS_WEIGHT
        + novelty_score * ATTENTION_NOVELTY_WEIGHT
    )
    return {
        "focus_ref": focus_ref,
        "hard_gate_passed": True,
        "urgency_score": urgency_score,
        "task_continuity_score": task_continuity_score,
        "relationship_salience_score": relationship_salience_score,
        "personality_fit_score": personality_fit_score,
        "experience_bias_score": experience_bias_score,
        "explicitness_score": explicitness_score,
        "novelty_score": novelty_score,
        "total_score": total_score,
    }


# Block: Self initiated breakdown builder
def build_self_initiated_score_breakdown(
    *,
    pending_input_source: str,
    selection_profile: dict[str, Any],
    task_snapshot: dict[str, Any],
) -> dict[str, Any]:
    scored_candidates = [
        _self_initiated_candidate(
            initiative_kind=initiative_kind,
            pending_input_source=pending_input_source,
            selection_profile=selection_profile,
            task_snapshot=task_snapshot,
        )
        for initiative_kind in INITIATIVE_KINDS
    ]
    return max(
        scored_candidates,
        key=lambda candidate: (
            bool(candidate["hard_gate_passed"]),
            float(candidate["total_score"]),
            str(candidate["initiative_kind"]),
        ),
    )


# Block: Skill candidate builder
def build_skill_candidates(
    *,
    selection_profile: dict[str, Any],
    self_initiated_score_breakdown: dict[str, Any],
    current_observation: dict[str, Any],
) -> list[dict[str, Any]]:
    preferred_action_types = _string_list(
        _required_object(
            selection_profile,
            "habit_biases",
            "selection_profile.habit_biases",
        ),
        "preferred_action_types",
        "selection_profile.habit_biases.preferred_action_types",
    )
    preferred_observation_kinds = _string_list(
        _required_object(
            selection_profile,
            "habit_biases",
            "selection_profile.habit_biases",
        ),
        "preferred_observation_kinds",
        "selection_profile.habit_biases.preferred_observation_kinds",
    )
    avoided_action_styles = set(
        _string_list(
            _required_object(
                selection_profile,
                "habit_biases",
                "selection_profile.habit_biases",
            ),
            "avoided_action_styles",
            "selection_profile.habit_biases.avoided_action_styles",
        )
    )
    candidate_action_types = list(preferred_action_types)
    for observation_kind in preferred_observation_kinds:
        action_type = ACTION_TYPE_BY_OBSERVATION_KIND.get(observation_kind)
        if action_type is None or action_type in candidate_action_types:
            continue
        candidate_action_types.append(action_type)
    for preference_entry in _required_list(
        selection_profile,
        "learned_preferences",
        "selection_profile.learned_preferences",
    ):
        if not isinstance(preference_entry, dict):
            raise RuntimeError("selection_profile.learned_preferences must contain only objects")
        if preference_entry.get("domain") != "action_type":
            continue
        action_type = preference_entry.get("target_key")
        if not isinstance(action_type, str) or not action_type:
            raise RuntimeError("selection_profile.learned_preferences.target_key must be non-empty string")
        if action_type in candidate_action_types:
            continue
        candidate_action_types.append(action_type)
    skill_candidates: list[dict[str, Any]] = []
    current_input_kind = str(current_observation["input_kind"])
    for rank, action_type in enumerate(candidate_action_types[:3]):
        action_style = ACTION_STYLE_BY_TYPE.get(action_type)
        if action_style is None or action_style in avoided_action_styles:
            continue
        initiative_kind = INITIATIVE_KIND_BY_ACTION_TYPE.get(action_type, "task_progress")
        fit_score = _skill_candidate_fit_score(
            selection_profile=selection_profile,
            action_type=action_type,
            initiative_kind=initiative_kind,
            rank=rank,
            self_initiated_score_breakdown=self_initiated_score_breakdown,
        )
        skill_candidates.append(
            {
                "skill_id": f"skill_{action_type}",
                "summary": f"{action_type} centered reusable pattern",
                "trigger_pattern": {
                    "initiative_kind": initiative_kind,
                    "input_kind": current_input_kind,
                },
                "preconditions": {
                    "preferred_action_type": action_type,
                    "avoided_action_styles": sorted(avoided_action_styles),
                },
                "action_pattern": {
                    "action_type": action_type,
                },
                "success_signature": {
                    "fit_score": fit_score,
                    "evidence_source": "personality_bias",
                },
            }
        )
    return skill_candidates


# Block: Attention helpers
def _focus_ref(
    *,
    attention_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
) -> str:
    primary_focus = attention_snapshot.get("primary_focus")
    if isinstance(primary_focus, dict):
        focus_ref = primary_focus.get("focus_ref")
        if isinstance(focus_ref, str) and focus_ref:
            return focus_ref
        focus_kind = primary_focus.get("kind")
        if isinstance(focus_kind, str) and focus_kind:
            return f"{focus_kind}:{current_observation['input_kind']}"
    return f"observation:{current_observation['input_kind']}"


def _attention_urgency_score(*, current_observation: dict[str, Any]) -> float:
    input_kind = str(current_observation["input_kind"])
    if input_kind == "chat_message":
        return 0.82
    if input_kind == "network_result":
        return 0.76
    if input_kind == "camera_observation":
        return 0.52
    raise RuntimeError("unsupported current_observation.input_kind")


def _attention_task_continuity_score(
    *,
    task_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
) -> float:
    active_tasks = _required_list(
        task_snapshot,
        "active_tasks",
        "cognition_input.task_snapshot.active_tasks",
    )
    waiting_external_tasks = _required_list(
        task_snapshot,
        "waiting_external_tasks",
        "cognition_input.task_snapshot.waiting_external_tasks",
    )
    source_task_id = current_observation.get("source_task_id")
    if isinstance(source_task_id, str) and source_task_id:
        if any(_task_id(task_entry) == source_task_id for task_entry in waiting_external_tasks):
            return 1.00
    if waiting_external_tasks:
        return 0.72
    if active_tasks:
        return 0.62
    return 0.38


def _attention_relationship_salience_score(
    *,
    selection_profile: dict[str, Any],
    current_observation: dict[str, Any],
) -> float:
    relationship_priorities = _required_list(
        selection_profile,
        "relationship_priorities",
        "selection_profile.relationship_priorities",
    )
    if not relationship_priorities:
        if current_observation["input_kind"] == "chat_message":
            return 0.40
        return 0.28
    strongest_priority = max(
        _normalized_score(relationship["priority_weight"])
        for relationship in relationship_priorities
        if isinstance(relationship, dict)
    )
    if current_observation["input_kind"] == "chat_message":
        return _normalized_score(0.55 + strongest_priority * 0.35)
    if current_observation["input_kind"] == "network_result":
        has_pending_relation = any(
            isinstance(relationship, dict)
            and relationship.get("reason_tag") == "pending_relation"
            for relationship in relationship_priorities
        )
        if has_pending_relation:
            return 0.62
        return _normalized_score(0.30 + strongest_priority * 0.20)
    return _normalized_score(0.25 + strongest_priority * 0.15)


def _attention_personality_fit_score(
    *,
    selection_profile: dict[str, Any],
    current_observation: dict[str, Any],
) -> float:
    trait_values = _required_object(
        selection_profile,
        "trait_values",
        "selection_profile.trait_values",
    )
    sociability = _trait_score(trait_values, "sociability")
    caution = _trait_score(trait_values, "caution")
    curiosity = _trait_score(trait_values, "curiosity")
    persistence = _trait_score(trait_values, "persistence")
    warmth = _trait_score(trait_values, "warmth")
    assertiveness = _trait_score(trait_values, "assertiveness")
    novelty_preference = _trait_score(trait_values, "novelty_preference")
    input_kind = str(current_observation["input_kind"])
    if input_kind == "chat_message":
        return _normalized_score(
            warmth * 0.35
            + sociability * 0.30
            + (1.0 - caution) * 0.20
            + assertiveness * 0.15
        )
    if input_kind == "network_result":
        return _normalized_score(
            curiosity * 0.40
            + persistence * 0.25
            + novelty_preference * 0.20
            + (1.0 - caution) * 0.15
        )
    if input_kind == "camera_observation":
        return _normalized_score(
            curiosity * 0.40
            + novelty_preference * 0.25
            + assertiveness * 0.20
            + (1.0 - caution) * 0.15
        )
    raise RuntimeError("unsupported current_observation.input_kind")


def _attention_experience_bias_score(
    *,
    selection_profile: dict[str, Any],
    memory_bundle: dict[str, Any],
    current_observation: dict[str, Any],
) -> float:
    observation_kind = OBSERVATION_KIND_BY_INPUT_KIND.get(str(current_observation["input_kind"]))
    if observation_kind is None:
        raise RuntimeError("unsupported current_observation.input_kind")
    learned_preferences = _required_list(
        selection_profile,
        "learned_preferences",
        "selection_profile.learned_preferences",
    )
    learned_aversions = _required_list(
        selection_profile,
        "learned_aversions",
        "selection_profile.learned_aversions",
    )
    habit_biases = _required_object(
        selection_profile,
        "habit_biases",
        "selection_profile.habit_biases",
    )
    preferred_observation_kinds = _string_list(
        habit_biases,
        "preferred_observation_kinds",
        "selection_profile.habit_biases.preferred_observation_kinds",
    )
    score = 0.50
    if observation_kind in preferred_observation_kinds:
        score += 0.18
    score += _matched_preference_weight(
        entries=learned_preferences,
        domain="observation_kind",
        target_key=observation_kind,
        field_name="selection_profile.learned_preferences",
    ) * 0.20
    score -= _matched_preference_weight(
        entries=learned_aversions,
        domain="observation_kind",
        target_key=observation_kind,
        field_name="selection_profile.learned_aversions",
    ) * 0.25
    related_item_count = (
        len(_required_list(memory_bundle, "working_memory_items", "cognition_input.memory_bundle.working_memory_items"))
        + len(_required_list(memory_bundle, "semantic_items", "cognition_input.memory_bundle.semantic_items"))
        + len(_required_list(memory_bundle, "recent_event_window", "cognition_input.memory_bundle.recent_event_window"))
    )
    if related_item_count > 0:
        score += min(0.10, related_item_count * 0.03)
    return _normalized_score(score)


def _attention_explicitness_score(*, current_observation: dict[str, Any]) -> float:
    input_kind = str(current_observation["input_kind"])
    if input_kind == "chat_message":
        if current_observation.get("text"):
            return 0.95
        return 0.60
    if input_kind == "network_result":
        return 0.78
    if input_kind == "camera_observation":
        return 0.34
    raise RuntimeError("unsupported current_observation.input_kind")


def _attention_novelty_score(
    *,
    memory_bundle: dict[str, Any],
    current_observation: dict[str, Any],
) -> float:
    related_item_count = (
        len(_required_list(memory_bundle, "working_memory_items", "cognition_input.memory_bundle.working_memory_items"))
        + len(_required_list(memory_bundle, "semantic_items", "cognition_input.memory_bundle.semantic_items"))
        + len(_required_list(memory_bundle, "recent_event_window", "cognition_input.memory_bundle.recent_event_window"))
    )
    base_score = {
        "chat_message": 0.40,
        "network_result": 0.60,
        "camera_observation": 0.72,
    }.get(str(current_observation["input_kind"]))
    if base_score is None:
        raise RuntimeError("unsupported current_observation.input_kind")
    return _normalized_score(base_score - min(0.30, related_item_count * 0.05))


# Block: Self initiated helpers
def _self_initiated_candidate(
    *,
    initiative_kind: str,
    pending_input_source: str,
    selection_profile: dict[str, Any],
    task_snapshot: dict[str, Any],
) -> dict[str, Any]:
    task_progress_fit = _self_initiated_task_progress_fit(
        initiative_kind=initiative_kind,
        selection_profile=selection_profile,
        task_snapshot=task_snapshot,
    )
    relationship_care_fit = _self_initiated_relationship_care_fit(
        initiative_kind=initiative_kind,
        selection_profile=selection_profile,
    )
    self_maintenance_need = _self_initiated_maintenance_fit(
        initiative_kind=initiative_kind,
        selection_profile=selection_profile,
    )
    curiosity_fit = _self_initiated_curiosity_fit(
        initiative_kind=initiative_kind,
        selection_profile=selection_profile,
    )
    habit_match = _self_initiated_habit_match(
        initiative_kind=initiative_kind,
        selection_profile=selection_profile,
    )
    novelty_fit = _self_initiated_novelty_fit(
        initiative_kind=initiative_kind,
        selection_profile=selection_profile,
    )
    total_score = _normalized_score(
        task_progress_fit * SELF_INITIATED_TASK_PROGRESS_WEIGHT
        + relationship_care_fit * SELF_INITIATED_RELATIONSHIP_CARE_WEIGHT
        + self_maintenance_need * SELF_INITIATED_MAINTENANCE_WEIGHT
        + curiosity_fit * SELF_INITIATED_CURIOSITY_WEIGHT
        + habit_match * SELF_INITIATED_HABIT_WEIGHT
        + novelty_fit * SELF_INITIATED_NOVELTY_WEIGHT
    )
    return {
        "initiative_kind": initiative_kind,
        "hard_gate_passed": _self_initiated_hard_gate(
            initiative_kind=initiative_kind,
            pending_input_source=pending_input_source,
            task_snapshot=task_snapshot,
        ),
        "task_progress_fit": task_progress_fit,
        "relationship_care_fit": relationship_care_fit,
        "self_maintenance_need": self_maintenance_need,
        "curiosity_fit": curiosity_fit,
        "habit_match": habit_match,
        "novelty_fit": novelty_fit,
        "total_score": total_score,
    }


def _self_initiated_hard_gate(
    *,
    initiative_kind: str,
    pending_input_source: str,
    task_snapshot: dict[str, Any],
) -> bool:
    if pending_input_source not in SELF_INITIATED_ALLOWED_SOURCES:
        return False
    waiting_external_tasks = _required_list(
        task_snapshot,
        "waiting_external_tasks",
        "cognition_input.task_snapshot.waiting_external_tasks",
    )
    if waiting_external_tasks and initiative_kind != "task_progress":
        return False
    active_tasks = _required_list(
        task_snapshot,
        "active_tasks",
        "cognition_input.task_snapshot.active_tasks",
    )
    has_high_priority_task = any(
        _task_priority_score(task_entry) >= HIGH_PRIORITY_TASK_THRESHOLD
        for task_entry in active_tasks
        if isinstance(task_entry, dict)
    )
    if has_high_priority_task and initiative_kind in {"unexplored_check", "skill_rehearsal"}:
        return False
    if initiative_kind == "task_progress" and not active_tasks:
        return False
    return True


def _self_initiated_task_progress_fit(
    *,
    initiative_kind: str,
    selection_profile: dict[str, Any],
    task_snapshot: dict[str, Any],
) -> float:
    drive_bias = _required_object(
        selection_profile,
        "drive_bias",
        "selection_profile.drive_bias",
    )
    task_progress_bias = _signed_to_score(
        _required_signed_number(
            drive_bias,
            "task_progress_bias",
            "selection_profile.drive_bias.task_progress_bias",
        )
    )
    active_tasks = _required_list(
        task_snapshot,
        "active_tasks",
        "cognition_input.task_snapshot.active_tasks",
    )
    active_task_bonus = min(0.20, len(active_tasks) * 0.08)
    if initiative_kind == "task_progress":
        return _normalized_score(0.40 + task_progress_bias * 0.40 + active_task_bonus)
    if initiative_kind == "skill_rehearsal":
        return _normalized_score(0.30 + task_progress_bias * 0.20 + active_task_bonus * 0.50)
    return _normalized_score(0.20 + task_progress_bias * 0.15)


def _self_initiated_relationship_care_fit(
    *,
    initiative_kind: str,
    selection_profile: dict[str, Any],
) -> float:
    relationship_priorities = _required_list(
        selection_profile,
        "relationship_priorities",
        "selection_profile.relationship_priorities",
    )
    strongest_priority = 0.0
    if relationship_priorities:
        strongest_priority = max(
            _normalized_score(relationship["priority_weight"])
            for relationship in relationship_priorities
            if isinstance(relationship, dict)
        )
    warmth = _trait_score(
        _required_object(selection_profile, "trait_values", "selection_profile.trait_values"),
        "warmth",
    )
    if initiative_kind == "task_progress":
        return _normalized_score(0.35 + strongest_priority * 0.40 + warmth * 0.25)
    if initiative_kind == "skill_rehearsal":
        return _normalized_score(0.25 + strongest_priority * 0.25 + warmth * 0.15)
    return _normalized_score(0.15 + strongest_priority * 0.15 + warmth * 0.10)


def _self_initiated_maintenance_fit(
    *,
    initiative_kind: str,
    selection_profile: dict[str, Any],
) -> float:
    drive_bias = _required_object(
        selection_profile,
        "drive_bias",
        "selection_profile.drive_bias",
    )
    maintenance_bias = _signed_to_score(
        _required_signed_number(
            drive_bias,
            "maintenance_bias",
            "selection_profile.drive_bias.maintenance_bias",
        )
    )
    caution = _trait_score(
        _required_object(selection_profile, "trait_values", "selection_profile.trait_values"),
        "caution",
    )
    if initiative_kind == "self_maintenance":
        return _normalized_score(0.35 + maintenance_bias * 0.40 + caution * 0.25)
    if initiative_kind == "task_progress":
        return _normalized_score(0.20 + maintenance_bias * 0.10)
    return _normalized_score(0.15 + maintenance_bias * 0.15 + caution * 0.10)


def _self_initiated_curiosity_fit(
    *,
    initiative_kind: str,
    selection_profile: dict[str, Any],
) -> float:
    drive_bias = _required_object(
        selection_profile,
        "drive_bias",
        "selection_profile.drive_bias",
    )
    exploration_bias = _signed_to_score(
        _required_signed_number(
            drive_bias,
            "exploration_bias",
            "selection_profile.drive_bias.exploration_bias",
        )
    )
    trait_values = _required_object(
        selection_profile,
        "trait_values",
        "selection_profile.trait_values",
    )
    curiosity = _trait_score(trait_values, "curiosity")
    novelty_preference = _trait_score(trait_values, "novelty_preference")
    if initiative_kind == "unexplored_check":
        return _normalized_score(
            0.25
            + curiosity * 0.35
            + novelty_preference * 0.20
            + exploration_bias * 0.20
        )
    if initiative_kind == "skill_rehearsal":
        return _normalized_score(0.20 + curiosity * 0.20 + exploration_bias * 0.15)
    return _normalized_score(0.15 + curiosity * 0.10 + exploration_bias * 0.10)


def _self_initiated_habit_match(
    *,
    initiative_kind: str,
    selection_profile: dict[str, Any],
) -> float:
    habit_biases = _required_object(
        selection_profile,
        "habit_biases",
        "selection_profile.habit_biases",
    )
    preferred_action_types = _string_list(
        habit_biases,
        "preferred_action_types",
        "selection_profile.habit_biases.preferred_action_types",
    )
    preferred_observation_kinds = _string_list(
        habit_biases,
        "preferred_observation_kinds",
        "selection_profile.habit_biases.preferred_observation_kinds",
    )
    learned_preferences = _required_list(
        selection_profile,
        "learned_preferences",
        "selection_profile.learned_preferences",
    )
    if initiative_kind == "task_progress":
        if "notify" in preferred_action_types or "speak" in preferred_action_types:
            return 0.78
        return _normalized_score(
            0.35
            + _matched_preference_weight(
                entries=learned_preferences,
                domain="action_type",
                target_key="notify",
                field_name="selection_profile.learned_preferences",
            ) * 0.20
        )
    if initiative_kind == "unexplored_check":
        if "browse" in preferred_action_types or "look" in preferred_action_types:
            return 0.82
        if "web_search" in preferred_observation_kinds or "camera_scene" in preferred_observation_kinds:
            return 0.74
        return 0.40
    if initiative_kind == "self_maintenance":
        avoided_action_styles = _string_list(
            habit_biases,
            "avoided_action_styles",
            "selection_profile.habit_biases.avoided_action_styles",
        )
        if avoided_action_styles:
            return 0.68
        return 0.38
    if initiative_kind == "skill_rehearsal":
        if preferred_action_types:
            return 0.72
        return _normalized_score(
            0.32
            + _matched_preference_weight(
                entries=learned_preferences,
                domain="action_type",
                target_key="speak",
                field_name="selection_profile.learned_preferences",
            ) * 0.18
        )
    raise RuntimeError("unsupported initiative_kind")


def _self_initiated_novelty_fit(
    *,
    initiative_kind: str,
    selection_profile: dict[str, Any],
) -> float:
    novelty_preference = _trait_score(
        _required_object(selection_profile, "trait_values", "selection_profile.trait_values"),
        "novelty_preference",
    )
    if initiative_kind == "unexplored_check":
        return _normalized_score(0.30 + novelty_preference * 0.70)
    if initiative_kind == "skill_rehearsal":
        return _normalized_score(0.20 + novelty_preference * 0.35)
    if initiative_kind == "task_progress":
        return _normalized_score(0.20 + novelty_preference * 0.15)
    if initiative_kind == "self_maintenance":
        return _normalized_score(0.15 + novelty_preference * 0.10)
    raise RuntimeError("unsupported initiative_kind")


# Block: Skill helpers
def _skill_candidate_fit_score(
    *,
    selection_profile: dict[str, Any],
    action_type: str,
    initiative_kind: str,
    rank: int,
    self_initiated_score_breakdown: dict[str, Any],
) -> float:
    learned_preferences = _required_list(
        selection_profile,
        "learned_preferences",
        "selection_profile.learned_preferences",
    )
    fit_score = 0.50
    fit_score += max(0.0, 0.12 - rank * 0.04)
    fit_score += _matched_preference_weight(
        entries=learned_preferences,
        domain="action_type",
        target_key=action_type,
        field_name="selection_profile.learned_preferences",
    ) * 0.20
    if self_initiated_score_breakdown["initiative_kind"] == initiative_kind:
        fit_score += _normalized_score(self_initiated_score_breakdown["total_score"]) * 0.18
    return _normalized_score(fit_score)


# Block: Shared helpers
def _required_object(container: dict[str, Any], key: str, field_name: str) -> dict[str, Any]:
    value = container.get(key)
    if not isinstance(value, dict):
        raise RuntimeError(f"{field_name} must be object")
    return value


def _required_list(container: dict[str, Any], key: str, field_name: str) -> list[Any]:
    value = container.get(key)
    if not isinstance(value, list):
        raise RuntimeError(f"{field_name} must be list")
    return value


def _string_list(container: dict[str, Any], key: str, field_name: str) -> list[str]:
    values = _required_list(container, key, field_name)
    string_values: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"{field_name} must contain only non-empty strings")
        string_values.append(value)
    return string_values


def _required_signed_number(container: dict[str, Any], key: str, field_name: str) -> float:
    value = container.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{field_name} must be numeric")
    numeric_value = float(value)
    if numeric_value < -1.0:
        return -1.0
    if numeric_value > 1.0:
        return 1.0
    return numeric_value


def _trait_score(trait_values: dict[str, Any], key: str) -> float:
    value = trait_values.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"selection_profile.trait_values.{key} must be numeric")
    return _signed_to_score(float(value))


def _signed_to_score(value: float) -> float:
    return _normalized_score((value + 1.0) / 2.0)


def _normalized_score(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError("score must be numeric")
    numeric_value = float(value)
    if numeric_value < 0.0:
        return 0.0
    if numeric_value > 1.0:
        return 1.0
    return numeric_value


def _matched_preference_weight(
    *,
    entries: list[dict[str, Any]],
    domain: str,
    target_key: str,
    field_name: str,
) -> float:
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError(f"{field_name} must contain only objects")
        if entry.get("domain") != domain or entry.get("target_key") != target_key:
            continue
        weight = entry.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise RuntimeError(f"{field_name}.weight must be numeric")
        return _normalized_score(weight)
    return 0.0


def _task_id(task_entry: dict[str, Any]) -> str:
    task_id = task_entry.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise RuntimeError("cognition_input.task_snapshot.task_id must be non-empty string")
    return task_id


def _task_priority_score(task_entry: dict[str, Any]) -> float:
    priority = task_entry.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, (int, float)):
        raise RuntimeError("cognition_input.task_snapshot.priority must be numeric")
    numeric_priority = float(priority)
    if numeric_priority < 0.0:
        return 0.0
    if numeric_priority <= 1.0:
        return numeric_priority
    if numeric_priority >= 100.0:
        return 1.0
    return numeric_priority / 100.0
