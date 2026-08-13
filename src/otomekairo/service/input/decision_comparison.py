from __future__ import annotations

from dataclasses import replace
from typing import Any

from otomekairo.llm.contexts import CurrentInput, DecisionContext, InitiativeContext
from otomekairo.llm.contracts import (
    _initiative_has_self_activity,
    _workspace_has_self_activity,
)
from otomekairo.service.common import debug_log


SELF_ACTIVITY_ADVANCE_KINDS = frozenset({"capability_request", "autonomous_run", "pending_intent"})
SELF_ACTIVITY_EXECUTE_KINDS = frozenset({"capability_request", "autonomous_run"})


class ServiceInputDecisionComparisonMixin:
    def _should_compare_self_activity_separately(
        self,
        *,
        trigger_kind: str,
        workspace_context: dict[str, Any] | None,
        initiative_context: InitiativeContext | None,
    ) -> bool:
        if trigger_kind not in {"wake", "background_thinking"}:
            return False
        return (
            _workspace_has_self_activity(workspace_context)
            or _initiative_has_self_activity(initiative_context)
        )

    def _run_separated_activity_decisions(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        cycle_label = str(kwargs.get("cycle_label") or "")
        self_context = self._build_self_activity_decision_context(**kwargs)
        debug_log("Pipeline", f"{cycle_label} self_activity decision start", level="DEBUG")
        self_decision = self.llm.generate_decision(
            model_config=kwargs["model_config"],
            persona_context=kwargs["persona_context"],
            context=self_context,
        )
        self._validate_mcp_session_decision(
            decision=self_decision,
            capability_decision_view=kwargs.get("capability_decision_view"),
            trigger_kind=kwargs["trigger_kind"],
        )
        debug_log(
            "Pipeline",
            (
                f"{cycle_label} self_activity decision done kind={self_decision['kind']} "
                f"reason={self._clamp(self_decision['reason_summary'])}"
            ),
        )
        outward_context = self._build_outward_speech_decision_context(**kwargs)
        debug_log("Pipeline", f"{cycle_label} outward_speech decision start", level="DEBUG")
        outward_decision = self.llm.generate_decision(
            model_config=kwargs["model_config"],
            persona_context=kwargs["persona_context"],
            context=outward_context,
        )
        debug_log(
            "Pipeline",
            (
                f"{cycle_label} outward_speech decision done kind={outward_decision['kind']} "
                f"reason={self._clamp(outward_decision['reason_summary'])}"
            ),
        )
        composed = self._compose_separated_decisions(
            self_decision=self_decision,
            outward_decision=outward_decision,
        )
        debug_log(
            "Pipeline",
            (
                f"{cycle_label} decision done kinds={self._decision_kind_log(composed)} "
                f"reason={self._clamp(composed['reason_summary'])}"
            ),
        )
        return composed

    def _build_self_activity_decision_context(self, **kwargs: Any) -> DecisionContext:
        current_input = kwargs["current_input"]
        isolated_input = CurrentInput(
            sender_kind="system",
            sender_ref=None,
            source_kind=current_input.source_kind,
            response_target_refs=(),
            interaction_context=None,
            text="自己評価。しばらく関わっていない気にかけている場がある。今その場へ関わるかを見る。",
        )
        return self._build_decision_context(
            input_text=isolated_input.text,
            current_input=isolated_input,
            trigger_kind=kwargs["trigger_kind"],
            recent_turns=[],
            time_context=kwargs["time_context"],
            affect_context=kwargs["affect_context"],
            drive_state_summary=kwargs["drive_state_summary"],
            foreground_world_state=self._self_activity_world_state(kwargs.get("foreground_world_state")),
            activity_context=None,
            ongoing_action_summary=kwargs.get("ongoing_action_summary"),
            autonomous_run_summaries=kwargs.get("autonomous_run_summaries"),
            capability_decision_view=self._self_activity_capability_view(
                kwargs.get("capability_decision_view")
            ),
            agent_skill_context=kwargs.get("agent_skill_context"),
            initiative_context=self._self_activity_initiative_context(kwargs.get("initiative_context")),
            capability_result_context=None,
            visual_observation_context=None,
            self_state_context=kwargs.get("self_state_context"),
            people_context=[],
            relationship_context=None,
            prediction_error_context=None,
            default_mode_context=None,
            workspace_context=self._self_activity_workspace(
                kwargs.get("workspace_context"),
                current_input_text=isolated_input.text,
            ),
            recall_hint=kwargs.get("recall_hint") or {},
            recall_pack=kwargs.get("recall_pack") or {},
            reference_context=None,
            pre_send_check_feedback=kwargs.get("pre_send_check_feedback"),
            comparison_scope="self_activity",
        )

    def _self_activity_world_state(
        self,
        foreground_world_state: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]] | None:
        if not isinstance(foreground_world_state, list):
            return None
        kept = [
            item
            for item in foreground_world_state
            if isinstance(item, dict) and item.get("state_type") != "visual_context"
        ]
        return kept or None

    def _self_activity_capability_view(
        self,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]] | None:
        if not isinstance(capability_decision_view, list):
            return None
        return [
            item
            for item in capability_decision_view
            if isinstance(item, dict) and item.get("id") not in {"vision.capture", "camera.ptz"}
        ]

    def _self_activity_workspace(
        self,
        workspace_context: dict[str, Any] | None,
        *,
        current_input_text: str | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(workspace_context, dict):
            return None
        candidates = workspace_context.get("workspace_candidates")
        if not isinstance(candidates, list):
            return workspace_context
        kept = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            kind = candidate.get("kind")
            factor_ref = str(candidate.get("factor_ref") or "")
            if kind == "current_input":
                rewritten = dict(candidate)
                if isinstance(current_input_text, str) and current_input_text.strip():
                    rewritten["summary_text"] = current_input_text
                kept.append(rewritten)
                continue
            if kind in {"standing_concern", "ongoing_action", "autonomous_run", "drive_state"}:
                kept.append(candidate)
                continue
            if kind == "capability" and factor_ref not in {
                "capability:vision.capture",
                "capability:camera.ptz",
            }:
                kept.append(candidate)
                continue
            if kind == "initiative_candidate" and factor_ref == "initiative:autonomous":
                kept.append(candidate)
        return {
            **workspace_context,
            "workspace_candidates": kept,
        }

    def _build_outward_speech_decision_context(self, **kwargs: Any) -> DecisionContext:
        current_input = kwargs["current_input"]
        isolated_input = CurrentInput(
            sender_kind="system",
            sender_ref=None,
            source_kind=current_input.source_kind,
            response_target_refs=(),
            interaction_context=current_input.interaction_context,
            text="自己評価。いま短い見方として外へ出るかを見る。",
        )
        return self._build_decision_context(
            input_text=isolated_input.text,
            current_input=isolated_input,
            trigger_kind=kwargs["trigger_kind"],
            recent_turns=kwargs["recent_turns"],
            time_context=kwargs["time_context"],
            affect_context=kwargs["affect_context"],
            drive_state_summary=kwargs["drive_state_summary"],
            foreground_world_state=kwargs["foreground_world_state"],
            activity_context=kwargs["activity_context"],
            ongoing_action_summary=None,
            autonomous_run_summaries=None,
            capability_decision_view=None,
            agent_skill_context=kwargs.get("agent_skill_context"),
            initiative_context=self._outward_speech_initiative_context(kwargs.get("initiative_context")),
            capability_result_context=None,
            visual_observation_context=kwargs.get("visual_observation_context"),
            self_state_context=kwargs.get("self_state_context"),
            people_context=kwargs.get("people_context"),
            relationship_context=kwargs.get("relationship_context"),
            prediction_error_context=kwargs.get("prediction_error_context"),
            default_mode_context=kwargs.get("default_mode_context"),
            workspace_context=self._outward_speech_workspace(
                kwargs.get("workspace_context"),
                current_input_text=isolated_input.text,
            ),
            recall_hint=kwargs.get("recall_hint") or {},
            recall_pack=kwargs.get("recall_pack") or {},
            reference_context=kwargs.get("reference_context"),
            pre_send_check_feedback=kwargs.get("pre_send_check_feedback"),
            comparison_scope="outward_speech",
        )

    def _outward_speech_workspace(
        self,
        workspace_context: dict[str, Any] | None,
        *,
        current_input_text: str | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(workspace_context, dict):
            return None
        candidates = workspace_context.get("workspace_candidates")
        if not isinstance(candidates, list):
            return workspace_context
        dropped_kinds = {
            "standing_concern",
            "ongoing_action",
            "autonomous_run",
            "capability",
            "capability_result",
        }
        dropped_initiative_refs = {
            "initiative:autonomous",
            "initiative:ongoing_action",
        }
        kept = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            kind = candidate.get("kind")
            factor_ref = str(candidate.get("factor_ref") or "")
            if kind in dropped_kinds:
                continue
            if kind == "initiative_candidate" and factor_ref in dropped_initiative_refs:
                continue
            if kind == "current_input":
                rewritten = dict(candidate)
                if isinstance(current_input_text, str) and current_input_text.strip():
                    rewritten["summary_text"] = current_input_text
                kept.append(rewritten)
                continue
            kept.append(candidate)
        return {
            **workspace_context,
            "workspace_candidates": kept,
        }

    def _outward_speech_initiative_context(
        self,
        initiative_context: InitiativeContext | None,
    ) -> InitiativeContext | None:
        if initiative_context is None:
            return None
        families = []
        selected_family = None
        for family in initiative_context.candidate_families:
            if family.family in {"autonomous", "ongoing_action"}:
                families.append(replace(family, available=False, selected=False))
                continue
            families.append(family)
            if family.selected is True and family.available is True:
                selected_family = family.family
        return replace(
            initiative_context,
            opportunity_summary="外界の観測と直近文脈があり、短い見方として外へ出るかを見る。",
            ongoing_action_summary=None,
            capability_summary={},
            candidate_families=families,
            selected_candidate_family=selected_family,
        )

    def _self_activity_initiative_context(
        self,
        initiative_context: InitiativeContext | None,
    ) -> InitiativeContext | None:
        if initiative_context is None:
            return None
        foreground = dict(initiative_context.foreground_signal_summary or {})
        foreground.pop("visual_observations", None)
        families = []
        selected_family = None
        for family in initiative_context.candidate_families:
            if family.family == "autonomous" and family.available is True:
                families.append(replace(family, selected=True))
                selected_family = "autonomous"
            else:
                families.append(replace(family, selected=False))
        world_state_summary = [
            item
            for item in initiative_context.world_state_summary
            if isinstance(item, dict) and item.get("state_type") != "visual_context"
        ]
        return replace(
            initiative_context,
            opportunity_summary="気にかけている場がしばらく前景に出ていない。",
            initiative_entry_summary=None,
            foreground_signal_summary=foreground,
            activity_context=None,
            recent_turn_summary=[],
            world_state_summary=world_state_summary,
            candidate_families=families,
            selected_candidate_family=selected_family,
            suppression_summary={},
            speech_timing_summary="",
        )

    def _compose_separated_decisions(
        self,
        *,
        self_decision: dict[str, Any],
        outward_decision: dict[str, Any],
    ) -> dict[str, Any]:
        self_kind = str(self_decision.get("kind") or "")
        outward_kind = str(outward_decision.get("kind") or "")
        self_reason = str(self_decision.get("reason_summary") or "").strip() or "自身の活動を見送る。"
        outward_reason = str(outward_decision.get("reason_summary") or "").strip() or "外向き伝達を見送る。"
        self_stance = self._decision_target_stance(
            self_decision,
            target="self_activity",
            default_stance="advance" if self_kind in SELF_ACTIVITY_EXECUTE_KINDS else "hold",
            default_reason=self_reason,
        )
        outward_stance = self._decision_target_stance(
            outward_decision,
            target="outward_speech",
            default_stance="advance" if outward_kind == "speech" else "hold",
            default_reason=outward_reason,
        )
        return {
            "reason_summary": self._join_separated_reason_summaries(
                outward_reason=outward_reason,
                self_reason=self_reason,
            ),
            "requires_confirmation": False,
            "target_stances": [outward_stance, self_stance],
            "separated_comparisons": {
                "self_activity": self_decision,
                "outward_speech": outward_decision,
            },
        }

    def _join_separated_reason_summaries(self, *, outward_reason: str, self_reason: str) -> str:
        return f"外向き伝達: {outward_reason} 自身の活動: {self_reason}"

    def _decision_target_stance(
        self,
        decision: dict[str, Any],
        *,
        target: str,
        default_stance: str,
        default_reason: str,
    ) -> dict[str, str]:
        for item in decision.get("target_stances") or []:
            if isinstance(item, dict) and item.get("target") == target:
                stance = item.get("stance")
                reason = item.get("reason_summary")
                return {
                    "target": target,
                    "stance": stance if stance in {"advance", "hold"} else default_stance,
                    "reason_summary": (
                        str(reason).strip() if isinstance(reason, str) and reason.strip() else default_reason
                    ),
                }
        return {
            "target": target,
            "stance": default_stance,
            "reason_summary": default_reason,
        }

    def _separated_comparison(
        self,
        decision: dict[str, Any],
        target: str,
    ) -> dict[str, Any] | None:
        separated = decision.get("separated_comparisons")
        if not isinstance(separated, dict):
            return None
        payload = separated.get(target)
        return payload if isinstance(payload, dict) else None

    def _execution_self_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        return self._separated_comparison(decision, "self_activity") or decision

    def _execution_outward_decision(self, decision: dict[str, Any]) -> dict[str, Any]:
        return self._separated_comparison(decision, "outward_speech") or decision

    def _decision_is_separated(self, decision: dict[str, Any]) -> bool:
        return self._separated_comparison(decision, "self_activity") is not None

    def _decision_has_outward_speech(self, decision: dict[str, Any]) -> bool:
        return self._execution_outward_decision(decision).get("kind") == "speech"

    def _decision_has_self_activity_result(self, decision: dict[str, Any]) -> bool:
        return self._execution_self_decision(decision).get("kind") in SELF_ACTIVITY_ADVANCE_KINDS

    def _decision_has_any_kind(self, decision: dict[str, Any], kinds: set[str] | frozenset[str]) -> bool:
        if self._decision_is_separated(decision):
            return (
                self._execution_self_decision(decision).get("kind") in kinds
                or self._execution_outward_decision(decision).get("kind") in kinds
            )
        return decision.get("kind") in kinds

    def _decision_kind_log(self, decision: dict[str, Any]) -> str:
        if self._decision_is_separated(decision):
            self_kind = self._execution_self_decision(decision).get("kind") or "-"
            outward_kind = self._execution_outward_decision(decision).get("kind") or "-"
            return f"self={self_kind} outward={outward_kind}"
        return str(decision.get("kind") or "-")

    def _apply_outward_speech_suppression(
        self,
        decision: dict[str, Any],
        *,
        reason_code: str,
        reason_summary: str,
    ) -> None:
        if self._decision_is_separated(decision):
            outward = dict(self._execution_outward_decision(decision))
            outward.update(
                {
                    "kind": "noop",
                    "reason_code": reason_code,
                    "reason_summary": reason_summary,
                    "requires_confirmation": False,
                    "pending_intent": None,
                    "capability_request": None,
                    "autonomous_run": None,
                }
            )
            self_decision = self._execution_self_decision(decision)
            self_reason = str(self_decision.get("reason_summary") or "").strip() or "自身の活動を見送る。"
            decision["separated_comparisons"] = {
                "self_activity": self_decision,
                "outward_speech": outward,
            }
            decision["target_stances"] = self._hold_outward_speech_stance(
                decision,
                reason_summary=reason_summary,
            )
            decision["reason_summary"] = self._join_separated_reason_summaries(
                outward_reason=reason_summary,
                self_reason=self_reason,
            )
            for key in (
                "kind",
                "reason_code",
                "pending_intent",
                "capability_request",
                "autonomous_run",
                "foreground_selection",
            ):
                decision.pop(key, None)
            return
        original_reason = str(decision.get("reason_summary") or "").strip()
        combined_reason = f"{reason_summary} 元判断: {original_reason}" if original_reason else reason_summary
        decision.update(
            {
                "kind": "noop",
                "reason_code": reason_code,
                "reason_summary": combined_reason,
                "requires_confirmation": False,
                "pending_intent": None,
                "capability_request": None,
                "autonomous_run": None,
                "target_stances": self._hold_outward_speech_stance(
                    decision,
                    reason_summary=reason_summary,
                ),
            }
        )

    def _hold_outward_speech_stance(
        self,
        decision: dict[str, Any],
        *,
        reason_summary: str,
    ) -> list[dict[str, str]]:
        updated: list[dict[str, str]] = []
        seen_outward = False
        for item in decision.get("target_stances") or []:
            if not isinstance(item, dict):
                continue
            target = item.get("target")
            if target == "outward_speech":
                seen_outward = True
                updated.append(
                    {
                        "target": "outward_speech",
                        "stance": "hold",
                        "reason_summary": reason_summary,
                    }
                )
                continue
            if target == "self_activity":
                stance = item.get("stance") if item.get("stance") in {"advance", "hold"} else "hold"
                existing_reason = item.get("reason_summary")
                updated.append(
                    {
                        "target": "self_activity",
                        "stance": stance,
                        "reason_summary": (
                            str(existing_reason).strip()
                            if isinstance(existing_reason, str) and existing_reason.strip()
                            else reason_summary
                        ),
                    }
                )
        if not seen_outward:
            updated.insert(
                0,
                {
                    "target": "outward_speech",
                    "stance": "hold",
                    "reason_summary": reason_summary,
                },
            )
        return updated

    def _drop_self_activity_after_pre_send_withhold(
        self,
        decision: dict[str, Any],
        *,
        attempts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        reason_summary = "送信前チェックの結果、外部送信を行わず終了した。"
        outward_decision = self._execution_outward_decision(decision)
        self_hold = {
            "kind": "noop",
            "reason_code": "pre_send_check_withheld",
            "reason_summary": reason_summary,
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {
                "primary_factor_ref": None,
                "supporting_factor_refs": [],
                "suppressed_factors": [],
                "summary_text": reason_summary,
            },
            "target_stances": [
                {
                    "target": "self_activity",
                    "stance": "hold",
                    "reason_summary": reason_summary,
                }
            ],
        }
        if self._separated_comparison(decision, "outward_speech") is not None:
            updated = self._compose_separated_decisions(
                self_decision=self_hold,
                outward_decision=outward_decision,
            )
        else:
            updated = dict(decision)
            updated.update(
                {
                    "kind": "noop",
                    "reason_code": "pre_send_check_withheld",
                    "reason_summary": reason_summary,
                    "requires_confirmation": False,
                    "pending_intent": None,
                    "capability_request": None,
                    "autonomous_run": None,
                }
            )
        updated["pre_send_check"] = {
            "result_status": "withheld",
            "attempts": attempts,
        }
        return updated
