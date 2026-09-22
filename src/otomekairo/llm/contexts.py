from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from otomekairo.interaction import InteractionContext


PERSONA_PROMPT_EXCERPT_LIMIT = 240


PERSONA_CONTEXT_USE_POLICY = (
    "人格全体を、常に考え方、判断、振る舞い、話し方の基底として使う。"
    "本人の発言・投稿・返信は、人格本文の話し方、一人称、語尾、距離感に従う。"
    "事実は記憶と観測の根拠に従い、各処理の出力契約を守る。"
)
PERSONA_CONTEXT_ROLES = frozenset({
    "decision_generation",
    "autonomous_step_generation",
    "expression_generation",
    "disclosure_review",
    "pending_intent_selection",
    "initiative_entry_check",
    "input_interpretation",
    "recall_pack_selection",
    "event_evidence_generation",
    "memory_interpretation",
    "memory_reflection_summary",
    "world_state",
    "activity_state",
    "visual_observation",
    "drive_state",
})


@dataclass(frozen=True, slots=True)
class PersonaContext:
    display_name: str
    initiative_baseline: dict[str, Any]
    persona_prompt_text: str
    expression_addon: str | None
    use_policy: str

    def to_prompt_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "initiative_baseline": self.initiative_baseline,
            "persona_prompt_text": self.persona_prompt_text,
            "use_policy": self.use_policy,
        }
        if isinstance(self.expression_addon, str) and self.expression_addon.strip():
            payload["expression_addon"] = self.expression_addon
        return payload

    def to_summary_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "initiative_baseline": self.initiative_baseline,
            "persona_prompt_excerpt": self._prompt_excerpt(),
        }
        return payload

    def _prompt_excerpt(self) -> str:
        normalized = " ".join(self.persona_prompt_text.split())
        if len(normalized) <= PERSONA_PROMPT_EXCERPT_LIMIT:
            return normalized
        return normalized[: PERSONA_PROMPT_EXCERPT_LIMIT - 1].rstrip() + "…"


def build_persona_context(
    persona: dict[str, Any],
    *,
    role: str,
    include_expression: bool = False,
) -> PersonaContext:
    normalized_role = role.strip()
    if normalized_role not in PERSONA_CONTEXT_ROLES:
        raise ValueError(f"unsupported persona_context role: {role}")
    display_name = _persona_text(persona.get("display_name")) or "OtomeKairo"
    initiative_level = _persona_text(persona.get("initiative_baseline")) or "medium"
    persona_prompt_text = _persona_text(persona.get("persona_prompt")) or ""
    expression_addon = _persona_text(persona.get("expression_addon")) if include_expression else None
    return PersonaContext(
        display_name=display_name,
        initiative_baseline={
            "level": initiative_level,
            "summary_text": persona_initiative_baseline_summary(initiative_level),
        },
        persona_prompt_text=persona_prompt_text,
        expression_addon=expression_addon,
        use_policy=PERSONA_CONTEXT_USE_POLICY,
    )


def build_persona_context_summary(persona: dict[str, Any]) -> dict[str, Any]:
    return build_persona_context(persona, role="decision_generation").to_summary_payload()


def persona_initiative_baseline_summary(level: str) -> str:
    if level == "low":
        return "自発発話は控えめ寄りで、前景理由が弱ければ見送る。"
    if level == "high":
        return "自発発話は強めで、前景理由が揃うと関わる判断を取りやすい。"
    return "自発発話は中庸で、関わる、保留する、見送るを文脈で選ぶ。"


def _persona_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


@dataclass(frozen=True, slots=True)
class CurrentInput:
    sender_kind: str
    sender_ref: str | None
    source_kind: str
    response_target_refs: tuple[str, ...]
    interaction_context: InteractionContext | None
    text: str

    @property
    def interaction_ref(self) -> str | None:
        if self.interaction_context is None:
            return None
        return self.interaction_context.interaction_ref

    @property
    def participant_refs(self) -> tuple[str, ...]:
        if self.interaction_context is None:
            return ()
        return self.interaction_context.participant_refs

    def to_prompt_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "sender_kind": self.sender_kind,
            "sender_ref": self.sender_ref,
            "source_kind": self.source_kind,
            "response_target_refs": list(self.response_target_refs),
            "text": self.text,
        }
        if self.interaction_context is not None:
            payload["interaction_context"] = self.interaction_context.to_prompt_payload()
        return payload

    @classmethod
    def from_source_payload(cls, payload: dict[str, Any]) -> "CurrentInput | None":
        sender_kind = payload.get("sender_kind")
        source_kind = payload.get("source_kind")
        text = payload.get("text")
        if sender_kind != "person" or source_kind != "user_message":
            return None
        if not isinstance(text, str) or not text.strip():
            return None
        raw_refs = payload.get("response_target_refs")
        if not isinstance(raw_refs, list):
            return None
        response_target_refs = tuple(
            value.strip()
            for value in raw_refs
            if isinstance(value, str) and value.strip()
        )
        if not response_target_refs:
            return None
        sender_ref = payload.get("sender_ref")
        if not isinstance(sender_ref, str) or not sender_ref.strip():
            return None
        raw_context = payload.get("interaction_context")
        if not isinstance(raw_context, dict):
            return None
        from otomekairo.interaction import normalize_interaction_context

        return cls(
            sender_kind="person",
            sender_ref=sender_ref.strip(),
            source_kind="user_message",
            response_target_refs=response_target_refs,
            interaction_context=normalize_interaction_context(
                raw_context,
                required=True,
                require_speaker=False,
            ),
            text=text,
        )

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
    initiative_entry_summary: dict[str, Any] | None
    time_context_summary: dict[str, Any]
    foreground_signal_summary: dict[str, Any]
    activity_context: dict[str, Any] | None
    initiative_baseline: dict[str, Any]
    persona_context_summary: dict[str, Any]
    runtime_state_summary: dict[str, Any]
    recent_turn_summary: list[dict[str, str]]
    drive_summaries: list[dict[str, Any]]
    pending_intent_summaries: list[dict[str, Any]]
    world_state_summary: list[dict[str, Any]]
    ongoing_action_summary: dict[str, Any] | None
    capability_summary: dict[str, Any]
    candidate_families: list[InitiativeCandidateFamily]
    selected_candidate_family: str | None
    speech_timing_state: dict[str, Any]
    suppression_summary: dict[str, Any]
    speech_timing_summary: str
    speech_frequency_level: int = 5

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
            "initiative_entry_summary": self.initiative_entry_summary,
            "time_context_summary": self.time_context_summary,
            "foreground_signal_summary": self.foreground_signal_summary,
            "activity_context": self.activity_context,
            "initiative_baseline": self.initiative_baseline,
            "persona_context_summary": self.persona_context_summary,
            "runtime_state_summary": self.runtime_state_summary,
            "recent_turn_summary": self.recent_turn_summary,
            "drive_summaries": self.drive_summaries,
            "pending_intent_summaries": self.pending_intent_summaries,
            "world_state_summary": self.world_state_summary,
            "ongoing_action_summary": self.ongoing_action_summary,
            "capability_summary": self.capability_summary,
            "candidate_families": [family.to_prompt_payload() for family in self.candidate_families],
            "selected_candidate_family": self.selected_candidate_family,
            "speech_timing_state": self.speech_timing_state,
            "suppression_summary": self.suppression_summary,
            "speech_timing_summary": self.speech_timing_summary,
            "speech_frequency_level": self.speech_frequency_level,
        }


