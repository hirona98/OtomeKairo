"""Validate and select action proposals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from otomekairo.usecase.dispatch_action_command import opaque_action_id


# Block: Score weights
TASK_FIT_WEIGHT = 0.24
PERSONALITY_FIT_WEIGHT = 0.24
RELATIONSHIP_FIT_WEIGHT = 0.18
EXPERIENCE_FIT_WEIGHT = 0.16
DRIVE_RELIEF_WEIGHT = 0.10
EXPECTED_STABILITY_WEIGHT = 0.08
PERSONALITY_FIT_TRAIT_WEIGHT = 0.50
PERSONALITY_FIT_STYLE_WEIGHT = 0.50
NON_URGENT_PRIORITY_THRESHOLD = 0.85
AVERSION_HOLD_THRESHOLD = 0.70
AVERSION_REJECT_THRESHOLD = 0.85
RELATIONSHIP_HOLD_THRESHOLD = 0.40
RELATIONSHIP_REJECT_THRESHOLD = 0.35


# Block: Validation result
@dataclass(frozen=True, slots=True)
class ValidatedChatAction:
    decision: str
    decision_reason: str
    proposal: dict[str, Any] | None
    action_command: dict[str, Any] | None
    action_candidate_score: dict[str, Any]


# Block: Public validator
def validate_chat_response_action(
    *,
    pending_channel: str,
    message_id: str,
    cognition_input: dict[str, Any],
    cognition_result: dict[str, Any],
    response_text: str,
) -> ValidatedChatAction:
    proposals = _validated_action_proposals(cognition_result)
    if not proposals:
        return ValidatedChatAction(
            decision="reject",
            decision_reason="no_action_proposals",
            proposal=None,
            action_command=None,
            action_candidate_score=_empty_candidate_score(),
        )
    scored_candidates = [
        _score_candidate(
            proposal=proposal,
            message_id=message_id,
            pending_channel=pending_channel,
            cognition_input=cognition_input,
            response_text=response_text,
        )
        for proposal in proposals
    ]
    executable_candidates = [
        candidate for candidate in scored_candidates if bool(candidate["hard_gate_passed"])
    ]
    if not executable_candidates:
        best_candidate = max(
            scored_candidates,
            key=lambda candidate: (
                candidate["total_score"],
                candidate["priority_hint_score"],
            ),
        )
        return ValidatedChatAction(
            decision="reject",
            decision_reason="all_candidates_rejected_by_hard_gate",
            proposal=None,
            action_command=None,
            action_candidate_score=_candidate_payload(best_candidate),
        )
    best_candidate = max(
        executable_candidates,
        key=lambda candidate: (
            candidate["total_score"],
            candidate["priority_hint_score"],
        ),
    )
    action_type = str(best_candidate["proposal"]["action_type"])
    selected_proposal = _materialize_selected_proposal(
        proposal=best_candidate["proposal"],
        message_id=message_id,
    )
    aversion_conflict_decision = _aversion_conflict_decision(
        candidate=best_candidate,
    )
    if aversion_conflict_decision is not None:
        decision, decision_reason = aversion_conflict_decision
        return ValidatedChatAction(
            decision=decision,
            decision_reason=decision_reason,
            proposal=selected_proposal,
            action_command=None,
            action_candidate_score=_candidate_payload(best_candidate),
        )
    if float(best_candidate["personality_fit_score"]) < 0.30:
        return ValidatedChatAction(
            decision="hold",
            decision_reason="personality_fit_below_threshold",
            proposal=selected_proposal,
            action_command=None,
            action_candidate_score=_candidate_payload(best_candidate),
        )
    if action_type == "wait":
        return ValidatedChatAction(
            decision="hold",
            decision_reason="wait_selected",
            proposal=selected_proposal,
            action_command=None,
            action_candidate_score=_candidate_payload(best_candidate),
        )
    action_command = _build_action_command(
        action_type=action_type,
        pending_channel=pending_channel,
        proposal=selected_proposal,
        response_text=response_text,
    )
    return ValidatedChatAction(
        decision="execute",
        decision_reason=f"{action_type}_selected",
        proposal=selected_proposal,
        action_command=action_command,
        action_candidate_score=_candidate_payload(best_candidate),
    )


# Block: Proposal validation
def _validated_action_proposals(cognition_result: dict[str, Any]) -> list[dict[str, Any]]:
    action_proposals = cognition_result.get("action_proposals")
    if not isinstance(action_proposals, list):
        raise RuntimeError("cognition_result.action_proposals must be a list")
    validated_proposals: list[dict[str, Any]] = []
    for proposal in action_proposals:
        if not isinstance(proposal, dict):
            raise RuntimeError("cognition_result.action_proposals must contain only objects")
        action_type = _validated_action_type(proposal)
        if action_type == "browse":
            _browse_query_text(proposal)
        if action_type == "look":
            _validated_look_target(proposal)
        validated_proposals.append(proposal)
    return validated_proposals


# Block: Candidate scoring
def _score_candidate(
    *,
    proposal: dict[str, Any],
    message_id: str,
    pending_channel: str,
    cognition_input: dict[str, Any],
    response_text: str,
) -> dict[str, Any]:
    action_type = _validated_action_type(proposal)
    selection_profile = cognition_input["selection_profile"]
    task_snapshot = cognition_input["task_snapshot"]
    memory_bundle = cognition_input["memory_bundle"]
    current_observation = cognition_input["current_observation"]
    learned_aversions = selection_profile["learned_aversions"]
    habit_biases = selection_profile["habit_biases"]
    hard_gate_passed = _passes_hard_gate(
        proposal=proposal,
        pending_channel=pending_channel,
        cognition_input=cognition_input,
        learned_aversions=learned_aversions,
        habit_biases=habit_biases,
    )
    priority_hint_score = _proposal_priority_score(proposal)
    persona_consistency = _persona_consistency_score(
        action_type=action_type,
        proposal=proposal,
        selection_profile=selection_profile,
        memory_bundle=memory_bundle,
        current_observation=current_observation,
        response_text=response_text,
    )
    personality_fit_score = _personality_fit_score(persona_consistency=persona_consistency)
    relationship_fit_score = persona_consistency["relationship_alignment"]
    experience_fit_score = _experience_fit_score(
        persona_consistency=persona_consistency,
    )
    drive_relief_score = _drive_relief_score(
        action_type=action_type,
        drive_bias=selection_profile["drive_bias"],
    )
    expected_stability_score = _expected_stability_score(
        action_type=action_type,
        task_snapshot=task_snapshot,
    )
    task_fit_score = _task_fit_score(
        action_type=action_type,
        proposal=proposal,
        task_snapshot=task_snapshot,
        current_observation=current_observation,
    )
    total_score = (
        TASK_FIT_WEIGHT * task_fit_score
        + PERSONALITY_FIT_WEIGHT * personality_fit_score
        + RELATIONSHIP_FIT_WEIGHT * relationship_fit_score
        + EXPERIENCE_FIT_WEIGHT * experience_fit_score
        + DRIVE_RELIEF_WEIGHT * drive_relief_score
        + EXPECTED_STABILITY_WEIGHT * expected_stability_score
    )
    return {
        "proposal": proposal,
        "proposal_id": _proposal_id(proposal, message_id),
        "hard_gate_passed": hard_gate_passed,
        "task_fit_score": task_fit_score,
        "personality_fit_score": personality_fit_score,
        "relationship_fit_score": relationship_fit_score,
        "experience_fit_score": experience_fit_score,
        "drive_relief_score": drive_relief_score,
        "expected_stability_score": expected_stability_score,
        "priority_hint_score": priority_hint_score,
        "total_score": _normalized_score(total_score),
        "persona_consistency": persona_consistency,
    }


# Block: Hard gate check
def _passes_hard_gate(
    *,
    proposal: dict[str, Any],
    pending_channel: str,
    cognition_input: dict[str, Any],
    learned_aversions: list[dict[str, Any]],
    habit_biases: dict[str, Any],
) -> bool:
    action_type = _validated_action_type(proposal)
    invariants = cognition_input["self_snapshot"]["invariants"]
    forbidden_action_types = _required_list(
        invariants,
        "forbidden_action_types",
        "self_snapshot.invariants.forbidden_action_types",
    )
    if action_type in forbidden_action_types:
        return False
    forbidden_action_styles = _required_list(
        invariants,
        "forbidden_action_styles",
        "self_snapshot.invariants.forbidden_action_styles",
    )
    if _proposal_action_style(action_type) in forbidden_action_styles:
        return False
    target_channel = proposal.get("target_channel")
    if action_type in {"speak", "notify"} and target_channel != pending_channel:
        return False
    if action_type == "browse" and _has_waiting_browse_for_same_query(
        proposal=proposal,
        task_snapshot=cognition_input["task_snapshot"],
    ):
        return False
    if action_type == "look":
        policy_snapshot = _required_object(
            cognition_input,
            "policy_snapshot",
            "cognition_input.policy_snapshot",
        )
        runtime_policy = _required_object(
            policy_snapshot,
            "runtime_policy",
            "cognition_input.policy_snapshot.runtime_policy",
        )
        if not bool(runtime_policy.get("camera_enabled")):
            return False
        if not bool(runtime_policy.get("camera_available")):
            return False
    return not _has_strong_aversion(
        action_type=action_type,
        learned_aversions=learned_aversions,
        habit_biases=habit_biases,
    )


# Block: Persona consistency scoring
def _persona_consistency_score(
    *,
    action_type: str,
    proposal: dict[str, Any],
    selection_profile: dict[str, Any],
    memory_bundle: dict[str, Any],
    current_observation: dict[str, Any],
    response_text: str,
) -> dict[str, Any]:
    trait_alignment = _trait_alignment(
        action_type=action_type,
        trait_values=_required_object(
            selection_profile,
            "trait_values",
            "selection_profile.trait_values",
        ),
    )
    style_alignment = _style_alignment(
        action_type=action_type,
        interaction_style=_required_object(
            selection_profile,
            "interaction_style",
            "selection_profile.interaction_style",
        ),
        response_text=response_text,
    )
    relationship_alignment = _relationship_alignment(
        action_type=action_type,
        relationship_priorities=_required_list(
            selection_profile,
            "relationship_priorities",
            "selection_profile.relationship_priorities",
        ),
    )
    preference_alignment = _preference_alignment(
        action_type=action_type,
        proposal=proposal,
        learned_preferences=_required_list(
            selection_profile,
            "learned_preferences",
            "selection_profile.learned_preferences",
        ),
        habit_biases=_required_object(
            selection_profile,
            "habit_biases",
            "selection_profile.habit_biases",
        ),
        memory_bundle=memory_bundle,
        current_observation=current_observation,
    )
    aversion_penalty = _aversion_penalty(
        action_type=action_type,
        learned_aversions=_required_list(
            selection_profile,
            "learned_aversions",
            "selection_profile.learned_aversions",
        ),
        habit_biases=_required_object(
            selection_profile,
            "habit_biases",
            "selection_profile.habit_biases",
        ),
    )
    emotion_alignment = _emotion_alignment(
        action_type=action_type,
        emotion_bias=_required_object(
            selection_profile,
            "emotion_bias",
            "selection_profile.emotion_bias",
        ),
    )
    drive_alignment = _drive_alignment(
        action_type=action_type,
        drive_bias=_required_object(
            selection_profile,
            "drive_bias",
            "selection_profile.drive_bias",
        ),
    )
    positive_average = (
        trait_alignment
        + style_alignment
        + relationship_alignment
        + preference_alignment
        + emotion_alignment
        + drive_alignment
    ) / 6.0
    overall_score = _normalized_score(positive_average - aversion_penalty * 0.50)
    return {
        "trait_alignment": trait_alignment,
        "style_alignment": style_alignment,
        "relationship_alignment": relationship_alignment,
        "preference_alignment": preference_alignment,
        "aversion_penalty": aversion_penalty,
        "emotion_alignment": emotion_alignment,
        "drive_alignment": drive_alignment,
        "overall_score": overall_score,
    }


# Block: Personality fit scoring
def _personality_fit_score(
    *,
    persona_consistency: dict[str, Any],
) -> float:
    trait_alignment = _normalized_score(persona_consistency["trait_alignment"])
    style_alignment = _normalized_score(persona_consistency["style_alignment"])
    return _normalized_score(
        trait_alignment * PERSONALITY_FIT_TRAIT_WEIGHT
        + style_alignment * PERSONALITY_FIT_STYLE_WEIGHT
    )


# Block: Style alignment
def _style_alignment(
    *,
    action_type: str,
    interaction_style: dict[str, Any],
    response_text: str,
) -> float:
    if action_type == "speak":
        return _speech_style_alignment(
            interaction_style=interaction_style,
            response_text=response_text,
        )
    if action_type == "browse":
        confirmation_style = interaction_style["confirmation_style"]
        if confirmation_style == "careful":
            return 0.90
        if confirmation_style == "balanced":
            return 0.75
        if confirmation_style == "light":
            return 0.55
        raise RuntimeError("selection_profile.interaction_style.confirmation_style is invalid")
    if action_type == "notify":
        speech_tone = interaction_style["speech_tone"]
        if speech_tone in {"warm", "gentle"}:
            return 0.85
        if speech_tone in {"neutral", "calm"}:
            return 0.70
        if speech_tone in {"direct", "firm"}:
            return 0.60
        raise RuntimeError("selection_profile.interaction_style.speech_tone is invalid")
    if action_type == "look":
        confirmation_style = interaction_style["confirmation_style"]
        if confirmation_style == "careful":
            return 0.85
        if confirmation_style == "balanced":
            return 0.75
        if confirmation_style == "light":
            return 0.60
        raise RuntimeError("selection_profile.interaction_style.confirmation_style is invalid")
    if action_type == "wait":
        confirmation_style = interaction_style["confirmation_style"]
        if confirmation_style == "careful":
            return 0.90
        if confirmation_style == "balanced":
            return 0.70
        if confirmation_style == "light":
            return 0.45
        raise RuntimeError("selection_profile.interaction_style.confirmation_style is invalid")
    raise RuntimeError("unsupported action_type for style alignment")


def _speech_style_alignment(
    *,
    interaction_style: dict[str, Any],
    response_text: str,
) -> float:
    response_pace = interaction_style["response_pace"]
    text_length = len(response_text)
    if response_pace == "quick":
        if text_length <= 40:
            return 1.00
        if text_length <= 80:
            return 0.75
        return 0.45
    if response_pace == "balanced":
        if 20 <= text_length <= 80:
            return 0.90
        return 0.65
    if response_pace == "careful":
        if text_length >= 40:
            return 0.90
        return 0.60
    raise RuntimeError("selection_profile.interaction_style.response_pace is invalid")


# Block: Trait alignment
def _trait_alignment(
    *,
    action_type: str,
    trait_values: dict[str, Any],
) -> float:
    sociability = _trait_value(trait_values, "sociability")
    caution = _trait_value(trait_values, "caution")
    curiosity = _trait_value(trait_values, "curiosity")
    warmth = _trait_value(trait_values, "warmth")
    assertiveness = _trait_value(trait_values, "assertiveness")
    novelty_preference = _trait_value(trait_values, "novelty_preference")
    if action_type == "speak":
        return _normalized_score(
            0.40 * sociability
            + 0.35 * warmth
            + 0.25 * (1.0 - caution)
        )
    if action_type == "browse":
        return _normalized_score(
            0.50 * curiosity
            + 0.35 * novelty_preference
            + 0.15 * (1.0 - caution)
        )
    if action_type == "notify":
        return _normalized_score(
            0.45 * warmth
            + 0.35 * assertiveness
            + 0.20 * sociability
        )
    if action_type == "look":
        return _normalized_score(
            0.45 * curiosity
            + 0.25 * novelty_preference
            + 0.20 * assertiveness
            + 0.10 * (1.0 - caution)
        )
    if action_type == "wait":
        return _normalized_score(
            0.70 * caution
            + 0.30 * (1.0 - assertiveness)
        )
    raise RuntimeError("unsupported action_type for trait alignment")


# Block: Relationship alignment
def _relationship_alignment(
    *,
    action_type: str,
    relationship_priorities: list[dict[str, Any]],
) -> float:
    if not relationship_priorities:
        if action_type in {"speak", "notify"}:
            return 0.50
        return 0.60
    strongest_weight = max(
        _normalized_score(relationship["priority_weight"])
        for relationship in relationship_priorities
    )
    if action_type in {"speak", "notify"}:
        return _normalized_score(0.60 + strongest_weight * 0.40)
    if action_type == "browse":
        has_pending_relation = any(
            relationship["reason_tag"] == "pending_relation"
            for relationship in relationship_priorities
        )
        if has_pending_relation:
            return 0.40
        return _normalized_score(0.45 + strongest_weight * 0.20)
    if action_type == "look":
        return _normalized_score(0.55 + strongest_weight * 0.15)
    if action_type == "wait":
        has_pending_relation = any(
            relationship["reason_tag"] == "pending_relation"
            for relationship in relationship_priorities
        )
        if has_pending_relation:
            return 0.35
        return 0.65
    raise RuntimeError("unsupported action_type for relationship alignment")


# Block: Preference alignment
def _preference_alignment(
    *,
    action_type: str,
    proposal: dict[str, Any],
    learned_preferences: list[dict[str, Any]],
    habit_biases: dict[str, Any],
    memory_bundle: dict[str, Any],
    current_observation: dict[str, Any],
) -> float:
    preferred_action_types = _required_list(
        habit_biases,
        "preferred_action_types",
        "selection_profile.habit_biases.preferred_action_types",
    )
    preferred_observation_kinds = _required_list(
        habit_biases,
        "preferred_observation_kinds",
        "selection_profile.habit_biases.preferred_observation_kinds",
    )
    base_score = 0.50
    if action_type in preferred_action_types:
        base_score += 0.18
    observation_kind = _observation_kind_for_action(action_type)
    if observation_kind is not None and observation_kind in preferred_observation_kinds:
        base_score += 0.10
    base_score += _matched_preference_weight(
        entries=learned_preferences,
        domain="action_type",
        target_key=action_type,
        field_name="selection_profile.learned_preferences",
    ) * 0.20
    if observation_kind is not None:
        base_score += _matched_preference_weight(
            entries=learned_preferences,
            domain="observation_kind",
            target_key=observation_kind,
            field_name="selection_profile.learned_preferences",
        ) * 0.12
    base_score += _memory_support_score(
        action_type=action_type,
        proposal=proposal,
        memory_bundle=memory_bundle,
        current_observation=current_observation,
    ) * 0.20
    return _normalized_score(base_score)


# Block: Memory support score
def _memory_support_score(
    *,
    action_type: str,
    proposal: dict[str, Any],
    memory_bundle: dict[str, Any],
    current_observation: dict[str, Any],
) -> float:
    working_memory_items = _required_list(
        memory_bundle,
        "working_memory_items",
        "cognition_input.memory_bundle.working_memory_items",
    )
    semantic_items = _required_list(
        memory_bundle,
        "semantic_items",
        "cognition_input.memory_bundle.semantic_items",
    )
    recent_event_window = _required_list(
        memory_bundle,
        "recent_event_window",
        "cognition_input.memory_bundle.recent_event_window",
    )
    if action_type == "browse":
        query_hint = _browse_query_text(proposal)
        for memory_entry in semantic_items:
            if not isinstance(memory_entry, dict):
                raise RuntimeError("memory_bundle.semantic_items must contain only objects")
            payload = _required_object(
                memory_entry,
                "payload",
                "memory_bundle.semantic_items.payload",
            )
            if payload.get("query") == query_hint:
                return 0.20
        return 0.75
    if action_type in {"speak", "notify"}:
        if current_observation["input_kind"] == "network_result" and semantic_items:
            return 0.95
        if working_memory_items or recent_event_window:
            return 0.70
        return 0.45
    if action_type == "look":
        if working_memory_items or recent_event_window:
            return 0.70
        return 0.55
    if action_type == "wait":
        if current_observation["input_kind"] == "network_result" and semantic_items:
            return 0.30
        return 0.60
    raise RuntimeError("unsupported action_type for memory support scoring")


# Block: Aversion penalty
def _aversion_penalty(
    *,
    action_type: str,
    learned_aversions: list[dict[str, Any]],
    habit_biases: dict[str, Any],
) -> float:
    penalty = _matched_preference_weight(
        entries=learned_aversions,
        domain="action_type",
        target_key=action_type,
        field_name="selection_profile.learned_aversions",
    )
    observation_kind = _observation_kind_for_action(action_type)
    if observation_kind is not None:
        penalty = max(
            penalty,
            _matched_preference_weight(
                entries=learned_aversions,
                domain="observation_kind",
                target_key=observation_kind,
                field_name="selection_profile.learned_aversions",
            ) * 0.85,
        )
    avoided_action_styles = _required_list(
        habit_biases,
        "avoided_action_styles",
        "selection_profile.habit_biases.avoided_action_styles",
    )
    if _proposal_action_style(action_type) in avoided_action_styles:
        penalty = max(penalty, 0.35)
    return _normalized_score(penalty)


# Block: Emotion alignment
def _emotion_alignment(
    *,
    action_type: str,
    emotion_bias: dict[str, Any],
) -> float:
    if action_type == "wait":
        return _signed_bias_to_score(
            _required_signed_score(
                emotion_bias,
                "caution_bias",
                "selection_profile.emotion_bias.caution_bias",
            )
        )
    if action_type == "browse":
        return _signed_bias_to_score(
            _required_signed_score(
                emotion_bias,
                "approach_bias",
                "selection_profile.emotion_bias.approach_bias",
            )
        )
    if action_type == "look":
        return _signed_bias_to_score(
            _required_signed_score(
                emotion_bias,
                "approach_bias",
                "selection_profile.emotion_bias.approach_bias",
            )
        )
    if action_type in {"speak", "notify"}:
        speech_intensity_bias = _required_signed_score(
            emotion_bias,
            "speech_intensity_bias",
            "selection_profile.emotion_bias.speech_intensity_bias",
        )
        avoidance_bias = _required_signed_score(
            emotion_bias,
            "avoidance_bias",
            "selection_profile.emotion_bias.avoidance_bias",
        )
        return _normalized_score(
            _signed_bias_to_score(speech_intensity_bias) * 0.65
            + (1.0 - _signed_bias_to_score(avoidance_bias)) * 0.35
        )
    raise RuntimeError("unsupported action_type for emotion alignment")


# Block: Drive alignment
def _drive_alignment(
    *,
    action_type: str,
    drive_bias: dict[str, Any],
) -> float:
    if action_type == "browse":
        return _signed_bias_to_score(
            _required_signed_score(
                drive_bias,
                "exploration_bias",
                "selection_profile.drive_bias.exploration_bias",
            )
        )
    if action_type == "look":
        return _signed_bias_to_score(
            _required_signed_score(
                drive_bias,
                "exploration_bias",
                "selection_profile.drive_bias.exploration_bias",
            )
        )
    if action_type in {"speak", "notify"}:
        return _signed_bias_to_score(
            _required_signed_score(
                drive_bias,
                "social_bias",
                "selection_profile.drive_bias.social_bias",
            )
        )
    if action_type == "wait":
        return _signed_bias_to_score(
            _required_signed_score(
                drive_bias,
                "maintenance_bias",
                "selection_profile.drive_bias.maintenance_bias",
            )
        )
    raise RuntimeError("unsupported action_type for drive alignment")


# Block: Experience score
def _experience_fit_score(
    *,
    persona_consistency: dict[str, Any],
) -> float:
    preference_alignment = _normalized_score(persona_consistency["preference_alignment"])
    aversion_penalty = _normalized_score(persona_consistency["aversion_penalty"])
    return _normalized_score(
        preference_alignment * 0.65
        + (1.0 - aversion_penalty) * 0.35
    )


def _aversion_conflict_decision(candidate: dict[str, Any]) -> tuple[str, str] | None:
    priority_hint_score = _normalized_score(candidate["priority_hint_score"])
    if priority_hint_score >= NON_URGENT_PRIORITY_THRESHOLD:
        return None
    aversion_penalty = _normalized_score(
        candidate["persona_consistency"]["aversion_penalty"]
    )
    relationship_fit_score = _normalized_score(
        candidate["relationship_fit_score"]
    )
    if (
        aversion_penalty >= AVERSION_REJECT_THRESHOLD
        and relationship_fit_score <= RELATIONSHIP_REJECT_THRESHOLD
    ):
        return ("reject", "aversion_relationship_conflict_rejected")
    if (
        aversion_penalty >= AVERSION_HOLD_THRESHOLD
        and relationship_fit_score <= RELATIONSHIP_HOLD_THRESHOLD
    ):
        return ("hold", "aversion_relationship_conflict_held")
    return None


def _has_strong_aversion(
    *,
    action_type: str,
    learned_aversions: list[dict[str, Any]],
    habit_biases: dict[str, Any],
) -> bool:
    del habit_biases
    strong_action_type_aversion = _matched_preference_entry(
        entries=learned_aversions,
        domain="action_type",
        target_key=action_type,
        field_name="selection_profile.learned_aversions",
    )
    if (
        strong_action_type_aversion is not None
        and _normalized_score(strong_action_type_aversion["weight"]) >= 0.80
        and int(strong_action_type_aversion["evidence_count"]) >= 4
    ):
        return True
    observation_kind = _observation_kind_for_action(action_type)
    strong_observation_aversion = None
    if observation_kind is not None:
        strong_observation_aversion = _matched_preference_entry(
            entries=learned_aversions,
            domain="observation_kind",
            target_key=observation_kind,
            field_name="selection_profile.learned_aversions",
        )
    if (
        strong_observation_aversion is not None
        and _normalized_score(strong_observation_aversion["weight"]) >= 0.80
        and int(strong_observation_aversion["evidence_count"]) >= 4
    ):
        return True
    return False


# Block: Preference match weight
def _matched_preference_weight(
    *,
    entries: list[dict[str, Any]],
    domain: str,
    target_key: str,
    field_name: str,
) -> float:
    matched_entry = _matched_preference_entry(
        entries=entries,
        domain=domain,
        target_key=target_key,
        field_name=field_name,
    )
    if matched_entry is None:
        return 0.0
    return _normalized_score(matched_entry["weight"])


# Block: Preference match entry
def _matched_preference_entry(
    *,
    entries: list[dict[str, Any]],
    domain: str,
    target_key: str,
    field_name: str,
) -> dict[str, Any] | None:
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError(f"{field_name} must contain only objects")
        if entry.get("domain") != domain:
            continue
        if entry.get("target_key") != target_key:
            continue
        evidence_count = entry.get("evidence_count")
        if not isinstance(evidence_count, int) or evidence_count < 1:
            raise RuntimeError(f"{field_name}.evidence_count must be integer >= 1")
        return entry
    return None


# Block: Observation kind helper
def _observation_kind_for_action(action_type: str) -> str | None:
    return {
        "browse": "web_search",
        "look": "camera_scene",
    }.get(action_type)


# Block: Drive score
def _drive_relief_score(
    *,
    action_type: str,
    drive_bias: dict[str, Any],
) -> float:
    bias_key = {
        "speak": "social_bias",
        "notify": "social_bias",
        "browse": "exploration_bias",
        "look": "exploration_bias",
        "wait": "maintenance_bias",
    }.get(action_type)
    if bias_key is None:
        raise RuntimeError("unsupported action_type for drive scoring")
    bias_value = drive_bias[bias_key]
    if not isinstance(bias_value, (int, float)):
        raise RuntimeError("selection_profile.drive_bias values must be numeric")
    normalized_value = (float(bias_value) + 1.0) / 2.0
    return _normalized_score(normalized_value)


# Block: Task score
def _task_fit_score(
    action_type: str,
    *,
    proposal: dict[str, Any],
    task_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
) -> float:
    waiting_external_tasks = _required_list(
        task_snapshot,
        "waiting_external_tasks",
        "cognition_input.task_snapshot.waiting_external_tasks",
    )
    if action_type == "speak":
        if current_observation["input_kind"] == "network_result":
            return 0.95
        return 1.00
    if action_type == "notify":
        if current_observation["input_kind"] == "network_result":
            return 0.85
        return 0.75
    if action_type == "browse":
        query = _browse_query_text(proposal)
        if _has_waiting_browse_query(waiting_external_tasks, query):
            return 0.10
        if current_observation["input_kind"] == "network_result":
            return 0.25
        return 0.65
    if action_type == "look":
        if current_observation["input_kind"] == "network_result":
            return 0.30
        return 0.70
    if action_type == "wait":
        if waiting_external_tasks:
            return 0.75
        return 0.60
    raise RuntimeError("unsupported action_type for task scoring")


# Block: Stability score
def _expected_stability_score(
    action_type: str,
    *,
    task_snapshot: dict[str, Any],
) -> float:
    waiting_external_tasks = _required_list(
        task_snapshot,
        "waiting_external_tasks",
        "cognition_input.task_snapshot.waiting_external_tasks",
    )
    if action_type == "wait":
        return 0.95
    if action_type == "speak":
        return 0.80
    if action_type == "notify":
        return 0.65
    if action_type == "browse":
        if waiting_external_tasks:
            return 0.35
        return 0.55
    if action_type == "look":
        if waiting_external_tasks:
            return 0.50
        return 0.68
    raise RuntimeError("unsupported action_type for stability scoring")


# Block: Proposal materialization
def _materialize_selected_proposal(
    *,
    proposal: dict[str, Any],
    message_id: str,
) -> dict[str, Any]:
    selected_proposal = dict(proposal)
    selected_proposal["proposal_id"] = _proposal_id(proposal, message_id)
    selected_proposal["message_id"] = message_id
    return selected_proposal


# Block: Action command builder
def _build_action_command(
    *,
    action_type: str,
    pending_channel: str,
    proposal: dict[str, Any],
    response_text: str,
) -> dict[str, Any]:
    if action_type == "speak":
        return {
            "command_id": opaque_action_id("cmd"),
            "command_type": "speak_ui_message",
            "actuator_port": "browser_chat_ui",
            "target": {
                "channel": pending_channel,
            },
            "parameters": {
                "message_id": str(proposal["message_id"]),
                "text": response_text,
                "role": "assistant",
            },
            "preconditions": {
                "channel_matches_input": True,
            },
            "stop_conditions": {
                "kind": "message_completed_or_cancelled",
            },
            "timeout_ms": 30_000,
            "requires_reobserve": False,
            "expected_effects": {
                "emitted_event_types": ["status", "token", "message", "status"],
                "status_code_after": "idle",
            },
            "proposal_ref": str(proposal["proposal_id"]),
        }
    if action_type == "notify":
        return {
            "command_id": opaque_action_id("cmd"),
            "command_type": "dispatch_notice",
            "actuator_port": "notification",
            "target": {
                "channel": pending_channel,
            },
            "parameters": {
                "notice_code": "llm_notify",
                "text": response_text,
            },
            "preconditions": {
                "channel_matches_input": True,
            },
            "stop_conditions": {
                "kind": "notification_dispatched",
            },
            "timeout_ms": 5_000,
            "requires_reobserve": False,
            "expected_effects": {
                "emitted_event_types": ["notice", "status"],
                "status_code_after": "idle",
            },
            "proposal_ref": str(proposal["proposal_id"]),
        }
    if action_type == "browse":
        query = _browse_query_text(proposal)
        return {
            "command_id": opaque_action_id("cmd"),
            "command_type": "enqueue_browse_task",
            "actuator_port": "task_state",
            "target": {
                "queue": "task_state",
            },
            "parameters": {
                "task_id": opaque_action_id("task"),
                "query": query,
                "target_channel": pending_channel,
            },
            "preconditions": {
                "runtime_allows_browse": True,
            },
            "stop_conditions": {
                "kind": "task_queued",
            },
            "timeout_ms": 5_000,
            "requires_reobserve": False,
            "expected_effects": {
                "queued_task_kind": "browse",
                "queued_task_status": "waiting_external",
            },
            "proposal_ref": str(proposal["proposal_id"]),
        }
    if action_type == "look":
        return {
            "command_id": opaque_action_id("cmd"),
            "command_type": "control_camera_look",
            "actuator_port": "wifi_camera",
            "target": {
                "device": "primary_camera",
            },
            "parameters": {
                "message_id": str(proposal["message_id"]),
                "text": response_text,
                **_look_command_parameters(proposal),
            },
            "preconditions": {
                "runtime_allows_camera_look": True,
            },
            "stop_conditions": {
                "kind": "camera_move_completed",
            },
            "timeout_ms": 10_000,
            "requires_reobserve": False,
            "expected_effects": {
                "emitted_event_types": ["status", "message", "status"],
                "status_code_after": "idle",
            },
            "proposal_ref": str(proposal["proposal_id"]),
        }
    raise RuntimeError("unsupported action_type for execute command")


# Block: Browse query
def _browse_query_text(proposal: dict[str, Any]) -> str:
    query = proposal.get("query")
    if not isinstance(query, str) or not query.strip():
        raise RuntimeError("browse action requires non-empty query")
    return query.strip()


# Block: Look target helpers
def _validated_look_target(proposal: dict[str, Any]) -> dict[str, str]:
    direction = proposal.get("direction")
    if isinstance(direction, str) and direction.strip():
        normalized_direction = direction.strip()
        if normalized_direction not in {"left", "right", "up", "down"}:
            raise RuntimeError("look action direction must be left/right/up/down")
        if proposal.get("preset_id") is not None or proposal.get("preset_name") is not None:
            raise RuntimeError("look action must not mix direction and preset")
        return {"direction": normalized_direction}
    preset_id = proposal.get("preset_id")
    if isinstance(preset_id, str) and preset_id.strip():
        if proposal.get("preset_name") is not None:
            raise RuntimeError("look action must specify only one preset field")
        return {"preset_id": preset_id.strip()}
    preset_name = proposal.get("preset_name")
    if isinstance(preset_name, str) and preset_name.strip():
        return {"preset_name": preset_name.strip()}
    raise RuntimeError("look action requires direction or preset")


def _look_command_parameters(proposal: dict[str, Any]) -> dict[str, Any]:
    return _validated_look_target(proposal)


# Block: Candidate payload
def _candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in candidate.items()
        if key not in {"proposal", "persona_consistency"}
    }


# Block: Empty candidate score
def _empty_candidate_score() -> dict[str, Any]:
    return {
        "proposal_id": "none",
        "hard_gate_passed": False,
        "task_fit_score": 0.0,
        "personality_fit_score": 0.0,
        "relationship_fit_score": 0.0,
        "experience_fit_score": 0.0,
        "drive_relief_score": 0.0,
        "expected_stability_score": 0.0,
        "priority_hint_score": 0.0,
        "total_score": 0.0,
    }


# Block: Proposal id
def _proposal_id(proposal: dict[str, Any], message_id: str) -> str:
    proposal_id = proposal.get("proposal_id")
    if isinstance(proposal_id, str) and proposal_id:
        return proposal_id
    return f"prop_{message_id}"


# Block: Action type validation
def _validated_action_type(proposal: dict[str, Any]) -> str:
    action_type = proposal.get("action_type")
    if not isinstance(action_type, str) or not action_type:
        raise RuntimeError("cognition_result.action_proposals.action_type must be a non-empty string")
    if action_type not in {"speak", "browse", "notify", "look", "wait"}:
        raise RuntimeError("unsupported action_type in chat validator")
    return action_type


# Block: Proposal priority
def _proposal_priority_score(proposal: dict[str, Any]) -> float:
    if "priority" not in proposal:
        raise RuntimeError("cognition_result.action_proposals.priority is required")
    return _normalized_score(proposal["priority"])


# Block: Waiting browse query check
def _has_waiting_browse_for_same_query(
    *,
    proposal: dict[str, Any],
    task_snapshot: dict[str, Any],
) -> bool:
    query = _browse_query_text(proposal)
    waiting_external_tasks = _required_list(
        task_snapshot,
        "waiting_external_tasks",
        "cognition_input.task_snapshot.waiting_external_tasks",
    )
    return _has_waiting_browse_query(waiting_external_tasks, query)


def _has_waiting_browse_query(
    waiting_external_tasks: list[Any],
    query: str,
) -> bool:
    for task_entry in waiting_external_tasks:
        if not isinstance(task_entry, dict):
            raise RuntimeError("task_snapshot.waiting_external_tasks must contain only objects")
        if task_entry["task_kind"] != "browse":
            continue
        completion_hint = _required_object(
            task_entry,
            "completion_hint",
            "task_snapshot.waiting_external_tasks.completion_hint",
        )
        if completion_hint.get("query") == query:
            return True
    return False


# Block: Proposal action style
def _proposal_action_style(action_type: str) -> str:
    return {
        "speak": "conversational_response",
        "notify": "push_notice",
        "browse": "external_lookup",
        "look": "viewpoint_adjustment",
        "wait": "defer_action",
    }[action_type]


# Block: Required object helper
def _required_object(container: dict[str, Any], key: str, field_name: str) -> dict[str, Any]:
    value = container.get(key)
    if not isinstance(value, dict):
        raise RuntimeError(f"{field_name} must be an object")
    return value


# Block: Required list helper
def _required_list(container: dict[str, Any], key: str, field_name: str) -> list[Any]:
    value = container.get(key)
    if not isinstance(value, list):
        raise RuntimeError(f"{field_name} must be a list")
    return value


# Block: Trait value helper
def _trait_value(trait_values: dict[str, Any], key: str) -> float:
    if key not in trait_values:
        raise RuntimeError(f"selection_profile.trait_values.{key} is required")
    return _signed_bias_to_score(
        _required_signed_score(
            trait_values,
            key,
            f"selection_profile.trait_values.{key}",
        )
    )


# Block: Signed score helper
def _required_signed_score(container: dict[str, Any], key: str, field_name: str) -> float:
    if key not in container:
        raise RuntimeError(f"{field_name} is required")
    value = container[key]
    if isinstance(value, bool):
        raise RuntimeError(f"{field_name} must not be boolean")
    if not isinstance(value, (int, float)):
        raise RuntimeError(f"{field_name} must be numeric")
    numeric_value = float(value)
    if numeric_value < -1.0 or numeric_value > 1.0:
        raise RuntimeError(f"{field_name} must be within -1.0..1.0")
    return numeric_value


# Block: Signed conversion helper
def _signed_bias_to_score(value: float) -> float:
    return _normalized_score((value + 1.0) / 2.0)


# Block: Score helper
def _normalized_score(value: Any) -> float:
    if isinstance(value, bool):
        raise RuntimeError("score values must not be boolean")
    if not isinstance(value, (int, float)):
        raise RuntimeError("score values must be numeric")
    numeric_value = float(value)
    if numeric_value < 0.0:
        return 0.0
    if numeric_value > 1.0:
        return 1.0
    return numeric_value
