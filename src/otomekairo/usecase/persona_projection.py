"""Project persona state into attention and skill hints."""

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
SELF_INITIATED_SELF_MAINTENANCE_WEIGHT = 0.20
SELF_INITIATED_CURIOSITY_WEIGHT = 0.15
SELF_INITIATED_HABIT_MATCH_WEIGHT = 0.08
SELF_INITIATED_NOVELTY_WEIGHT = 0.05
MIN_SKILL_CANDIDATE_SCORE = 0.30
MAX_SKILL_CANDIDATES = 3


# Block: Public attention builder
def build_attention_snapshot(
    *,
    current_observation: dict[str, Any],
    selection_profile: dict[str, Any],
    task_snapshot: dict[str, Any],
    resolved_at: int,
) -> dict[str, Any]:
    candidates = _attention_candidates(
        current_observation=current_observation,
        selection_profile=selection_profile,
        task_snapshot=task_snapshot,
    )
    if not candidates:
        return {
            "primary_focus": {
                "focus_ref": "attention:idle",
                "focus_kind": "idle",
                "summary": "主注意なし",
                "score_hint": 0.0,
                "reason_codes": [],
            },
            "secondary_focuses": [],
            "suppressed_items": [],
            "revisit_queue": [],
            "updated_at": resolved_at,
        }
    scored_candidates = [
        _score_attention_candidate(
            candidate=candidate,
            current_observation=current_observation,
            selection_profile=selection_profile,
        )
        for candidate in candidates
    ]
    active_candidates = [
        candidate for candidate in scored_candidates if bool(candidate["hard_gate_passed"])
    ] or scored_candidates
    ranked_candidates = sorted(
        active_candidates,
        key=lambda candidate: candidate["total_score"],
        reverse=True,
    )
    primary_focus = ranked_candidates[0]
    revisit_queue: list[dict[str, Any]] = []
    for candidate in ranked_candidates[1:]:
        delta = round(primary_focus["total_score"] - candidate["total_score"], 4)
        if delta >= 0.05:
            continue
        revisit_queue.append(
            {
                **_attention_focus_payload(candidate),
                "delta_from_primary": delta,
            }
        )
        if len(revisit_queue) >= 2:
            break
    return {
        "primary_focus": _attention_focus_payload(primary_focus),
        "secondary_focuses": [
            _attention_focus_payload(candidate) for candidate in ranked_candidates[1:3]
        ],
        "suppressed_items": [
            {
                "focus_ref": candidate["focus_ref"],
                "focus_kind": candidate["focus_kind"],
                "summary": candidate["summary"],
                "reason_codes": ["hard_gate_filtered"],
            }
            for candidate in scored_candidates
            if not bool(candidate["hard_gate_passed"])
        ][:2],
        "revisit_queue": revisit_queue,
        "updated_at": resolved_at,
    }