@dataclass(frozen=True, slots=True)
class DecisionContext:
    input_text: str
    current_input: CurrentInput
    trigger_kind: str
    recent_turns: list[dict[str, Any]]
    time_context: dict[str, Any]
    affect_context: dict[str, Any]
    drive_state_summary: list[dict[str, Any]] | None
    foreground_world_state: list[dict[str, Any]] | None
    activity_context: dict[str, Any] | None
    ongoing_action_summary: dict[str, Any] | None
    autonomous_run_summaries: list[dict[str, Any]] | None
    capability_decision_view: list[dict[str, Any]] | None
    initiative_context: InitiativeContext | None
    capability_result_context: dict[str, Any] | None
    visual_observation_context: dict[str, Any] | None
    self_state_context: dict[str, Any] | None
    relationship_context: dict[str, Any] | None
    prediction_error_context: dict[str, Any] | None
    default_mode_context: dict[str, Any] | None
    workspace_context: dict[str, Any] | None
    recall_hint: dict[str, Any]
    recall_pack: dict[str, Any]
    reference_context: dict[str, Any] | None = None
    people_context: list[dict[str, str]] | None = None
    pre_send_check_feedback: str | None = None
    agent_skill_context: dict[str, Any] | None = None
    comparison_scope: str = "full"
    recent_interactions: list[dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class AutonomousStepContext:
    run: dict[str, Any]
    current_input: CurrentInput
    recent_turns: list[dict[str, Any]]
    time_context: dict[str, Any]
    foreground_world_state: list[dict[str, Any]] | None
    activity_context: dict[str, Any] | None
    ongoing_action_summary: dict[str, Any] | None
    capability_decision_view: list[dict[str, Any]] | None
    last_result_context: dict[str, Any] | None
    observation_context: dict[str, Any] | None = None
    people_context: list[dict[str, str]] | None = None
    pre_send_check_feedback: str | None = None
    completion_review_feedback: str | None = None
    agent_skill_context: dict[str, Any] | None = None

    def to_prompt_payload(self) -> dict[str, Any]:
        payload = {
            "run": self.run,
            "current_input": self.current_input.to_prompt_payload(),
            "recent_turns": self.recent_turns,
            "time_context": self.time_context,
            "foreground_world_state": self.foreground_world_state,
            "activity_context": self.activity_context,
            "ongoing_action_summary": self.ongoing_action_summary,
            "capability_decision_view": self.capability_decision_view,
            "last_result_context": self.last_result_context,
            "observation_context": self.observation_context,
            "people_context": self.people_context or [],
        }
        if self.pre_send_check_feedback is not None:
            payload["pre_send_check_feedback"] = self.pre_send_check_feedback
        if self.completion_review_feedback is not None:
            payload["completion_review_feedback"] = self.completion_review_feedback
        return payload


@dataclass(frozen=True, slots=True)
class SpeechContext:
    input_text: str
    current_input: CurrentInput
    recent_turns: list[dict[str, Any]]
    time_context: dict[str, Any]
    affect_context: dict[str, Any]
    drive_state_summary: list[dict[str, Any]] | None
    foreground_world_state: list[dict[str, Any]] | None
    activity_context: dict[str, Any] | None
    ongoing_action_summary: dict[str, Any] | None
    initiative_context: InitiativeContext | None
    visual_observation_context: dict[str, Any] | None
    self_state_context: dict[str, Any] | None
    relationship_context: dict[str, Any] | None
    prediction_error_context: dict[str, Any] | None
    workspace_context: dict[str, Any] | None
    recall_hint: dict[str, Any]
    recall_pack: dict[str, Any]
    decision: dict[str, Any]
    reference_context: dict[str, Any] | None = None
    people_context: list[dict[str, str]] | None = None
    agent_skill_context: dict[str, Any] | None = None
