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
        best_candidate = max(scored_candidates, key=lambda candidate: candidate["total_score"])
        return ValidatedChatAction(
            decision="reject",
            decision_reason="all_candidates_rejected_by_hard_gate",
            proposal=None,
            action_command=None,
            action_candidate_score=_candidate_payload(best_candidate),
        )
    best_candidate = max(executable_candidates, key=lambda candidate: candidate["total_score"])
    action_type = str(best_candidate["proposal"]["action_type"])
    selected_proposal = _materialize_selected_proposal(
        proposal=best_candidate["proposal"],
        message_id=message_id,
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
    if action_type == "browse":
        return ValidatedChatAction(
            decision="hold",
            decision_reason="action_type_not_implemented",
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
    relationship_priorities = selection_profile["relationship_priorities"]
    drive_bias = selection_profile["drive_bias"]
    interaction_style = selection_profile["interaction_style"]
    learned_aversions = selection_profile["learned_aversions"]
    hard_gate_passed = _passes_hard_gate(
        proposal=proposal,
        pending_channel=pending_channel,
        cognition_input=cognition_input,
        learned_aversions=learned_aversions,
    )
    priority_hint_score = _proposal_priority_score(proposal)
    personality_fit_score = _personality_fit_score(
        action_type=action_type,
        interaction_style=interaction_style,
        response_text=response_text,
    )
    relationship_fit_score = 0.80 if relationship_priorities else 0.50
    experience_fit_score = _experience_fit_score(
        action_type=action_type,
        learned_aversions=learned_aversions,
    )
    drive_relief_score = _drive_relief_score(
        action_type=action_type,
        drive_bias=drive_bias,
    )
    expected_stability_score = _expected_stability_score(action_type)
    task_fit_score = _task_fit_score(action_type)
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
    }


# Block: Hard gate check
def _passes_hard_gate(
    *,
    proposal: dict[str, Any],
    pending_channel: str,
    cognition_input: dict[str, Any],
    learned_aversions: list[dict[str, Any]],
) -> bool:
    action_type = _validated_action_type(proposal)
    invariants = cognition_input["persona_snapshot"]["invariants"]
    forbidden_action_types = invariants.get("forbidden_action_types", [])
    if action_type in forbidden_action_types:
        return False
    target_channel = proposal.get("target_channel")
    if action_type in {"speak", "notify"} and target_channel != pending_channel:
        return False
    return not _has_strong_aversion(
        action_type=action_type,
        learned_aversions=learned_aversions,
    )


# Block: Personality fit scoring
def _personality_fit_score(
    *,
    action_type: str,
    interaction_style: dict[str, Any],
    response_text: str,
) -> float:
    if action_type == "speak":
        return _speech_personality_fit(
            interaction_style=interaction_style,
            response_text=response_text,
        )
    if action_type == "browse":
        confirmation_style = interaction_style.get("confirmation_style")
        if confirmation_style == "careful":
            return 0.90
        if confirmation_style == "balanced":
            return 0.75
        return 0.55
    if action_type == "notify":
        speech_tone = interaction_style.get("speech_tone")
        if speech_tone in {"warm", "gentle"}:
            return 0.85
        return 0.65
    if action_type == "wait":
        confirmation_style = interaction_style.get("confirmation_style")
        if confirmation_style == "careful":
            return 0.90
        return 0.60
    raise RuntimeError("unsupported action_type for personality scoring")


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
def _experience_fit_score(
    *,
    action_type: str,
    learned_aversions: list[dict[str, Any]],
) -> float:
    if _has_strong_aversion(
        action_type=action_type,
        learned_aversions=learned_aversions,
    ):
        return 0.20
    if action_type == "wait":
        return 0.70
    if action_type == "browse":
        return 0.75
    if action_type == "notify":
        return 0.80
    if action_type == "speak":
        return 0.80
    raise RuntimeError("unsupported action_type for experience scoring")


def _has_strong_aversion(
    *,
    action_type: str,
    learned_aversions: list[dict[str, Any]],
) -> bool:
    for aversion in learned_aversions:
        if not isinstance(aversion, dict):
            raise RuntimeError("selection_profile.learned_aversions must contain only objects")
        if (
            aversion.get("target_action_type") == action_type
            and _normalized_score(aversion.get("weight", 0.0)) >= 0.80
            and int(aversion.get("evidence_count", 0)) >= 4
        ):
            return True
    return False


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
def _task_fit_score(action_type: str) -> float:
    if action_type == "speak":
        return 1.00
    if action_type == "notify":
        return 0.75
    if action_type == "browse":
        return 0.65
    if action_type == "wait":
        return 0.60
    raise RuntimeError("unsupported action_type for task scoring")


# Block: Stability score
def _expected_stability_score(action_type: str) -> float:
    if action_type == "wait":
        return 0.95
    if action_type == "speak":
        return 0.80
    if action_type == "notify":
        return 0.65
    if action_type == "browse":
        return 0.55
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
            "command_type": "speak_ui_message",
            "target_channel": pending_channel,
            "message_id": str(proposal["message_id"]),
            "text": response_text,
            "proposal_ref": str(proposal["proposal_id"]),
        }
    if action_type == "notify":
        return {
            "command_type": "browser_notice",
            "target_channel": pending_channel,
            "notice_code": "llm_notify",
            "text": response_text,
            "proposal_ref": str(proposal["proposal_id"]),
        }
    raise RuntimeError("unsupported action_type for execute command")


# Block: Candidate payload
def _candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in candidate.items()
        if key != "proposal"
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
    if action_type not in {"speak", "browse", "notify", "wait"}:
        raise RuntimeError("unsupported action_type in chat validator")
    return action_type


# Block: Proposal priority
def _proposal_priority_score(proposal: dict[str, Any]) -> float:
    if "priority" not in proposal:
        raise RuntimeError("cognition_result.action_proposals.priority is required")
    return _normalized_score(proposal["priority"])


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
