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
SELF_ACTIVITY_EXCLUDED_CAPABILITY_IDS = frozenset({"vision.capture", "camera.ptz"})
SELF_ACTIVITY_INPUT_TEXT = "自己評価。今、自身の活動へ関わるかを見る。"
SELF_ACTIVITY_STANDING_CONCERN_INPUT_TEXT = (
    "自己評価。しばらく関わっていない気にかけていることがある。"
    "今それに関わるか、関わるなら見る、返す、自分から書くのどれが今の向きとして自然かを見る。"
)
SELF_ACTIVITY_BOUNDARY_RECALL_HINT = {
    "primary_recall_focus": "self",
    "secondary_recall_focuses": ["topic", "commitment"],
    "confidence": 0.0,
    "time_reference": "recent",
    "focus_scopes": ["self"],
    "mentioned_entities": [],
    "mentioned_topics": [],
    "risk_flags": [],
}
SELF_ACTIVITY_ORIENTATION_KINDS = ("standing_concern", "ongoing_action", "autonomous_run")


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
        self_decision = self.llm.generate_decision(
            model_config=kwargs["model_config"],
            persona_context=kwargs["persona_context"],
            context=self_context,
        )
        if self_decision.get("kind") == "capability_request":
            self_decision, selected_skills = self._hydrate_self_activity_capability_request(
                self_decision=self_decision,
                self_context=self_context,
                **kwargs,
            )
            skill_slot = kwargs.get("skill_context_slot")
            if isinstance(skill_slot, dict):
                skill_slot["agent_skill_context"] = selected_skills
                skill_slot["self_activity_agent_skill_context"] = selected_skills
        outward_context = self._build_outward_speech_decision_context(**kwargs)
        outward_decision = self.llm.generate_decision(
            model_config=kwargs["model_config"],
            persona_context=kwargs["persona_context"],
            context=outward_context,
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

    def _self_activity_current_input(
        self,
        current_input: CurrentInput,
        workspace_context: dict[str, Any] | None,
    ) -> CurrentInput:
        has_standing_concern = bool(self._workspace_standing_concerns(workspace_context))
        return CurrentInput(
            sender_kind="system",
            sender_ref=None,
            source_kind=current_input.source_kind,
            response_target_refs=(),
            interaction_context=None,
            text=(
                SELF_ACTIVITY_STANDING_CONCERN_INPUT_TEXT
                if has_standing_concern
                else SELF_ACTIVITY_INPUT_TEXT
            ),
        )

    def _build_self_activity_decision_context(self, **kwargs: Any) -> DecisionContext:
        current_input = kwargs["current_input"]
        source_workspace = kwargs.get("workspace_context")
        isolated_input = kwargs.get("self_activity_current_input")
        if not isinstance(isolated_input, CurrentInput):
            isolated_input = self._self_activity_current_input(current_input, source_workspace)
        recall_hint = kwargs.get("self_activity_recall_hint")
        if not isinstance(recall_hint, dict):
            recall_hint = kwargs.get("recall_hint") or {}
        recall_pack = kwargs.get("self_activity_recall_pack")
        if not isinstance(recall_pack, dict):
            recall_pack = kwargs.get("recall_pack") or {}
        initiative_context = self._self_activity_initiative_context(
            kwargs.get("initiative_context"),
            workspace_context=source_workspace,
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
            agent_skill_context=None,
            initiative_context=initiative_context,
            capability_result_context=None,
            visual_observation_context=None,
            self_state_context=kwargs.get("self_state_context"),
            people_context=[],
            relationship_context=None,
            prediction_error_context=None,
            default_mode_context=None,
            workspace_context=self._self_activity_workspace(
                source_workspace,
                current_input_text=isolated_input.text,
                initiative_context=initiative_context,
            ),
            recall_hint=recall_hint,
            recall_pack=recall_pack,
            reference_context=None,
            pre_send_check_feedback=kwargs.get("pre_send_check_feedback"),
            comparison_scope="self_activity",
            materialize_capability_input=False,
        )

    def _hydrate_self_activity_capability_request(
        self,
        *,
        self_decision: dict[str, Any],
        self_context: DecisionContext,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        isolated_input = self_context.current_input
        selected_skills = self._build_agent_skill_context(
            model_config=kwargs["model_config"],
            current_input=isolated_input,
            trigger_kind=kwargs["trigger_kind"],
            capability_decision_view=self._self_activity_capability_view(
                kwargs.get("capability_decision_view")
            ),
            recent_turns=[],
            work_log=[],
            orientation_context=kwargs.get("agent_skill_orientation_context")
            or {"standing_concerns": []},
            prior_activation=kwargs.get("agent_skill_prior_activation"),
            origin_source_kind=kwargs.get("agent_skill_origin_source_kind"),
        )
        request = self_decision.get("capability_request")
        if not isinstance(request, dict) or "target_ref" not in request:
            return self_decision, selected_skills
        hydrated_context = replace(self_context, agent_skill_context=selected_skills)
        hydrated = self.llm.materialize_decision_capability_input(
            payload=self_decision,
            context=hydrated_context,
            model_config=kwargs["model_config"],
            persona_context=kwargs["persona_context"],
        )
        return hydrated, selected_skills

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
            if isinstance(item, dict) and item.get("id") not in SELF_ACTIVITY_EXCLUDED_CAPABILITY_IDS
        ]

    def _self_activity_workspace(
        self,
        workspace_context: dict[str, Any] | None,
        *,
        current_input_text: str | None = None,
        initiative_context: InitiativeContext | None = None,
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
            if kind in {
                "standing_concern",
                "ongoing_action",
                "autonomous_run",
                "drive_state",
                "affect",
            }:
                kept.append(candidate)
                continue
            if kind == "capability" and factor_ref not in {
                f"capability:{capability_id}"
                for capability_id in SELF_ACTIVITY_EXCLUDED_CAPABILITY_IDS
            }:
                kept.append(candidate)
                continue
            if kind == "initiative_candidate" and factor_ref == "initiative:autonomous":
                kept.append(
                    self._self_activity_initiative_workspace_candidate(
                        candidate,
                        initiative_context=initiative_context,
                    )
                )
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
            agent_skill_context=None,
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

    def _self_activity_initiative_workspace_candidate(
        self,
        candidate: dict[str, Any],
        *,
        initiative_context: InitiativeContext | None,
    ) -> dict[str, Any]:
        rewritten = dict(candidate)
        family = None
        if initiative_context is not None:
            for item in initiative_context.candidate_families:
                if item.family == "autonomous":
                    family = item
                    break
        if family is None:
            return rewritten
        rewritten["summary_text"] = (
            family.reason_summary
            if family.available is True and family.reason_summary
            else family.blocking_reason_summary or rewritten.get("summary_text")
        )
        metadata = dict(rewritten.get("metadata") or {})
        metadata["available"] = family.available
        metadata["selected"] = family.selected
        metadata["preferred_result_kind"] = family.preferred_result_kind
        metadata["preferred_capability_id"] = family.preferred_capability_id
        rewritten["metadata"] = metadata
        return rewritten

    def _workspace_standing_concerns(
        self,
        workspace_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not isinstance(workspace_context, dict):
            return []
        candidates = workspace_context.get("workspace_candidates")
        if not isinstance(candidates, list):
            return []
        return [
            candidate
            for candidate in candidates
            if isinstance(candidate, dict) and candidate.get("kind") == "standing_concern"
        ]

    def _self_activity_capability_summary(
        self,
        capability_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(capability_summary, dict) or not capability_summary:
            return {}
        available_ids = [
            capability_id
            for capability_id in capability_summary.get("available_ids") or []
            if capability_id not in SELF_ACTIVITY_EXCLUDED_CAPABILITY_IDS
        ]
        available_items = [
            item
            for item in capability_summary.get("available_items") or []
            if isinstance(item, dict) and item.get("id") not in SELF_ACTIVITY_EXCLUDED_CAPABILITY_IDS
        ]
        unavailable_items = [
            item
            for item in capability_summary.get("unavailable_items") or []
            if isinstance(item, dict) and item.get("id") not in SELF_ACTIVITY_EXCLUDED_CAPABILITY_IDS
        ]
        return {
            "available_count": len(available_ids),
            "available_ids": available_ids,
            "available_items": available_items,
            "unavailable_count": len(unavailable_items),
            "unavailable_items": unavailable_items,
            "vision_sources": [],
        }

    def _self_activity_foreground_signal_summary(
        self,
        foreground_signal_summary: dict[str, Any] | None,
        *,
        world_state_summary: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = dict(foreground_signal_summary or {})
        payload.pop("visual_observations", None)
        if not world_state_summary:
            payload["foreground_thinness"] = "thin"
            payload["reason_summary"] = "前景 world_state はまだ薄い。"
            payload["world_state_count"] = 0
            payload.pop("state_types", None)
            return payload
        state_types = [
            state_type
            for state_type in payload.get("state_types") or []
            if state_type != "visual_context"
        ]
        if state_types:
            payload["state_types"] = state_types[:12]
        else:
            payload.pop("state_types", None)
        payload["world_state_count"] = len(world_state_summary)
        return payload

    def _self_activity_initiative_context(
        self,
        initiative_context: InitiativeContext | None,
        *,
        workspace_context: dict[str, Any] | None = None,
    ) -> InitiativeContext | None:
        if initiative_context is None:
            return None
        world_state_summary = [
            item
            for item in initiative_context.world_state_summary
            if isinstance(item, dict) and item.get("state_type") != "visual_context"
        ]
        due_standing_concerns = self._workspace_standing_concerns(workspace_context)
        capability_summary = self._self_activity_capability_summary(
            initiative_context.capability_summary
        )
        drive_summaries = initiative_context.drive_summaries
        foreground_drives = self._initiative_foreground_drive_summaries(drive_summaries)
        orientation_available = bool(due_standing_concerns or foreground_drives)
        families = []
        selected_family = None
        for family in initiative_context.candidate_families:
            if family.family != "autonomous":
                families.append(replace(family, selected=False))
                continue
            available = family.available is True and orientation_available
            if available:
                strongest_drive = (
                    foreground_drives[0]
                    if foreground_drives
                    else drive_summaries[0] if drive_summaries else None
                )
                families.append(
                    replace(
                        family,
                        available=True,
                        selected=True,
                        reason_summary=self._initiative_autonomous_family_reason(
                            drive_summaries=drive_summaries,
                            foreground_drive_summaries=foreground_drives,
                            strongest_drive=strongest_drive,
                            world_state_summary=world_state_summary,
                            recent_turn_summary=[],
                            initiative_entry_summary=None,
                            visual_signals=[],
                            suppression_summary={},
                            capability_summary=capability_summary,
                            due_standing_concerns=due_standing_concerns,
                        ),
                        preferred_result_kind=None,
                        preferred_result_reason_summary=None,
                        preferred_capability_id=None,
                        preferred_capability_input=None,
                        blocking_reason_summary=None,
                    )
                )
                selected_family = "autonomous"
                continue
            families.append(
                replace(
                    family,
                    available=False,
                    selected=False,
                    preferred_result_kind=None,
                    preferred_result_reason_summary=None,
                    preferred_capability_id=None,
                    preferred_capability_input=None,
                    blocking_reason_summary="気にかけていることも前景の drive_state も無い。",
                )
            )
        return replace(
            initiative_context,
            opportunity_summary=(
                "気にかけていることがしばらく前景に出ていない。"
                if due_standing_concerns
                else "今、自身の活動へ関わるかを見る。"
            ),
            initiative_entry_summary=None,
            foreground_signal_summary=self._self_activity_foreground_signal_summary(
                initiative_context.foreground_signal_summary,
                world_state_summary=world_state_summary,
            ),
            activity_context=None,
            recent_turn_summary=[],
            world_state_summary=world_state_summary,
            capability_summary=capability_summary,
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
