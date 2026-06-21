from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


PERSONA_PROMPT_EXCERPT_LIMIT = 240
PERSONA_REFERENCE_STYLE_USER_NATURAL_REFERENCE = "user_natural_reference"
PERSONA_SCHEMA_USER_REFERENCE = "user"


PERSONA_CONTEXT_USE_POLICIES = {
    "decision_generation": "行動選択、見送り、能力実行、保留、継続目的の基底として使う。記憶、観測、候補集合を上書きしない。",
    "autonomous_step_generation": "autonomous_run の次 step と継続境界の基底として使う。run 目的、能力可否、観測事実を上書きしない。",
    "expression_generation": "外向き本文の立ち位置、距離感、言い回し、注目点に使う。判断結果と根拠文脈の外を補わない。",
    "pending_intent_selection": "今前へ出る自然さ、関心の強さ、距離感の判断に使う。候補外の意図を作らない。",
    "initiative_entry_check": "外向き自律判断へ進む自然さ、関心の強さ、距離感の判断に使う。観測事実を追加しない。",
    "input_interpretation": "入力内で何を重く見るかの補助に使う。ユーザー発話、時刻参照、根拠分類を上書きしない。",
    "recall_hint": "想起焦点の重みづけの補助に使う。ユーザー発話や明示された参照を人格で補完しない。",
    "answer_contract": "回答に必要な根拠種別の重みづけ補助に使う。正確性要求や境界指定を人格で変えない。",
    "recall_pack_selection": "想起候補の優先順位の補助に使う。候補集合、候補本文、conflict を上書きしない。",
    "event_evidence_generation": "証拠要約の注目点の補助に使う。source pack 外の出来事や言い換えを足さない。",
    "memory_interpretation": "self / relationship の反応や関係温度の解釈補助に使う。ユーザー事実を人格で補完しない。",
    "memory_correction_reconciliation": "訂正らしさの意味判断の補助に使う。対象候補外の revision を作らない。",
    "memory_reflection_summary": "言い回しと注目点の補助に使う。episodes と memory_units を根拠の中心にする。",
    "world_state": "観測事実の優先順位と要約粒度の補助に使う。見えていない短期状態を足さない。",
    "activity_state": "活動推定の注目点と要約粒度の補助に使う。観測外の活動を足さない。",
    "visual_observation": "画像内で判断に効く部分の優先順位と要約粒度の補助に使う。見えていないものを足さない。",
    "drive_state": "drive の種類、根拠記憶、scope support と合わせた整合度評価に使う。人格本文を状態へ複写しない。",
}


@dataclass(frozen=True, slots=True)
class PersonaContext:
    display_name: str
    initiative_baseline: dict[str, Any]
    reference_style: dict[str, str]
    persona_prompt_text: str
    expression_addon: str | None
    use_policy: str

    def to_prompt_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "initiative_baseline": self.initiative_baseline,
            "reference_style": {
                "schema_user_reference": PERSONA_SCHEMA_USER_REFERENCE,
                "user_natural_reference": self.reference_style[PERSONA_REFERENCE_STYLE_USER_NATURAL_REFERENCE],
            },
            "persona_prompt_text": self.persona_prompt_text,
            "use_policy": self.use_policy,
        }
        if isinstance(self.expression_addon, str) and self.expression_addon.strip():
            payload["expression_addon"] = self.expression_addon
        return payload

    def to_summary_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "initiative_baseline": self.initiative_baseline,
            "reference_style": {
                "user_natural_reference": self.reference_style[PERSONA_REFERENCE_STYLE_USER_NATURAL_REFERENCE],
            },
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
    use_policy = PERSONA_CONTEXT_USE_POLICIES.get(normalized_role)
    if use_policy is None:
        raise ValueError(f"unsupported persona_context role: {role}")
    display_name = _persona_text(persona.get("display_name")) or "OtomeKairo"
    initiative_level = _persona_text(persona.get("initiative_baseline")) or "medium"
    reference_style = _persona_reference_style(persona.get("reference_style"))
    persona_prompt_text = _persona_text(persona.get("persona_prompt")) or ""
    expression_addon = _persona_text(persona.get("expression_addon")) if include_expression else None
    return PersonaContext(
        display_name=display_name,
        initiative_baseline={
            "level": initiative_level,
            "summary_text": persona_initiative_baseline_summary(initiative_level),
        },
        reference_style=reference_style,
        persona_prompt_text=persona_prompt_text,
        expression_addon=expression_addon,
        use_policy=use_policy,
    )


def build_persona_context_summary(persona: dict[str, Any]) -> dict[str, Any]:
    return build_persona_context(persona, role="decision_generation").to_summary_payload()


def persona_initiative_baseline_summary(level: str) -> str:
    if level == "low":
        return "自発介入は控えめ寄りで、前景理由が弱ければ見送る。"
    if level == "high":
        return "自発介入は強めで、前景理由が揃えば一歩前へ出る。"
    return "自発介入は中庸で、具体的な前景変化があれば短く前へ出る。"


def _persona_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _persona_reference_style(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("persona.reference_style must be an object.")
    user_natural_reference = _persona_text(value.get(PERSONA_REFERENCE_STYLE_USER_NATURAL_REFERENCE))
    if user_natural_reference is None:
        raise ValueError("persona.reference_style.user_natural_reference must be a non-empty string.")
    return {
        PERSONA_REFERENCE_STYLE_USER_NATURAL_REFERENCE: user_natural_reference,
    }


@dataclass(frozen=True, slots=True)
class CurrentInput:
    sender: str
    source_kind: str
    response_target: str
    text: str

    def to_prompt_payload(self) -> dict[str, Any]:
        return {
            "sender": self.sender,
            "source_kind": self.source_kind,
            "response_target": self.response_target,
            "text": self.text,
        }


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
            "intervention_state": self.intervention_state,
            "suppression_summary": self.suppression_summary,
            "intervention_risk_summary": self.intervention_risk_summary,
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

    def to_prompt_payload(self) -> dict[str, Any]:
        return {
            "run": self.run,
            "current_input": self.current_input.to_prompt_payload(),
            "recent_turns": self.recent_turns,
            "time_context": self.time_context,
            "foreground_world_state": self.foreground_world_state,
            "activity_context": self.activity_context,
            "ongoing_action_summary": self.ongoing_action_summary,
            "capability_decision_view": self.capability_decision_view,
            "last_result_context": self.last_result_context,
        }


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
