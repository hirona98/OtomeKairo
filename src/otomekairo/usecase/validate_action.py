"""Validate and select action proposals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Block: Score weights
TASK_FIT_WEIGHT = 0.24
PERSONALITY_FIT_WEIGHT = 0.24
RELATIONSHIP_FIT_WEIGHT = 0.18
EXPERIENCE_FIT_WEIGHT = 0.16
DRIVE_RELIEF_WEIGHT = 0.10
EXPECTED_STABILITY_WEIGHT = 0.08


# Block: Validation result
@dataclass(frozen=True, slots=True)
class ValidatedChatAction:
    proposal: dict[str, Any]
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
    speak_proposals = [
        proposal
        for proposal in proposals
        if proposal.get("action_type") == "speak" and proposal.get("target_channel") == pending_channel
    ]
    if not speak_proposals:
        raise RuntimeError("cognition_result.action_proposals must include speak for the active channel")
    _validate_invariants(cognition_input)
    scored_candidates = [
        _score_speak_proposal(
            proposal=proposal,
            message_id=message_id,
            cognition_input=cognition_input,
            response_text=response_text,
        )
        for proposal in speak_proposals
    ]
    best_candidate = max(scored_candidates, key=lambda candidate: candidate["total_score"])
    if float(best_candidate["personality_fit_score"]) < 0.30:
        raise RuntimeError("validated speak proposal is below minimum personality_fit_score")
    selected_proposal = _materialize_selected_proposal(
        proposal=best_candidate["proposal"],
        message_id=message_id,
    )
    candidate_score = {
        key: value
        for key, value in best_candidate.items()
        if key != "proposal"
    }
    return ValidatedChatAction(
        proposal=selected_proposal,
        action_candidate_score=candidate_score,
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
        validated_proposals.append(proposal)
    return validated_proposals


# Block: Invariant validation
def _validate_invariants(cognition_input: dict[str, Any]) -> None:
    invariants = cognition_input["persona_snapshot"]["invariants"]
    forbidden_action_types = invariants.get("forbidden_action_types", [])
    if "speak" in forbidden_action_types:
        raise RuntimeError("invariants forbid speak")


# Block: Candidate scoring
def _score_speak_proposal(
    *,
    proposal: dict[str, Any],
    message_id: str,
    cognition_input: dict[str, Any],
    response_text: str,
) -> dict[str, Any]:
    selection_profile = cognition_input["selection_profile"]
    relationship_priorities = selection_profile["relationship_priorities"]
    drive_bias = selection_profile["drive_bias"]
    interaction_style = selection_profile["interaction_style"]
    learned_aversions = selection_profile["learned_aversions"]
    priority_hint_score = _normalized_score(proposal.get("priority", 0.0))
    personality_fit_score = _speech_personality_fit(
        interaction_style=interaction_style,
        response_text=response_text,
    )
    relationship_fit_score = 0.80 if relationship_priorities else 0.50
    experience_fit_score = _experience_fit_score(learned_aversions)
    drive_relief_score = _drive_relief_score(drive_bias)
    expected_stability_score = 0.80
    task_fit_score = 1.00
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
        "hard_gate_passed": True,
        "task_fit_score": task_fit_score,
        "personality_fit_score": personality_fit_score,
        "relationship_fit_score": relationship_fit_score,
        "experience_fit_score": experience_fit_score,
        "drive_relief_score": drive_relief_score,
        "expected_stability_score": expected_stability_score,
        "priority_hint_score": priority_hint_score,
        "total_score": _normalized_score(total_score),
    }


# Block: Speech fit scoring
def _speech_personality_fit(
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


# Block: Experience score
def _experience_fit_score(learned_aversions: list[dict[str, Any]]) -> float:
    for aversion in learned_aversions:
        if not isinstance(aversion, dict):
            raise RuntimeError("selection_profile.learned_aversions must contain only objects")
        if (
            aversion.get("target_action_type") == "speak"
            and _normalized_score(aversion.get("weight", 0.0)) >= 0.80
            and int(aversion.get("evidence_count", 0)) >= 4
        ):
            return 0.20
    return 0.80


# Block: Drive score
def _drive_relief_score(drive_bias: dict[str, Any]) -> float:
    social_bias = drive_bias["social_bias"]
    if not isinstance(social_bias, (int, float)):
        raise RuntimeError("selection_profile.drive_bias.social_bias must be numeric")
    normalized_value = (float(social_bias) + 1.0) / 2.0
    return _normalized_score(normalized_value)


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


# Block: Proposal id
def _proposal_id(proposal: dict[str, Any], message_id: str) -> str:
    proposal_id = proposal.get("proposal_id")
    if isinstance(proposal_id, str) and proposal_id:
        return proposal_id
    return f"prop_{message_id}"


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