# Block: Public skill builder
def build_skill_candidates(
    *,
    current_observation: dict[str, Any],
    selection_profile: dict[str, Any],
    behavior_settings: dict[str, Any],
    body_state: dict[str, Any],
    task_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    score_breakdowns = _self_initiated_score_breakdowns(
        current_observation=current_observation,
        selection_profile=selection_profile,
        body_state=body_state,
        task_snapshot=task_snapshot,
    )
    ranked_breakdowns = sorted(
        (
            breakdown
            for breakdown in score_breakdowns
            if bool(breakdown["hard_gate_passed"])
            and float(breakdown["total_score"]) >= MIN_SKILL_CANDIDATE_SCORE
        ),
        key=lambda breakdown: float(breakdown["total_score"]),
        reverse=True,
    )
    return [
        _skill_candidate_from_breakdown(
            breakdown=breakdown,
            behavior_settings=behavior_settings,
            task_snapshot=task_snapshot,
        )
        for breakdown in ranked_breakdowns[:MAX_SKILL_CANDIDATES]
    ]


# Block: Attention candidates
def _attention_candidates(
    *,
    current_observation: dict[str, Any],
    selection_profile: dict[str, Any],
    task_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = [
        _observation_attention_candidate(current_observation=current_observation),
    ]
    active_tasks = task_snapshot["active_tasks"]
    if active_tasks:
        candidates.append(_task_attention_candidate(task_entry=active_tasks[0]))
    relationship_priorities = selection_profile["relationship_priorities"]
    if relationship_priorities:
        candidates.append(
            _relationship_attention_candidate(
                relationship_entry=relationship_priorities[0],
            )
        )
    return candidates


def _observation_attention_candidate(current_observation: dict[str, Any]) -> dict[str, Any]:
    input_kind = str(current_observation["input_kind"])
    reason_codes = [f"input_kind:{input_kind}"]
    if input_kind == "chat_message":
        attachments = current_observation.get("attachments")
        if isinstance(attachments, list) and attachments:
            reason_codes.append("camera_attachment")
    if input_kind == "network_result":
        reason_codes.append("external_result")
    return {
        "candidate_type": "observation",
        "focus_ref": f"observation:{input_kind}",
        "focus_kind": "observation",
        "summary": str(current_observation["observation_text"]),
        "reason_codes": reason_codes,
        "observation_kind": input_kind,
    }


def _task_attention_candidate(task_entry: dict[str, Any]) -> dict[str, Any]:
    task_id = str(task_entry["task_id"])
    goal_hint = str(task_entry["goal_hint"])
    return {
        "candidate_type": "task",
        "focus_ref": f"task:{task_id}",
        "focus_kind": "task",
        "summary": goal_hint,
        "reason_codes": [f"task_kind:{task_entry['task_kind']}"],
        "task_entry": task_entry,
    }


def _relationship_attention_candidate(
    *,
    relationship_entry: dict[str, Any],
) -> dict[str, Any]:
    target_ref = str(relationship_entry["target_ref"])
    reason_tag = str(relationship_entry["reason_tag"])
    return {
        "candidate_type": "relationship",
        "focus_ref": f"relationship:{target_ref}",
        "focus_kind": "relationship",
        "summary": f"{target_ref} への注意",
        "reason_codes": [reason_tag],
        "relationship_entry": relationship_entry,
    }


# Block: Attention scoring
def _score_attention_candidate(
    *,
    candidate: dict[str, Any],
    current_observation: dict[str, Any],
    selection_profile: dict[str, Any],
) -> dict[str, Any]:
    candidate_type = str(candidate["candidate_type"])
    if candidate_type == "observation":
        urgency_score = _observation_attention_urgency(
            observation_kind=str(candidate["observation_kind"]),
        )
        task_continuity_score = _observation_attention_task_continuity(
            current_observation=current_observation,
        )
        relationship_salience_score = _observation_attention_relationship_salience(
            current_observation=current_observation,
            selection_profile=selection_profile,
        )
        personality_fit_score = _observation_attention_personality_fit(
            observation_kind=str(candidate["observation_kind"]),
            selection_profile=selection_profile,
        )
        experience_bias_score = _observation_attention_experience_bias(
            observation_kind=str(candidate["observation_kind"]),
            selection_profile=selection_profile,
        )
        explicitness_score = _observation_attention_explicitness(
            current_observation=current_observation,
        )
        novelty_score = _observation_attention_novelty(
            observation_kind=str(candidate["observation_kind"]),
            selection_profile=selection_profile,
        )
    elif candidate_type == "task":
        task_entry = candidate["task_entry"]
        urgency_score = _task_priority_score(task_entry)
        task_continuity_score = _task_attention_task_continuity(
            selection_profile=selection_profile,
        )
        relationship_salience_score = _task_attention_relationship_salience(
            selection_profile=selection_profile,
        )
        personality_fit_score = _task_attention_personality_fit(
            selection_profile=selection_profile,
        )
        experience_bias_score = _task_attention_experience_bias(
            task_entry=task_entry,
            selection_profile=selection_profile,
        )
        explicitness_score = 0.55
        novelty_score = _task_attention_novelty(selection_profile=selection_profile)
    else:
        relationship_entry = candidate["relationship_entry"]
        urgency_score = _relationship_attention_urgency(relationship_entry=relationship_entry)
        task_continuity_score = 0.35
        relationship_salience_score = _relationship_attention_salience(
            relationship_entry=relationship_entry,
        )
        personality_fit_score = _relationship_attention_personality_fit(
            selection_profile=selection_profile,
        )
        experience_bias_score = _relationship_attention_experience_bias(
            selection_profile=selection_profile,
        )
        explicitness_score = _relationship_attention_explicitness(
            relationship_entry=relationship_entry,
        )
        novelty_score = 0.12
    total_score = (
        ATTENTION_URGENCY_WEIGHT * urgency_score
        + ATTENTION_TASK_CONTINUITY_WEIGHT * task_continuity_score
        + ATTENTION_RELATIONSHIP_SALIENCE_WEIGHT * relationship_salience_score
        + ATTENTION_PERSONALITY_FIT_WEIGHT * personality_fit_score
        + ATTENTION_EXPERIENCE_BIAS_WEIGHT * experience_bias_score
        + ATTENTION_EXPLICITNESS_WEIGHT * explicitness_score
        + ATTENTION_NOVELTY_WEIGHT * novelty_score
    )
    return {
        **candidate,
        "hard_gate_passed": True,
        "urgency_score": _clamp_unit(urgency_score),
        "task_continuity_score": _clamp_unit(task_continuity_score),
        "relationship_salience_score": _clamp_unit(relationship_salience_score),
        "personality_fit_score": _clamp_unit(personality_fit_score),
        "experience_bias_score": _clamp_unit(experience_bias_score),
        "explicitness_score": _clamp_unit(explicitness_score),
        "novelty_score": _clamp_unit(novelty_score),
        "total_score": _clamp_unit(total_score),
    }


def _attention_focus_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "focus_ref": str(candidate["focus_ref"]),
        "focus_kind": str(candidate["focus_kind"]),
        "summary": str(candidate["summary"]),
        "score_hint": round(float(candidate["total_score"]), 4),
        "reason_codes": list(candidate["reason_codes"]),
    }


def _observation_attention_urgency(*, observation_kind: str) -> float:
    if observation_kind == "chat_message":
        return 0.96
    if observation_kind == "network_result":
        return 0.90
    if observation_kind == "camera_observation":
        return 0.62
    if observation_kind == "idle_tick":
        return 0.24
    raise ValueError("unsupported observation_kind for attention urgency")


def _observation_attention_task_continuity(
    *,
    current_observation: dict[str, Any],
) -> float:
    if current_observation["input_kind"] == "network_result":
        return 0.88
    if current_observation["input_kind"] == "chat_message":
        return 0.58
    return 0.42


def _observation_attention_relationship_salience(
    *,
    current_observation: dict[str, Any],
    selection_profile: dict[str, Any],
) -> float:
    strongest_priority = _strongest_relationship_priority(selection_profile)
    if current_observation["input_kind"] == "chat_message":
        return _clamp_unit(0.32 + strongest_priority * 0.48)
    if current_observation["input_kind"] == "network_result":
        return _clamp_unit(0.22 + strongest_priority * 0.25)
    return _clamp_unit(0.18 + strongest_priority * 0.18)


def _observation_attention_personality_fit(
    *,
    observation_kind: str,
    selection_profile: dict[str, Any],
) -> float:
    traits = selection_profile["trait_values"]
    curiosity = _trait_value(traits, "curiosity")
    caution = _trait_value(traits, "caution")
    warmth = _trait_value(traits, "warmth")
    sociability = _trait_value(traits, "sociability")
    novelty_preference = _trait_value(traits, "novelty_preference")
    if observation_kind == "chat_message":
        return _clamp_unit(0.42 * warmth + 0.38 * sociability + 0.20 * (1.0 - caution))
    if observation_kind == "network_result":
        return _clamp_unit(0.45 * curiosity + 0.35 * caution + 0.20 * novelty_preference)
    if observation_kind == "camera_observation":
        return _clamp_unit(0.55 * curiosity + 0.25 * novelty_preference + 0.20 * caution)
    if observation_kind == "idle_tick":
        return _clamp_unit(0.40 * caution + 0.35 * curiosity + 0.25 * novelty_preference)
    raise ValueError("unsupported observation_kind for attention personality fit")


def _observation_attention_experience_bias(
    *,
    observation_kind: str,
    selection_profile: dict[str, Any],
) -> float:
    return _experience_bias_from_preferences(
        action_type=None,
        observation_kind=observation_kind,
        selection_profile=selection_profile,
    )


def _observation_attention_explicitness(
    *,
    current_observation: dict[str, Any],
) -> float:
    if current_observation["input_kind"] == "network_result":
        return 0.92
    if current_observation["input_kind"] == "chat_message":
        text = current_observation.get("text")
        return 0.96 if isinstance(text, str) and text else 0.68
    return 0.54


def _observation_attention_novelty(
    *,
    observation_kind: str,
    selection_profile: dict[str, Any],
) -> float:
    novelty_preference = _trait_value(selection_profile["trait_values"], "novelty_preference")
    base_score = 0.12
    if observation_kind in {"camera_observation", "network_result"}:
        base_score += 0.14
    return _clamp_unit(base_score + novelty_preference * 0.26)


def _task_attention_task_continuity(*, selection_profile: dict[str, Any]) -> float:
    persistence = _trait_value(selection_profile["trait_values"], "persistence")
    task_progress_bias = _positive_drive_bias(selection_profile, "task_progress_bias")
    return _clamp_unit(0.62 + persistence * 0.23 + task_progress_bias * 0.15)


def _task_attention_relationship_salience(*, selection_profile: dict[str, Any]) -> float:
    return _clamp_unit(0.18 + _strongest_relationship_priority(selection_profile) * 0.20)


def _task_attention_personality_fit(*, selection_profile: dict[str, Any]) -> float:
    traits = selection_profile["trait_values"]
    persistence = _trait_value(traits, "persistence")
    caution = _trait_value(traits, "caution")
    return _clamp_unit(0.62 * persistence + 0.38 * caution)


def _task_attention_experience_bias(
    *,
    task_entry: dict[str, Any],
    selection_profile: dict[str, Any],
) -> float:
    action_type = "browse" if str(task_entry["task_kind"]) == "browse" else None
    return _experience_bias_from_preferences(
        action_type=action_type,
        observation_kind=None,
        selection_profile=selection_profile,
    )


def _task_attention_novelty(*, selection_profile: dict[str, Any]) -> float:
    novelty_preference = _trait_value(selection_profile["trait_values"], "novelty_preference")
    return _clamp_unit(0.08 + novelty_preference * 0.12)


def _relationship_attention_urgency(*, relationship_entry: dict[str, Any]) -> float:
    priority_weight = _normalized_number(
        relationship_entry["priority_weight"],
        field_name="selection_profile.relationship_priorities.priority_weight",
    )
    reason_tag = str(relationship_entry["reason_tag"])
    base_score = 0.42 + priority_weight * 0.38
    if reason_tag == "pending_relation":
        base_score += 0.15
    return _clamp_unit(base_score)


def _relationship_attention_salience(*, relationship_entry: dict[str, Any]) -> float:
    priority_weight = _normalized_number(
        relationship_entry["priority_weight"],
        field_name="selection_profile.relationship_priorities.priority_weight",
    )
    return _clamp_unit(0.60 + priority_weight * 0.40)


def _relationship_attention_personality_fit(
    *,
    selection_profile: dict[str, Any],
) -> float:
    traits = selection_profile["trait_values"]
    warmth = _trait_value(traits, "warmth")
    sociability = _trait_value(traits, "sociability")
    caution = _trait_value(traits, "caution")
    return _clamp_unit(0.46 * warmth + 0.34 * sociability + 0.20 * (1.0 - caution))


def _relationship_attention_experience_bias(
    *,
    selection_profile: dict[str, Any],
) -> float:
    speak_bias = _experience_bias_from_preferences(
        action_type="speak",
        observation_kind="chat_message",
        selection_profile=selection_profile,
    )
    notify_bias = _experience_bias_from_preferences(
        action_type="notify",
        observation_kind="chat_message",
        selection_profile=selection_profile,
    )
    return max(speak_bias, notify_bias)


def _relationship_attention_explicitness(
    *,
    relationship_entry: dict[str, Any],
) -> float:
    reason_tag = str(relationship_entry["reason_tag"])
    if reason_tag == "pending_relation":
        return 0.78
    if reason_tag == "recent_tension":
        return 0.62
    return 0.50


# Block: Self initiated scoring
def _self_initiated_score_breakdowns(
    *,
    current_observation: dict[str, Any],
    selection_profile: dict[str, Any],
    body_state: dict[str, Any],
    task_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _task_progress_breakdown(
            selection_profile=selection_profile,
            task_snapshot=task_snapshot,
        ),
        _unexplored_check_breakdown(
            current_observation=current_observation,
            selection_profile=selection_profile,
            body_state=body_state,
        ),
        _self_maintenance_breakdown(
            selection_profile=selection_profile,
            body_state=body_state,
        ),
        _skill_rehearsal_breakdown(selection_profile=selection_profile),
    ]


def _task_progress_breakdown(
    *,
    selection_profile: dict[str, Any],
    task_snapshot: dict[str, Any],
) -> dict[str, Any]:
    active_tasks = task_snapshot["active_tasks"]
    highest_task_priority = _task_priority_score(active_tasks[0]) if active_tasks else 0.0
    persistence = _trait_value(selection_profile["trait_values"], "persistence")
    task_progress_fit = _clamp_unit(
        0.50 * highest_task_priority
        + 0.32 * persistence
        + 0.18 * _positive_drive_bias(selection_profile, "task_progress_bias")
    )
    relationship_care_fit = _clamp_unit(0.18 + _strongest_relationship_priority(selection_profile) * 0.18)
    self_maintenance_need = 0.10
    curiosity_fit = _clamp_unit(0.12 + _trait_value(selection_profile["trait_values"], "curiosity") * 0.10)
    habit_match = _action_habit_match(
        selection_profile=selection_profile,
        action_types=["browse"],
        observation_kinds=[],
    )
    novelty_fit = _clamp_unit(0.08 + _trait_value(selection_profile["trait_values"], "novelty_preference") * 0.08)
    return _self_initiated_breakdown_payload(
        initiative_kind="task_progress",
        hard_gate_passed=bool(active_tasks),
        task_progress_fit=task_progress_fit,
        relationship_care_fit=relationship_care_fit,
        self_maintenance_need=self_maintenance_need,
        curiosity_fit=curiosity_fit,
        habit_match=habit_match,
        novelty_fit=novelty_fit,
    )


def _unexplored_check_breakdown(
    *,
    current_observation: dict[str, Any],
    selection_profile: dict[str, Any],
    body_state: dict[str, Any],
) -> dict[str, Any]:
    curiosity = _trait_value(selection_profile["trait_values"], "curiosity")
    novelty_preference = _trait_value(selection_profile["trait_values"], "novelty_preference")
    task_progress_fit = 0.42
    relationship_care_fit = _clamp_unit(0.20 + _strongest_relationship_priority(selection_profile) * 0.12)
    self_maintenance_need = 0.12
    curiosity_fit = _clamp_unit(
        0.48 * curiosity
        + 0.22 * novelty_preference
        + 0.30 * _positive_drive_bias(selection_profile, "exploration_bias")
    )
    observation_kind = str(current_observation["input_kind"])
    habit_match = _action_habit_match(
        selection_profile=selection_profile,
        action_types=["look", "browse"],
        observation_kinds=[observation_kind],
    )
    novelty_fit = _clamp_unit(0.18 + novelty_preference * 0.42)
    sensor_availability = body_state.get("sensor_availability")
    camera_blocked = isinstance(sensor_availability, dict) and sensor_availability.get("camera") is False
    return _self_initiated_breakdown_payload(
        initiative_kind="unexplored_check",
        hard_gate_passed=not camera_blocked,
        task_progress_fit=task_progress_fit,
        relationship_care_fit=relationship_care_fit,
        self_maintenance_need=self_maintenance_need,
        curiosity_fit=curiosity_fit,
        habit_match=habit_match,
        novelty_fit=novelty_fit,
    )


def _self_maintenance_breakdown(
    *,
    selection_profile: dict[str, Any],
    body_state: dict[str, Any],
) -> dict[str, Any]:
    caution = _trait_value(selection_profile["trait_values"], "caution")
    body_load = _dict_signal_max(body_state.get("load"))
    task_progress_fit = 0.24
    relationship_care_fit = 0.18
    self_maintenance_need = _clamp_unit(
        0.42 * caution
        + 0.30 * _positive_drive_bias(selection_profile, "maintenance_bias")
        + 0.28 * body_load
    )
    curiosity_fit = _clamp_unit(0.08 + (1.0 - caution) * 0.10)
    habit_match = _action_habit_match(
        selection_profile=selection_profile,
        action_types=["wait"],
        observation_kinds=[],
    )
    novelty_fit = 0.06
    return _self_initiated_breakdown_payload(
        initiative_kind="self_maintenance",
        hard_gate_passed=True,
        task_progress_fit=task_progress_fit,
        relationship_care_fit=relationship_care_fit,
        self_maintenance_need=self_maintenance_need,
        curiosity_fit=curiosity_fit,
        habit_match=habit_match,
        novelty_fit=novelty_fit,
    )


def _skill_rehearsal_breakdown(
    *,
    selection_profile: dict[str, Any],
) -> dict[str, Any]:
    preferred_actions = _required_list_of_strings(
        selection_profile["habit_biases"],
        "preferred_action_types",
        "selection_profile.habit_biases.preferred_action_types",
    )
    has_preferences = bool(preferred_actions) or bool(selection_profile["learned_preferences"])
    persistence = _trait_value(selection_profile["trait_values"], "persistence")
    curiosity = _trait_value(selection_profile["trait_values"], "curiosity")
    task_progress_fit = _clamp_unit(0.28 + persistence * 0.20)
    relationship_care_fit = _clamp_unit(0.18 + _strongest_relationship_priority(selection_profile) * 0.14)
    self_maintenance_need = 0.12
    curiosity_fit = _clamp_unit(0.24 + curiosity * 0.24)
    habit_match = _action_habit_match(
        selection_profile=selection_profile,
        action_types=preferred_actions[:2],
        observation_kinds=[],
    )
    novelty_fit = _clamp_unit(0.12 + _trait_value(selection_profile["trait_values"], "novelty_preference") * 0.18)
    return _self_initiated_breakdown_payload(
        initiative_kind="skill_rehearsal",
        hard_gate_passed=has_preferences,
        task_progress_fit=task_progress_fit,
        relationship_care_fit=relationship_care_fit,
        self_maintenance_need=self_maintenance_need,
        curiosity_fit=curiosity_fit,
        habit_match=habit_match,
        novelty_fit=novelty_fit,
    )


def _self_initiated_breakdown_payload(
    *,
    initiative_kind: str,
    hard_gate_passed: bool,
    task_progress_fit: float,
    relationship_care_fit: float,
    self_maintenance_need: float,
    curiosity_fit: float,
    habit_match: float,
    novelty_fit: float,
) -> dict[str, Any]:
    total_score = (
        SELF_INITIATED_TASK_PROGRESS_WEIGHT * task_progress_fit
        + SELF_INITIATED_RELATIONSHIP_CARE_WEIGHT * relationship_care_fit
        + SELF_INITIATED_SELF_MAINTENANCE_WEIGHT * self_maintenance_need
        + SELF_INITIATED_CURIOSITY_WEIGHT * curiosity_fit
        + SELF_INITIATED_HABIT_MATCH_WEIGHT * habit_match
        + SELF_INITIATED_NOVELTY_WEIGHT * novelty_fit
    )
    return {
        "initiative_kind": initiative_kind,
        "hard_gate_passed": hard_gate_passed,
        "task_progress_fit": _clamp_unit(task_progress_fit),
        "relationship_care_fit": _clamp_unit(relationship_care_fit),
        "self_maintenance_need": _clamp_unit(self_maintenance_need),
        "curiosity_fit": _clamp_unit(curiosity_fit),
        "habit_match": _clamp_unit(habit_match),
        "novelty_fit": _clamp_unit(novelty_fit),
        "total_score": _clamp_unit(total_score),
    }


def _skill_candidate_from_breakdown(
    *,
    breakdown: dict[str, Any],
    behavior_settings: dict[str, Any],
    task_snapshot: dict[str, Any],
) -> dict[str, Any]:
    initiative_kind = str(breakdown["initiative_kind"])
    if initiative_kind == "task_progress":
        skill_id = "resume_active_task"
        suggested_action_types = _task_progress_action_types(task_snapshot)
        reason_codes = ["active_task_present", "task_progress_fit"]
    elif initiative_kind == "unexplored_check":
        skill_id = "inspect_unresolved_observation"
        suggested_action_types = _unexplored_action_types(behavior_settings)
        reason_codes = ["curiosity_fit", "novelty_fit"]
    elif initiative_kind == "self_maintenance":
        skill_id = "stabilize_and_wait"
        suggested_action_types = ["wait"]
        reason_codes = ["self_maintenance_need", "caution_bias"]
    else:
        skill_id = "repeat_preferred_pattern"
        suggested_action_types = _skill_rehearsal_action_types(behavior_settings)
        reason_codes = ["habit_match", "preferred_pattern"]
    return {
        "skill_id": skill_id,
        "initiative_kind": initiative_kind,
        "fit_score": round(float(breakdown["total_score"]), 4),
        "suggested_action_types": suggested_action_types,
        "reason_codes": reason_codes,
    }


def _task_progress_action_types(task_snapshot: dict[str, Any]) -> list[str]:
    active_tasks = task_snapshot["active_tasks"]
    if not active_tasks:
        return ["wait"]
    if str(active_tasks[0]["task_kind"]) == "browse":
        return ["browse"]
    return ["wait"]


def _unexplored_action_types(behavior_settings: dict[str, Any]) -> list[str]:
    browse_preference = str(behavior_settings["browse_preference"])
    if browse_preference == "prefer":
        return ["browse", "look"]
    return ["look"]


def _skill_rehearsal_action_types(behavior_settings: dict[str, Any]) -> list[str]:
    notify_preference = str(behavior_settings["notify_preference"])
    if notify_preference == "prefer":
        return ["notify", "speak"]
    return ["speak", "look"]


# Block: Numeric helpers
def _normalized_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    numeric_value = float(value)
    if numeric_value < 0.0:
        return 0.0
    if numeric_value > 1.0:
        return 1.0
    return numeric_value


def _normalized_signed_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    numeric_value = float(value)
    if numeric_value < -1.0:
        return -1.0
    if numeric_value > 1.0:
        return 1.0
    return numeric_value


def _clamp_unit(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _trait_value(trait_values: dict[str, Any], key: str) -> float:
    return _normalized_number(
        trait_values[key],
        field_name=f"selection_profile.trait_values.{key}",
    )


def _positive_drive_bias(selection_profile: dict[str, Any], key: str) -> float:
    drive_bias = selection_profile["drive_bias"]
    return _clamp_unit(
        max(
            0.0,
            _normalized_signed_number(
                drive_bias[key],
                field_name=f"selection_profile.drive_bias.{key}",
            ),
        )
    )


def _strongest_relationship_priority(selection_profile: dict[str, Any]) -> float:
    relationship_priorities = selection_profile["relationship_priorities"]
    if not relationship_priorities:
        return 0.0
    return max(
        _normalized_number(
            relationship["priority_weight"],
            field_name="selection_profile.relationship_priorities.priority_weight",
        )
        for relationship in relationship_priorities
    )


def _experience_bias_from_preferences(
    *,
    action_type: str | None,
    observation_kind: str | None,
    selection_profile: dict[str, Any],
) -> float:
    habit_biases = selection_profile["habit_biases"]
    preferred_action_types = _required_list_of_strings(
        habit_biases,
        "preferred_action_types",
        "selection_profile.habit_biases.preferred_action_types",
    )
    preferred_observation_kinds = _required_list_of_strings(
        habit_biases,
        "preferred_observation_kinds",
        "selection_profile.habit_biases.preferred_observation_kinds",
    )
    score = 0.50
    if action_type is not None and action_type in preferred_action_types:
        score += 0.16
    if observation_kind is not None and observation_kind in preferred_observation_kinds:
        score += 0.14
    if action_type is not None:
        score += _matched_preference_weight(
            entries=selection_profile["learned_preferences"],
            domain="action_type",
            target_key=action_type,
            field_name="selection_profile.learned_preferences",
        ) * 0.20
        score -= _matched_preference_weight(
            entries=selection_profile["learned_aversions"],
            domain="action_type",
            target_key=action_type,
            field_name="selection_profile.learned_aversions",
        ) * 0.32
    if observation_kind is not None:
        score += _matched_preference_weight(
            entries=selection_profile["learned_preferences"],
            domain="observation_kind",
            target_key=observation_kind,
            field_name="selection_profile.learned_preferences",
        ) * 0.16
        score -= _matched_preference_weight(
            entries=selection_profile["learned_aversions"],
            domain="observation_kind",
            target_key=observation_kind,
            field_name="selection_profile.learned_aversions",
        ) * 0.28
    return _clamp_unit(score)


def _action_habit_match(
    *,
    selection_profile: dict[str, Any],
    action_types: list[str],
    observation_kinds: list[str],
) -> float:
    if not action_types and not observation_kinds:
        return 0.0
    best_score = 0.0
    for action_type in action_types:
        best_score = max(
            best_score,
            _experience_bias_from_preferences(
                action_type=action_type,
                observation_kind=None,
                selection_profile=selection_profile,
            ),
        )
    for observation_kind in observation_kinds:
        best_score = max(
            best_score,
            _experience_bias_from_preferences(
                action_type=None,
                observation_kind=observation_kind,
                selection_profile=selection_profile,
            ),
        )
    return best_score


def _matched_preference_weight(
    *,
    entries: Any,
    domain: str,
    target_key: str,
    field_name: str,
) -> float:
    if not isinstance(entries, list):
        raise ValueError(f"{field_name} must be list")
    best_weight = 0.0
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"{field_name}[{index}] must be object")
        if entry.get("domain") != domain:
            continue
        if entry.get("target_key") != target_key:
            continue
        best_weight = max(
            best_weight,
            _normalized_number(
                entry["weight"],
                field_name=f"{field_name}[{index}].weight",
            ),
        )
    return best_weight


def _required_list_of_strings(
    payload: dict[str, Any],
    key: str,
    field_name: str,
) -> list[str]:
    raw_value = payload.get(key)
    if not isinstance(raw_value, list):
        raise ValueError(f"{field_name} must be list")
    normalized_values: list[str] = []
    for index, entry in enumerate(raw_value):
        if not isinstance(entry, str):
            raise ValueError(f"{field_name}[{index}] must be string")
        normalized_values.append(entry)
    return normalized_values


def _task_priority_score(task_entry: dict[str, Any]) -> float:
    priority_value = task_entry["priority"]
    if isinstance(priority_value, bool) or not isinstance(priority_value, (int, float)):
        raise ValueError("task_snapshot.active_tasks.priority must be numeric")
    numeric_value = float(priority_value)
    if numeric_value > 1.0:
        numeric_value /= 100.0
    return _clamp_unit(numeric_value)


def _dict_signal_max(payload: Any) -> float:
    if not isinstance(payload, dict):
        return 0.0
    strongest_signal = 0.0
    for value in payload.values():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            strongest_signal = max(strongest_signal, _clamp_unit(float(value)))
    return strongest_signal
