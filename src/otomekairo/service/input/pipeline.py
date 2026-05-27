from __future__ import annotations

from typing import Any

from otomekairo.llm.contexts import CurrentInput, DecisionContext, InitiativeContext, ReplyContext
from otomekairo.service.common import debug_log


class ServiceInputPipelineMixin:
    def _run_input_pipeline(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        input_text: str,
        recent_turns: list[dict[str, Any]],
        cycle_id: str | None = None,
        trigger_kind: str = "user_message",
        client_context: dict[str, Any] | None = None,
        selected_candidate: dict[str, Any] | None = None,
        pending_intent_selection: dict[str, Any] | None = None,
        observation_summary: dict[str, Any] | None = None,
        capability_request_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cycle_label = self._debug_cycle_label(cycle_id)
        current_client_context = client_context or {}
        current_input = self._build_current_input(
            input_text=input_text,
            trigger_kind=trigger_kind,
            capability_request_summary=capability_request_summary,
        )
        augmented_query_text = self._pipeline_augmented_query_text(
            input_text=input_text,
            trigger_kind=trigger_kind,
            observation_summary=observation_summary,
        )
        visual_observation_context = self._build_visual_observation_decision_context(
            trigger_kind=trigger_kind,
            observation_summary=observation_summary,
        )
        debug_log(
            "Pipeline",
            (
                f"{cycle_label} start memory_set={self._short_identifier(state['selected_memory_set_id'])} "
                f"persona={state['selected_persona_id']} preset={state['selected_model_preset_id']} "
                f"input_chars={len(input_text)} recent_turns={len(recent_turns)}"
            ),
        )
        # モデル選択
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        recall_role = selected_preset["roles"]["input_interpretation"]
        decision_role = selected_preset["roles"]["decision_generation"]
        reply_role = selected_preset["roles"]["expression_generation"]
        persona = state["personas"][state["selected_persona_id"]]

        # 想起入力
        recall_inputs = self._build_pipeline_recall_inputs(
            state=state,
            started_at=started_at,
            input_text=input_text,
            current_input=current_input,
            recent_turns=recent_turns,
            augmented_query_text=augmented_query_text,
            visual_observation_context=visual_observation_context,
            recall_role=recall_role,
            cycle_label=cycle_label,
        )
        recall_hint = recall_inputs["recall_hint"]
        recall_pack = recall_inputs["recall_pack"]
        answer_contract = recall_inputs["answer_contract"]
        evidence_pack = recall_inputs["evidence_pack"]

        # 内部コンテキスト
        pipeline_contexts = self._build_pipeline_internal_contexts(
            state=state,
            persona=persona,
            started_at=started_at,
            input_text=input_text,
            recent_turns=recent_turns,
            trigger_kind=trigger_kind,
            client_context=current_client_context,
            cycle_id=cycle_id,
            selected_candidate=selected_candidate,
            pending_intent_selection=pending_intent_selection,
            observation_summary=observation_summary,
            capability_request_summary=capability_request_summary,
            recall_hint=recall_hint,
            cycle_label=cycle_label,
        )

        # decision生成
        decision = self._run_pipeline_decision(
            input_text=input_text,
            current_input=current_input,
            trigger_kind=trigger_kind,
            recent_turns=recent_turns,
            time_context=pipeline_contexts["time_context"],
            affect_context=pipeline_contexts["affect_context"],
            drive_state_summary=pipeline_contexts["drive_state_summary"],
            foreground_world_state=pipeline_contexts["foreground_world_state"],
            ongoing_action_summary=pipeline_contexts["ongoing_action_summary"],
            capability_decision_view=pipeline_contexts["capability_decision_view"],
            initiative_context=pipeline_contexts["initiative_context"],
            capability_result_context=pipeline_contexts["capability_result_context"],
            recall_hint=recall_hint,
            recall_pack=recall_pack,
            visual_observation_context=visual_observation_context,
            decision_role=decision_role,
            persona=persona,
            cycle_label=cycle_label,
        )

        # 出力
        output_result = self._run_pipeline_output(
            state=state,
            input_text=input_text,
            current_input=current_input,
            recent_turns=recent_turns,
            time_context=pipeline_contexts["time_context"],
            affect_context=pipeline_contexts["affect_context"],
            drive_state_summary=pipeline_contexts["drive_state_summary"],
            foreground_world_state=pipeline_contexts["foreground_world_state"],
            ongoing_action_summary=pipeline_contexts["ongoing_action_summary"],
            initiative_context=pipeline_contexts["initiative_context"],
            recall_hint=recall_hint,
            recall_pack=recall_pack,
            visual_observation_context=visual_observation_context,
            reply_role=reply_role,
            persona=persona,
            decision=decision,
            cycle_label=cycle_label,
        )

        # 結果
        debug_log("Pipeline", f"{cycle_label} done")
        return {
            "current_input": current_input.to_prompt_payload(),
            "augmented_query_text": augmented_query_text,
            "recall_hint": recall_hint,
            "recall_pack": recall_pack,
            "answer_contract": answer_contract,
            "evidence_pack": evidence_pack,
            "time_context": pipeline_contexts["time_context"],
            "affect_context": pipeline_contexts["affect_context"],
            "drive_state_summary": pipeline_contexts["drive_state_summary"],
            "foreground_world_state": pipeline_contexts["foreground_world_state"],
            "ongoing_action_summary": pipeline_contexts["ongoing_action_summary"],
            "capability_decision_view": pipeline_contexts["capability_decision_view"],
            "initiative_context": pipeline_contexts["initiative_context"],
            "capability_result_context": pipeline_contexts["capability_result_context"],
            "visual_observation_context": visual_observation_context,
            "world_state_trace": pipeline_contexts["world_state_trace"],
            "decision": decision,
            "reply_payload": output_result["reply_payload"],
            "capability_request_summary": output_result["capability_request_summary"],
            "ongoing_action_transition_summary": output_result["ongoing_action_transition_summary"],
        }

    def _build_current_input(
        self,
        *,
        input_text: str,
        trigger_kind: str,
        capability_request_summary: dict[str, Any] | None = None,
    ) -> CurrentInput:
        normalized_trigger = trigger_kind.strip() or "user_message"
        if normalized_trigger == "user_message":
            sender = "user"
            source_kind = "user_message"
            response_target = "user"
        elif normalized_trigger in {"wake", "background_wake"}:
            sender = "system"
            source_kind = normalized_trigger
            response_target = "none"
        elif normalized_trigger == "capability_result":
            sender = "capability"
            source_kind = "capability_result"
            response_target = self._capability_result_response_target(capability_request_summary)
        else:
            sender = "system"
            source_kind = normalized_trigger
            response_target = "none"
        return CurrentInput(
            sender=sender,
            source_kind=source_kind,
            response_target=response_target,
            text=input_text,
        )

    def _capability_result_response_target(self, capability_request_summary: dict[str, Any] | None) -> str:
        if isinstance(capability_request_summary, dict):
            source_current_input = capability_request_summary.get("source_current_input")
            if isinstance(source_current_input, dict) and source_current_input.get("response_target") == "user":
                return "user"
        return "none"

    def _build_pipeline_recall_inputs(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        input_text: str,
        current_input: CurrentInput,
        recent_turns: list[dict[str, Any]],
        augmented_query_text: str,
        visual_observation_context: dict[str, Any] | None,
        recall_role: dict[str, Any],
        cycle_label: str,
    ) -> dict[str, Any]:
        # 入口解釈
        recall_hint_recent_turns = self._recall_hint_recent_turns(recent_turns)
        debug_log("Pipeline", f"{cycle_label} input_interpretation start recent_turns={len(recall_hint_recent_turns)}")
        input_interpretation = self.llm.generate_input_interpretation(
            role_definition=recall_role,
            input_text=input_text,
            current_input=current_input,
            recent_turns=recall_hint_recent_turns,
            current_time=started_at,
            visual_observation_context=visual_observation_context,
        )
        recall_hint = input_interpretation["recall_hint"]
        answer_contract = input_interpretation["answer_contract"]
        debug_log(
            "Pipeline",
            (
                f"{cycle_label} input_interpretation done mode={recall_hint['interaction_mode']} "
                f"focus={recall_hint['primary_recall_focus']} confidence={recall_hint['confidence']} "
                f"contract={answer_contract.get('contract')}"
            ),
        )

        # recall_pack構築
        debug_log("Pipeline", f"{cycle_label} recall_pack start")
        recall_pack = self.recall.build_recall_pack(
            state=state,
            augmented_query_text=augmented_query_text,
            recall_hint=recall_hint,
        )
        recall_summary = self._summarize_recall_pack(recall_pack)
        debug_log(
            "Pipeline",
            (
                f"{cycle_label} recall_pack done candidates={recall_pack['candidate_count']} "
                f"selected_memory={len(recall_pack['selected_memory_ids'])} "
                f"selected_episode={len(recall_pack['selected_episode_ids'])} "
                f"sections={recall_summary}"
            ),
        )

        # 回答根拠解決
        debug_log("Pipeline", f"{cycle_label} evidence_resolution start contract={answer_contract.get('contract')}")
        evidence_resolution = self.evidence.build_evidence_resolution(
            memory_set_id=state["selected_memory_set_id"],
            augmented_query_text=augmented_query_text,
            recall_pack=recall_pack,
            answer_contract=answer_contract,
            current_time=started_at,
        )
        evidence_pack = evidence_resolution["evidence_pack"]
        fact_resolution_trace = evidence_resolution["fact_resolution_trace"]
        recall_pack = dict(recall_pack)
        recall_pack["answer_contract"] = answer_contract
        recall_pack["evidence_pack"] = evidence_pack
        recall_pack["fact_resolution_trace"] = fact_resolution_trace
        debug_log(
            "Pipeline",
            (
                f"{cycle_label} evidence_resolution done contract={answer_contract.get('contract')} "
                f"evidence_status={evidence_pack.get('status')}"
            ),
        )
        return {
            "recall_hint": recall_hint,
            "recall_pack": recall_pack,
            "answer_contract": answer_contract,
            "evidence_pack": evidence_pack,
        }

    def _build_pipeline_internal_contexts(
        self,
        *,
        state: dict[str, Any],
        persona: dict[str, Any],
        started_at: str,
        input_text: str,
        recent_turns: list[dict[str, Any]],
        trigger_kind: str,
        client_context: dict[str, Any],
        cycle_id: str | None,
        selected_candidate: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None,
        observation_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
        recall_hint: dict[str, Any],
        cycle_label: str,
    ) -> dict[str, Any]:
        # 内部コンテキスト
        debug_log("Pipeline", f"{cycle_label} context start")
        time_context = self._build_time_context(current_time=started_at)
        affect_context = self._build_affect_context(
            state=state,
            recall_hint=recall_hint,
            current_time=started_at,
        )
        drive_state_summary = self._summarize_drive_states(
            self._list_current_drive_states(
                state=state,
                current_time=started_at,
            )
        )
        world_state_trace, foreground_world_state = self._refresh_world_state_context(
            state=state,
            started_at=started_at,
            input_text=input_text,
            trigger_kind=trigger_kind,
            client_context=client_context,
            cycle_id=cycle_id,
            selected_candidate=selected_candidate,
            observation_summary=observation_summary,
            capability_request_summary=capability_request_summary,
        )
        ongoing_action_summary = self._summarize_ongoing_action(
            self._current_ongoing_action(
                state=state,
                current_time=started_at,
            )
        )
        capability_decision_view = self._build_capability_decision_view(
            state=state,
            current_time=started_at,
        )
        capability_decision_view = self._annotate_capability_decision_view_with_fresh_world_state(
            capability_decision_view=capability_decision_view,
            foreground_world_state=foreground_world_state,
            world_state_trace=world_state_trace,
            trigger_kind=trigger_kind,
        )
        initiative_context = self._build_initiative_context(
            state=state,
            persona=persona,
            current_time=started_at,
            time_context=time_context,
            recent_turns=recent_turns,
            trigger_kind=trigger_kind,
            client_context=client_context,
            drive_state_summary=drive_state_summary,
            foreground_world_state=foreground_world_state,
            world_state_trace=world_state_trace,
            ongoing_action_summary=ongoing_action_summary,
            capability_decision_view=capability_decision_view,
            selected_candidate=selected_candidate,
            pending_intent_selection=pending_intent_selection,
        )
        capability_result_context = self._build_capability_result_decision_context(
            trigger_kind=trigger_kind,
            observation_summary=observation_summary,
            capability_request_summary=capability_request_summary,
        )
        debug_log(
            "Pipeline",
            (
                f"{cycle_label} context done affect_states={len(affect_context.get('affect_states', []))} "
                f"drives={len(drive_state_summary or [])} world_states={len(foreground_world_state or [])} "
                f"ongoing_action={isinstance(ongoing_action_summary, dict)} "
                f"capabilities={len(capability_decision_view or [])} initiative={initiative_context is not None}"
            ),
        )
        return {
            "time_context": time_context,
            "affect_context": affect_context,
            "drive_state_summary": drive_state_summary,
            "world_state_trace": world_state_trace,
            "foreground_world_state": foreground_world_state,
            "ongoing_action_summary": ongoing_action_summary,
            "capability_decision_view": capability_decision_view,
            "initiative_context": initiative_context,
            "capability_result_context": capability_result_context,
        }

    def _run_pipeline_decision(
        self,
        *,
        input_text: str,
        current_input: CurrentInput,
        trigger_kind: str,
        recent_turns: list[dict[str, Any]],
        time_context: dict[str, Any],
        affect_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
        initiative_context: InitiativeContext | None,
        capability_result_context: dict[str, Any] | None,
        visual_observation_context: dict[str, Any] | None,
        recall_hint: dict[str, Any],
        recall_pack: dict[str, Any],
        decision_role: dict[str, Any],
        persona: dict[str, Any],
        cycle_label: str,
    ) -> dict[str, Any]:
        # decision生成
        debug_log("Pipeline", f"{cycle_label} decision start")
        decision_context = self._build_decision_context(
            input_text=input_text,
            current_input=current_input,
            trigger_kind=trigger_kind,
            recent_turns=recent_turns,
            time_context=time_context,
            affect_context=affect_context,
            drive_state_summary=drive_state_summary,
            foreground_world_state=foreground_world_state,
            ongoing_action_summary=ongoing_action_summary,
            capability_decision_view=capability_decision_view,
            initiative_context=initiative_context,
            capability_result_context=capability_result_context,
            visual_observation_context=visual_observation_context,
            recall_hint=recall_hint,
            recall_pack=recall_pack,
        )
        decision = self.llm.generate_decision(
            role_definition=decision_role,
            persona=persona,
            context=decision_context,
        )
        debug_log(
            "Pipeline",
            f"{cycle_label} decision done kind={decision['kind']} reason={self._clamp(decision['reason_summary'])}",
        )
        return decision

    def _run_pipeline_output(
        self,
        *,
        state: dict[str, Any],
        input_text: str,
        current_input: CurrentInput,
        recent_turns: list[dict[str, Any]],
        time_context: dict[str, Any],
        affect_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        ongoing_action_summary: dict[str, Any] | None,
        initiative_context: InitiativeContext | None,
        visual_observation_context: dict[str, Any] | None,
        recall_hint: dict[str, Any],
        recall_pack: dict[str, Any],
        reply_role: dict[str, Any],
        persona: dict[str, Any],
        decision: dict[str, Any],
        cycle_label: str,
    ) -> dict[str, Any]:
        # capability request
        dispatched_capability_request_summary: dict[str, Any] | None = None
        ongoing_action_transition_summary: dict[str, Any] | None = None
        if decision["kind"] == "capability_request":
            dispatch_result = self._dispatch_decision_capability_request(
                state=state,
                current_time=self._now_iso(),
                source_current_input=current_input.to_prompt_payload(),
                decision=decision,
            )
            dispatched_capability_request_summary = dispatch_result.get("capability_request_summary")
            transition_summary = dispatch_result.get("ongoing_action_transition_summary")
            if isinstance(transition_summary, dict):
                ongoing_action_transition_summary = transition_summary
            debug_log(
                "Pipeline",
                (
                    f"{cycle_label} capability dispatched "
                    f"request={dispatched_capability_request_summary.get('request_id') if isinstance(dispatched_capability_request_summary, dict) else '-'}"
                ),
            )

        # 返信
        reply_payload: dict[str, Any] | None = None
        reply_suppressed = (
            decision["kind"] == "reply"
            and current_input.source_kind == "capability_result"
            and current_input.response_target == "none"
        )
        if reply_suppressed:
            original_reason = str(decision.get("reason_summary") or "").strip()
            reason_summary = "capability result の source_current_input.response_target=none のため、内部観測結果として処理し assistant message を送信しない。"
            if original_reason:
                reason_summary = f"{reason_summary} 元判断: {original_reason}"
            decision.update(
                {
                    "kind": "noop",
                    "reason_code": "capability_result_response_target_none",
                    "reason_summary": reason_summary,
                    "requires_confirmation": False,
                    "pending_intent": None,
                    "capability_request": None,
                }
            )
            debug_log("Pipeline", f"{cycle_label} reply skipped capability_result_response_target=none")
        elif (
            decision["kind"] == "reply"
            and current_input.source_kind == "background_wake"
            and self._user_response_cycle_active()
        ):
            original_reason = str(decision.get("reason_summary") or "").strip()
            reason_summary = "ユーザー向け応答サイクルが進行中のため、background wake の自発発話は行わない。"
            if original_reason:
                reason_summary = f"{reason_summary} 元判断: {original_reason}"
            decision.update(
                {
                    "kind": "noop",
                    "reason_code": "background_wake_user_response_active",
                    "reason_summary": reason_summary,
                    "requires_confirmation": False,
                    "pending_intent": None,
                    "capability_request": None,
                }
            )
            debug_log("Pipeline", f"{cycle_label} reply skipped background_wake_user_response_active")
        elif decision["kind"] == "reply":
            debug_log("Pipeline", f"{cycle_label} reply start")
            reply_context = self._build_reply_context(
                input_text=input_text,
                current_input=current_input,
                recent_turns=recent_turns,
                time_context=time_context,
                affect_context=affect_context,
                drive_state_summary=drive_state_summary,
                foreground_world_state=foreground_world_state,
                ongoing_action_summary=ongoing_action_summary,
                initiative_context=initiative_context,
                visual_observation_context=visual_observation_context,
                recall_hint=recall_hint,
                recall_pack=recall_pack,
                decision=decision,
            )
            reply_payload = self.llm.generate_reply(
                role_definition=reply_role,
                persona=persona,
                context=reply_context,
            )
            debug_log("Pipeline", f"{cycle_label} reply done reply_chars={len(reply_payload['reply_text'])}")
        else:
            debug_log("Pipeline", f"{cycle_label} reply skipped decision_kind={decision['kind']}")
        return {
            "reply_payload": reply_payload,
            "capability_request_summary": dispatched_capability_request_summary,
            "ongoing_action_transition_summary": ongoing_action_transition_summary,
        }

    def _build_decision_context(
        self,
        *,
        input_text: str,
        current_input: CurrentInput,
        trigger_kind: str,
        recent_turns: list[dict[str, Any]],
        time_context: dict[str, Any],
        affect_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
        initiative_context: InitiativeContext | None,
        capability_result_context: dict[str, Any] | None,
        visual_observation_context: dict[str, Any] | None,
        recall_hint: dict[str, Any],
        recall_pack: dict[str, Any],
    ) -> DecisionContext:
        return DecisionContext(
            input_text=input_text,
            current_input=current_input,
            trigger_kind=trigger_kind,
            recent_turns=recent_turns,
            time_context=time_context,
            affect_context=affect_context,
            drive_state_summary=drive_state_summary,
            foreground_world_state=foreground_world_state,
            ongoing_action_summary=ongoing_action_summary,
            capability_decision_view=capability_decision_view,
            initiative_context=initiative_context,
            capability_result_context=capability_result_context,
            visual_observation_context=visual_observation_context,
            recall_hint=recall_hint,
            recall_pack=recall_pack,
        )

    def _build_reply_context(
        self,
        *,
        input_text: str,
        current_input: CurrentInput,
        recent_turns: list[dict[str, Any]],
        time_context: dict[str, Any],
        affect_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        ongoing_action_summary: dict[str, Any] | None,
        initiative_context: InitiativeContext | None,
        visual_observation_context: dict[str, Any] | None,
        recall_hint: dict[str, Any],
        recall_pack: dict[str, Any],
        decision: dict[str, Any],
    ) -> ReplyContext:
        return ReplyContext(
            input_text=input_text,
            current_input=current_input,
            recent_turns=recent_turns,
            time_context=time_context,
            affect_context=affect_context,
            drive_state_summary=drive_state_summary,
            foreground_world_state=foreground_world_state,
            ongoing_action_summary=ongoing_action_summary,
            initiative_context=initiative_context,
            visual_observation_context=visual_observation_context,
            recall_hint=recall_hint,
            recall_pack=recall_pack,
            decision=decision,
        )
