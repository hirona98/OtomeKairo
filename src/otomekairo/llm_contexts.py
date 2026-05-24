from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True, slots=True)
class InitiativeCandidateFamily:
    family: str
    available: bool
    selected: bool
    priority_score: float
    reason_summary: str | None = None
    preferred_result_kind: str | None = None
    preferred_result_reason_summary: str | None = None
    blocking_reason_summary: str | None = None
    preferred_capability_id: str | None = None
    preferred_capability_input: dict[str, Any] | None = None

    def with_selected(self, *, selected: bool) -> "InitiativeCandidateFamily":
        return replace(self, selected=selected)

    def to_prompt_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "family": self.family,
            "available": self.available,
            "selected": self.selected,
            "priority_score": self.priority_score,
        }
        for key, value in (
            ("reason_summary", self.reason_summary),
            ("preferred_result_kind", self.preferred_result_kind),
            ("preferred_result_reason_summary", self.preferred_result_reason_summary),
            ("blocking_reason_summary", self.blocking_reason_summary),
            ("preferred_capability_id", self.preferred_capability_id),
        ):
            if isinstance(value, str) and value.strip():
                payload[key] = value
        if isinstance(self.preferred_capability_input, dict):
            payload["preferred_capability_input"] = self.preferred_capability_input
        return payload


@dataclass(frozen=True, slots=True)
class InitiativeContext:
    trigger_kind: str
    opportunity_summary: str
    time_context_summary: dict[str, Any]
    foreground_signal_summary: dict[str, Any]
    initiative_baseline: dict[str, Any]
    runtime_state_summary: dict[str, Any]
    recent_turn_summary: list[dict[str, str]]
    drive_summaries: list[dict[str, Any]]
    pending_intent_summaries: list[dict[str, Any]]
    world_state_summary: list[dict[str, Any]]
    ongoing_action_summary: dict[str, Any] | None
    capability_summary: dict[str, Any]
    candidate_families: list[InitiativeCandidateFamily]
    selected_candidate_family: str | None
    intervention_state: dict[str, Any]
    suppression_summary: dict[str, Any]
    intervention_risk_summary: str

    def selected_family_entry(self) -> InitiativeCandidateFamily | None:
        for family in self.candidate_families:
            if family.selected is True:
                return family
            if (
                isinstance(self.selected_candidate_family, str)
                and family.family.strip() == self.selected_candidate_family
            ):
                return family
        return None

    def to_prompt_payload(self) -> dict[str, Any]:
        return {
            "trigger_kind": self.trigger_kind,
            "opportunity_summary": self.opportunity_summary,
            "time_context_summary": self.time_context_summary,
            "foreground_signal_summary": self.foreground_signal_summary,
            "initiative_baseline": self.initiative_baseline,
            "runtime_state_summary": self.runtime_state_summary,
            "recent_turn_summary": self.recent_turn_summary,
            "drive_summaries": self.drive_summaries,
            "pending_intent_summaries": self.pending_intent_summaries,
            "world_state_summary": self.world_state_summary,
            "ongoing_action_summary": self.ongoing_action_summary,
            "capability_summary": self.capability_summary,
            "candidate_families": [family.to_prompt_payload() for family in self.candidate_families],
            "selected_candidate_family": self.selected_candidate_family,
            "intervention_state": self.intervention_state,
            "suppression_summary": self.suppression_summary,
            "intervention_risk_summary": self.intervention_risk_summary,
        }


@dataclass(frozen=True, slots=True)
class DecisionContext:
    input_text: str
    trigger_kind: str
    recent_turns: list[dict[str, Any]]
    time_context: dict[str, Any]
    affect_context: dict[str, Any]
    drive_state_summary: list[dict[str, Any]] | None
    foreground_world_state: list[dict[str, Any]] | None
    ongoing_action_summary: dict[str, Any] | None
    capability_decision_view: list[dict[str, Any]] | None
    initiative_context: InitiativeContext | None
    capability_result_context: dict[str, Any] | None
    visual_observation_context: dict[str, Any] | None
    recall_hint: dict[str, Any]
    recall_pack: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReplyContext:
    input_text: str
    recent_turns: list[dict[str, Any]]
    time_context: dict[str, Any]
    affect_context: dict[str, Any]
    drive_state_summary: list[dict[str, Any]] | None
    foreground_world_state: list[dict[str, Any]] | None
    ongoing_action_summary: dict[str, Any] | None
    initiative_context: InitiativeContext | None
    visual_observation_context: dict[str, Any] | None
    recall_hint: dict[str, Any]
    recall_pack: dict[str, Any]
    decision: dict[str, Any]
