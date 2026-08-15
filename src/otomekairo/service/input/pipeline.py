from __future__ import annotations

from copy import deepcopy
from typing import Any

from otomekairo.llm.contexts import (
    CurrentInput,
    DecisionContext,
    InitiativeContext,
    PersonaContext,
    SpeechContext,
    build_persona_context_summary,
)
from otomekairo.interaction import InteractionContext
from otomekairo.service.agent_skills import origin_source_kind_from_capability_request
from otomekairo.service.capability import PreSendCheckWithheldError
from otomekairo.service.common import debug_log
from otomekairo.service.standing_concerns import standing_concern_factor_ref


WORKSPACE_CANDIDATE_LIMIT = 56
WORKSPACE_SUPPORTING_SELECTION_LIMIT = 3
WORKSPACE_SUPPRESSED_SELECTION_LIMIT = 5
WORKSPACE_LIMIT_RETAINED_KINDS = frozenset(
    {
        "self_state",
        "relationship",
        "prediction_error",
        "default_mode",
    }
)
WORKSPACE_MEMORY_SECTIONS = (
    "active_commitments",
    "active_topics",
    "relationship_model",
    "self_model",
    "person_model",
    "episodic_evidence",
    "event_evidence",
    "visual_observations",
)
WORKSPACE_MEMORY_ITEMS_PER_SECTION = 6
DEFAULT_MODE_CANDIDATE_LIMIT = 16
WORKSPACE_DERIVED_ITEMS_PER_SECTION = 6
PREDICTION_ERROR_SUMMARY_LIMIT = 4
PERSON_REFERENCE_FIELDS = {
    "person_ref",
    "sender_ref",
    "speaker_ref",
    "requested_by_person_ref",
    "target_person_ref",
    "current_person_ref",
}
PERSON_REFERENCE_LIST_FIELDS = {
    "participant_refs",
    "response_target_refs",
    "recipient_person_refs",
    "target_person_refs",
    "observed_person_refs",
}
PERSON_SCOPE_FIELDS = {
    "scope_key",
    "primary_scope_key",
    "target_scope_key",
    "subject_hint",
    "focus_scope_key",
}
PRE_SEND_CHECK_RETRY_FEEDBACK = (
    "前の MCP request は送信前チェックで見送られた。"
    "同じ目的の安全な別案または noop を選ぶ。"
)
PRE_SEND_CHECK_WITHHELD_NOTICE = (
    "外部送信候補に非公開情報が含まれる可能性があるため、送信しませんでした。"
)


class ServiceInputPipelineMixin:
    def _mark_standing_concerns_attended(self, **_kwargs) -> list[str]:
        return []

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
        interaction_context: InteractionContext | None = None,
        selected_candidate: dict[str, Any] | None = None,
        pending_intent_selection: dict[str, Any] | None = None,
        observation_summary: dict[str, Any] | None = None,
        capability_request_summary: dict[str, Any] | None = None,
        assistant_message_target_client_id: str | None = None,
        reference_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cycle_label = self._debug_cycle_label(cycle_id)
        current_client_context = client_context or {}
        current_input = self._build_current_input(
            input_text=input_text,
            trigger_kind=trigger_kind,
            interaction_context=interaction_context,
            capability_request_summary=capability_request_summary,
        )
        pipeline_assistant_message_target_client_id = self._pipeline_assistant_message_target_client_id(
            current_input=current_input,
            client_context=current_client_context,
            inherited_target_client_id=assistant_message_target_client_id,
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
        initial_activity_context = self._summarize_activity_context(
            self.store.get_current_activity_state(
                memory_set_id=state["selected_memory_set_id"],
                actor_ref=current_input.sender_ref,
                current_time=started_at,
            ) if current_input.sender_ref is not None else None,
            current_time=started_at,
        )
        # モデル選択
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        persona = state["personas"][state["selected_persona_id"]]
        persona_context_summary = self._persona_context_trace_summary(
            self._build_selected_persona_context(state=state, role="decision_generation")
        )

        # 想起入力
        recall_inputs = self._build_pipeline_recall_inputs(
            state=state,
            started_at=started_at,
            input_text=input_text,
            current_input=current_input,
            recent_turns=recent_turns,
            augmented_query_text=augmented_query_text,
            visual_observation_context=visual_observation_context,
            activity_context=initial_activity_context,
            model_config=selected_preset,
            persona_context=self._build_selected_persona_context(state=state, role="input_interpretation"),
            client_context=current_client_context,
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
            current_input=current_input,
            recent_turns=recent_turns,
            trigger_kind=trigger_kind,
            client_context=current_client_context,
            cycle_id=cycle_id,
            selected_candidate=selected_candidate,
            pending_intent_selection=pending_intent_selection,
            observation_summary=observation_summary,
            capability_request_summary=capability_request_summary,
            visual_observation_context=visual_observation_context,
            recall_hint=recall_hint,
            recall_pack=recall_pack,
            cycle_label=cycle_label,
            model_config=selected_preset,
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
            activity_context=pipeline_contexts["activity_context"],
            ongoing_action_summary=pipeline_contexts["ongoing_action_summary"],
            autonomous_run_summaries=pipeline_contexts["autonomous_run_summaries"],
            capability_decision_view=pipeline_contexts["capability_decision_view"],
            agent_skill_context=pipeline_contexts.get("agent_skill_context"),
            initiative_context=pipeline_contexts["initiative_context"],
            capability_result_context=pipeline_contexts["capability_result_context"],
            self_state_context=pipeline_contexts["self_state_context"],
            people_context=pipeline_contexts["people_context"],
            relationship_context=pipeline_contexts["relationship_context"],
            prediction_error_context=pipeline_contexts["prediction_error_context"],
            default_mode_context=pipeline_contexts["default_mode_context"],
            workspace_context=pipeline_contexts["workspace_context"],
            recall_hint=recall_hint,
            recall_pack=recall_pack,
            visual_observation_context=visual_observation_context,
            reference_context=reference_context,
            model_config=selected_preset,
            persona_context=self._build_selected_persona_context(state=state, role="decision_generation"),
            cycle_label=cycle_label,
        )

        # 最初の withhold だけは、候補や reviewer 理由を戻さず同一文脈で一度再判断する。
        suppress_outward_speech = current_client_context.get("suppress_outward_speech") is True
        suppress_outward_speech_reason = current_client_context.get("suppress_outward_speech_reason")
        if not isinstance(suppress_outward_speech_reason, str) or not suppress_outward_speech_reason.strip():
            suppress_outward_speech_reason = None

        def run_output(
            candidate_decision: dict[str, Any],
            *,
            review_attempt: int,
            prior_attempts: list[dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
            return self._run_pipeline_output(
                state=state,
                cycle_id=cycle_id,
                input_text=input_text,
                current_input=current_input,
                recent_turns=recent_turns,
                time_context=pipeline_contexts["time_context"],
                affect_context=pipeline_contexts["affect_context"],
                drive_state_summary=pipeline_contexts["drive_state_summary"],
                foreground_world_state=pipeline_contexts["foreground_world_state"],
                activity_context=pipeline_contexts["activity_context"],
                ongoing_action_summary=pipeline_contexts["ongoing_action_summary"],
                initiative_context=pipeline_contexts["initiative_context"],
                agent_skill_context=pipeline_contexts.get("agent_skill_context"),
                self_state_context=pipeline_contexts["self_state_context"],
                people_context=pipeline_contexts["people_context"],
                relationship_context=pipeline_contexts["relationship_context"],
                prediction_error_context=pipeline_contexts["prediction_error_context"],
                workspace_context=pipeline_contexts["workspace_context"],
                recall_hint=recall_hint,
                recall_pack=recall_pack,
                visual_observation_context=visual_observation_context,
                reference_context=reference_context,
                model_config=selected_preset,
                persona_context=self._build_selected_persona_context(
                    state=state,
                    role="expression_generation",
                    include_expression=True,
                ),
                decision=candidate_decision,
                assistant_message_target_client_id=pipeline_assistant_message_target_client_id,
                cycle_label=cycle_label,
                pre_send_check_attempt=review_attempt,
                pre_send_check_prior_attempts=prior_attempts,
                suppress_outward_speech=suppress_outward_speech,
                suppress_outward_speech_reason=suppress_outward_speech_reason,
            )

        system_notice: dict[str, Any] | None = None
        try:
            output_result = run_output(decision, review_attempt=1)
        except PreSendCheckWithheldError as first_withhold:
            first_attempt = deepcopy(first_withhold.audit_summary)
            decision = self._run_pipeline_decision(
                input_text=input_text,
                current_input=current_input,
                trigger_kind=trigger_kind,
                recent_turns=recent_turns,
                time_context=pipeline_contexts["time_context"],
                affect_context=pipeline_contexts["affect_context"],
                drive_state_summary=pipeline_contexts["drive_state_summary"],
                foreground_world_state=pipeline_contexts["foreground_world_state"],
                activity_context=pipeline_contexts["activity_context"],
                ongoing_action_summary=pipeline_contexts["ongoing_action_summary"],
                autonomous_run_summaries=pipeline_contexts["autonomous_run_summaries"],
                capability_decision_view=pipeline_contexts["capability_decision_view"],
                agent_skill_context=pipeline_contexts.get("agent_skill_context"),
                initiative_context=pipeline_contexts["initiative_context"],
                capability_result_context=pipeline_contexts["capability_result_context"],
                self_state_context=pipeline_contexts["self_state_context"],
                people_context=pipeline_contexts["people_context"],
                relationship_context=pipeline_contexts["relationship_context"],
                prediction_error_context=pipeline_contexts["prediction_error_context"],
                default_mode_context=pipeline_contexts["default_mode_context"],
                workspace_context=pipeline_contexts["workspace_context"],
                recall_hint=recall_hint,
                recall_pack=recall_pack,
                visual_observation_context=visual_observation_context,
                reference_context=reference_context,
                model_config=selected_preset,
                persona_context=self._build_selected_persona_context(state=state, role="decision_generation"),
                cycle_label=f"{cycle_label} pre-send-check-retry",
                pre_send_check_feedback=PRE_SEND_CHECK_RETRY_FEEDBACK,
            )
            if not self._decision_has_outward_speech(decision) and not self._decision_has_self_activity_result(
                decision
            ):
                decision = self._pre_send_check_terminal_noop(
                    attempts=[first_attempt],
                    reason_code="pre_send_check_retry_noop",
                )
                output_result = self._empty_pipeline_output_result()
                system_notice = self._pre_send_check_withheld_notice(current_input=current_input)
            else:
                try:
                    output_result = run_output(
                        decision,
                        review_attempt=2,
                        prior_attempts=[first_attempt],
                    )
                    capability_summary = output_result.get("capability_request_summary")
                    review_summary = (
                        capability_summary.get("pre_send_check")
                        if isinstance(capability_summary, dict)
                        else None
                    )
                    decision["pre_send_check"] = (
                        deepcopy(review_summary)
                        if isinstance(review_summary, dict)
                        else {
                            "result_status": "recovered",
                            "attempts": [first_attempt],
                        }
                    )
                except PreSendCheckWithheldError as second_withhold:
                    second_attempt = deepcopy(second_withhold.audit_summary)
                    if self._decision_has_outward_speech(decision):
                        decision = self._drop_self_activity_after_pre_send_withhold(
                            decision,
                            attempts=[first_attempt, second_attempt],
                        )
                        output_result = run_output(
                            decision,
                            review_attempt=2,
                            prior_attempts=[first_attempt, second_attempt],
                        )
                        system_notice = self._pre_send_check_withheld_notice(current_input=current_input)
                    else:
                        decision = self._pre_send_check_terminal_noop(
                            attempts=[first_attempt, second_attempt],
                            reason_code="pre_send_check_withheld",
                        )
                        output_result = self._empty_pipeline_output_result()
                        system_notice = self._pre_send_check_withheld_notice(current_input=current_input)

        self._mark_standing_concerns_attended(
            decision=decision,
            workspace_context=pipeline_contexts.get("workspace_context"),
            current_time=started_at,
        )
        # 結果
        return {
            "persona_id": state["selected_persona_id"],
            "persona_display_name": persona["display_name"],
            "current_input": current_input.to_prompt_payload(),
            "augmented_query_text": augmented_query_text,
            "recall_hint": recall_hint,
            "recall_pack": recall_pack,
            "answer_contract": answer_contract,
            "evidence_pack": evidence_pack,
            "persona_context_summary": persona_context_summary,
            "time_context": pipeline_contexts["time_context"],
            "affect_context": pipeline_contexts["affect_context"],
            "drive_state_summary": pipeline_contexts["drive_state_summary"],
            "foreground_world_state": pipeline_contexts["foreground_world_state"],
            "activity_context": pipeline_contexts["activity_context"],
            "activity_trace": pipeline_contexts["activity_trace"],
            "ongoing_action_summary": pipeline_contexts["ongoing_action_summary"],
            "autonomous_run_summaries": pipeline_contexts["autonomous_run_summaries"],
            "capability_decision_view": pipeline_contexts["capability_decision_view"],
            "agent_skill_activation": (
                self._agent_skill_activation_summary(pipeline_contexts["agent_skill_context"])
                if isinstance(pipeline_contexts.get("agent_skill_context"), dict)
                else None
            ),
            "initiative_context": pipeline_contexts["initiative_context"],
            "capability_result_context": pipeline_contexts["capability_result_context"],
            "self_state_context": pipeline_contexts["self_state_context"],
            "people_context": pipeline_contexts["people_context"],
            "relationship_context": pipeline_contexts["relationship_context"],
            "prediction_error_context": pipeline_contexts["prediction_error_context"],
            "default_mode_context": pipeline_contexts["default_mode_context"],
            "workspace_context": pipeline_contexts["workspace_context"],
            "visual_observation_context": visual_observation_context,
            "world_state_trace": pipeline_contexts["world_state_trace"],
            "decision": decision,
            "speech_payload": output_result["speech_payload"],
            "capability_request_summary": output_result["capability_request_summary"],
            "ongoing_action_transition_summary": output_result["ongoing_action_transition_summary"],
            "autonomous_run_summary": output_result["autonomous_run_summary"],
            "autonomous_run_step_result": output_result["autonomous_run_step_result"],
            "system_notice": system_notice,
        }

    def _build_current_input(
        self,
        *,
        input_text: str,
        trigger_kind: str,
        interaction_context: InteractionContext | None,
        capability_request_summary: dict[str, Any] | None = None,
    ) -> CurrentInput:
        normalized_trigger = trigger_kind.strip() or "user_message"
        if normalized_trigger == "user_message":
            if interaction_context is None or interaction_context.speaker_ref is None:
                raise ValueError("user_message requires interaction_context with speaker_ref.")
            sender_kind = "person"
            sender_ref = interaction_context.speaker_ref
            source_kind = "user_message"
            response_target_refs = interaction_context.participant_refs
        elif normalized_trigger in {"wake", "background_thinking"}:
            sender_kind = "system"
            sender_ref = None
            source_kind = normalized_trigger
            response_target_refs = interaction_context.participant_refs if interaction_context is not None else ()
        elif normalized_trigger == "capability_result":
            sender_kind = "capability"
            sender_ref = None
            source_kind = "capability_result"
            inherited_context = self._capability_result_interaction_context(capability_request_summary)
            if inherited_context is not None:
                interaction_context = inherited_context
            response_target_refs = self._capability_result_response_target_refs(capability_request_summary)
        else:
            sender_kind = "system"
            sender_ref = None
            source_kind = normalized_trigger
            response_target_refs = ()
        return CurrentInput(
            sender_kind=sender_kind,
            sender_ref=sender_ref,
            source_kind=source_kind,
            response_target_refs=tuple(response_target_refs),
            interaction_context=interaction_context,
            text=input_text,
        )

    def _empty_pipeline_output_result(self) -> dict[str, Any]:
        return {
            "speech_payload": None,
            "capability_request_summary": None,
            "ongoing_action_transition_summary": None,
            "autonomous_run_summary": None,
            "autonomous_run_step_result": None,
        }

    def _pre_send_check_terminal_noop(
        self,
        *,
        attempts: list[dict[str, Any]],
        reason_code: str,
    ) -> dict[str, Any]:
        return {
            "kind": "noop",
            "reason_code": reason_code,
            "reason_summary": "送信前チェックの結果、外部送信を行わず終了した。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "pre_send_check": {
                "result_status": "withheld",
                "attempts": deepcopy(attempts),
            },
        }

    def _pre_send_check_withheld_notice(
        self,
        *,
        current_input: CurrentInput,
    ) -> dict[str, Any]:
        return {
            "source_kind": "pre_send_check",
            "code": "pre_send_check_withheld",
            "message": PRE_SEND_CHECK_WITHHELD_NOTICE,
            "conversation_visible": (
                current_input.sender_kind == "person"
                or (
                    current_input.source_kind == "capability_result"
                    and current_input.interaction_ref is not None
                )
            ),
            "interaction_ref": current_input.interaction_ref,
            "recipient_person_refs": list(current_input.participant_refs),
        }

    def _capability_result_response_target_refs(
        self,
        capability_request_summary: dict[str, Any] | None,
    ) -> tuple[str, ...]:
        if isinstance(capability_request_summary, dict):
            source_current_input = capability_request_summary.get("source_current_input")
            if isinstance(source_current_input, dict):
                raw_refs = source_current_input.get("response_target_refs")
                if isinstance(raw_refs, list):
                    return tuple(
                        value.strip()
                        for value in raw_refs
                        if isinstance(value, str) and value.strip()
                    )
        return ()

    def _capability_result_interaction_context(
        self,
        capability_request_summary: dict[str, Any] | None,
    ) -> InteractionContext | None:
        if not isinstance(capability_request_summary, dict):
            return None
        source_current_input = capability_request_summary.get("source_current_input")
        if not isinstance(source_current_input, dict):
            return None
        raw_context = source_current_input.get("interaction_context")
        if not isinstance(raw_context, dict):
            return None
        from otomekairo.interaction import normalize_interaction_context

        return normalize_interaction_context(
            raw_context,
            required=True,
            require_speaker=False,
        )

    def _pipeline_assistant_message_target_client_id(
        self,
        *,
        current_input: CurrentInput,
        client_context: dict[str, Any],
        inherited_target_client_id: str | None,
    ) -> str | None:
        if not current_input.response_target_refs:
            return None
        inherited_target = self._client_context_text(inherited_target_client_id, limit=128)
        if inherited_target is not None:
            return inherited_target
        if current_input.sender_kind == "person" and current_input.source_kind == "user_message":
            return self._client_context_text(client_context.get("client_id"), limit=128)
        return None

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
        activity_context: dict[str, Any] | None,
        model_config: dict[str, Any],
        persona_context: Any,
        client_context: dict[str, Any],
        cycle_label: str,
    ) -> dict[str, Any]:
        if self._should_skip_recall_interpretation_for_wake_visual_observation(
            state=state,
            current_time=started_at,
            current_input=current_input,
            client_context=client_context,
        ):
            return self._build_visual_observation_direct_recall_inputs(
                augmented_query_text=augmented_query_text,
                current_time=started_at,
            )

        # 入口解釈
        recall_hint_recent_turns = self._recall_hint_recent_turns(recent_turns)
        input_interpretation = self.llm.generate_input_interpretation(
            model_config=model_config,
            persona_context=persona_context,
            input_text=input_text,
            current_input=current_input,
            recent_turns=recall_hint_recent_turns,
            current_time=started_at,
            visual_observation_context=visual_observation_context,
            activity_context=activity_context,
        )
        recall_hint = input_interpretation["recall_hint"]
        answer_contract = input_interpretation["answer_contract"]
        debug_log(
            "Pipeline",
            (
                f"{cycle_label} input_interpretation done "
                f"focus={recall_hint['primary_recall_focus']} confidence={recall_hint['confidence']} "
                f"contract={answer_contract.get('contract')}"
            ),
        )

        # recall_pack構築
        recall_pack = self.recall.build_recall_pack(
            state=state,
            augmented_query_text=augmented_query_text,
            recall_hint=recall_hint,
            current_person_ref=(
                current_input.sender_ref
                if current_input.sender_ref is not None
                else current_input.participant_refs[0]
                if current_input.participant_refs
                else None
            ),
            current_time=started_at,
        )
        # 回答根拠解決
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

    def _should_skip_recall_interpretation_for_wake_visual_observation(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
        current_input: CurrentInput,
        client_context: dict[str, Any],
    ) -> bool:
        if (
            current_input.sender_kind != "system"
            or current_input.source_kind not in {"wake", "background_thinking"}
            or client_context.get("autonomous_visual_observation_direct_entry") is not True
        ):
            return False
        return not bool(self._due_standing_concerns(state=state, current_time=current_time))

    def _build_visual_observation_direct_recall_inputs(
        self,
        *,
        augmented_query_text: str,
        current_time: str,
    ) -> dict[str, Any]:
        recall_hint = self._empty_recall_hint()
        answer_contract = {
            "contract": "summary",
            "reason_codes": ["visual_observation_direct_entry"],
            "boundary": "none",
            "target_actor": "any",
            "query_terms": [],
            "requires_direct_evidence": False,
        }
        evidence_pack = {
            "status": "summary",
            "answer_contract": answer_contract,
            "requires_direct_evidence": False,
            "evidence_items": [],
            "speech_guidance": "視覚観測と現在文脈で判断する。",
        }
        fact_resolution_trace = self._empty_fact_resolution_trace()
        fact_resolution_trace["query"] = {
            **fact_resolution_trace["query"],
            "augmented_query_text": augmented_query_text,
            "current_time": current_time,
            "reason_codes": answer_contract["reason_codes"],
        }
        fact_resolution_trace["speech_guidance"] = evidence_pack["speech_guidance"]
        recall_pack = self._empty_recall_pack()
        recall_pack["answer_contract"] = answer_contract
        recall_pack["evidence_pack"] = evidence_pack
        recall_pack["fact_resolution_trace"] = fact_resolution_trace
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
        current_input: CurrentInput,
        recent_turns: list[dict[str, Any]],
        trigger_kind: str,
        client_context: dict[str, Any],
        cycle_id: str | None,
        selected_candidate: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None,
        observation_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
        visual_observation_context: dict[str, Any] | None,
        recall_hint: dict[str, Any],
        recall_pack: dict[str, Any],
        cycle_label: str,
        model_config: dict[str, Any],
    ) -> dict[str, Any]:
        # 内部コンテキスト
        time_context = self._build_time_context(current_time=started_at)
        affect_context = self._build_affect_context(
            state=state,
            recall_hint=recall_hint,
            current_person_ref=(
                current_input.sender_ref
                if current_input.sender_ref is not None
                else current_input.participant_refs[0]
                if current_input.participant_refs
                else None
            ),
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
            persona_context=self._build_selected_persona_context(state=state, role="world_state"),
            current_person_ref=(
                current_input.sender_ref
                if current_input.sender_ref is not None
                else current_input.participant_refs[0]
                if current_input.participant_refs
                else None
            ),
        )
        ongoing_action_summary = self._summarize_ongoing_action(
            self._current_ongoing_action(
                state=state,
                current_time=started_at,
            )
        )
        autonomous_run_summaries = self._list_autonomous_run_prompt_summaries(
            state=state,
            current_time=started_at,
        )
        capability_decision_view = self._build_capability_decision_view(
            state=state,
            current_time=started_at,
            trigger_kind=trigger_kind,
        )
        capability_decision_view = self._annotate_capability_decision_view_with_fresh_visual_context(
            capability_decision_view=capability_decision_view,
            foreground_world_state=foreground_world_state,
            world_state_trace=world_state_trace,
            trigger_kind=trigger_kind,
            client_context=client_context,
        )
        agent_skill_context = self._build_agent_skill_context(
            model_config=model_config,
            current_input=current_input,
            trigger_kind=trigger_kind,
            capability_decision_view=capability_decision_view,
            prior_activation=(
                capability_request_summary.get("source_current_input", {}).get("agent_skill_activation")
                if isinstance(capability_request_summary, dict)
                and isinstance(capability_request_summary.get("source_current_input"), dict)
                else None
            ),
            origin_source_kind=origin_source_kind_from_capability_request(
                capability_request_summary
            ),
        )
        activity_context, activity_trace = self._refresh_activity_context(
            state=state,
            started_at=started_at,
            input_text=input_text,
            current_input=current_input.to_prompt_payload(),
            recent_turns=recent_turns,
            trigger_kind=trigger_kind,
            client_context=client_context,
            observation_summary=observation_summary,
            visual_observation_context=visual_observation_context,
            foreground_world_state=foreground_world_state,
            cycle_id=cycle_id,
            cycle_label=cycle_label,
            persona_context=self._build_selected_persona_context(state=state, role="activity_state"),
        )
        initiative_context = self._build_initiative_context(
            state=state,
            persona=persona,
            persona_context_summary=build_persona_context_summary(persona),
            current_time=started_at,
            time_context=time_context,
            recent_turns=recent_turns,
            trigger_kind=trigger_kind,
            client_context=client_context,
            drive_state_summary=drive_state_summary,
            foreground_world_state=foreground_world_state,
            activity_context=activity_context,
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
        self_state_context = self._build_self_state_context(
            foreground_world_state=foreground_world_state,
            ongoing_action_summary=ongoing_action_summary,
            capability_result_context=capability_result_context,
            visual_observation_context=visual_observation_context,
        )
        people_context = self._build_people_context(
            state=state,
            current_input=current_input,
            structured_sources=[
                recall_pack,
                selected_candidate,
                pending_intent_selection,
                affect_context,
                foreground_world_state,
                activity_context,
                ongoing_action_summary,
                autonomous_run_summaries,
                capability_result_context,
            ],
        )
        relationship_context = self._build_relationship_context(
            recall_pack=recall_pack,
            affect_context=affect_context,
        )
        prediction_error_context = self._build_prediction_error_context(
            world_state_trace=world_state_trace,
            foreground_world_state=foreground_world_state,
            capability_result_context=capability_result_context,
        )
        default_mode_context = self._build_default_mode_context(
            recall_pack=recall_pack,
            affect_context=affect_context,
        )
        workspace_context = self._build_workspace_context(
            current_input=current_input,
            due_standing_concerns=self._due_standing_concerns(
                state=state,
                current_time=started_at,
            ),
            recall_pack=recall_pack,
            drive_state_summary=drive_state_summary,
            foreground_world_state=foreground_world_state,
            activity_context=activity_context,
            ongoing_action_summary=ongoing_action_summary,
            autonomous_run_summaries=autonomous_run_summaries,
            capability_decision_view=capability_decision_view,
            initiative_context=initiative_context,
            capability_result_context=capability_result_context,
            visual_observation_context=visual_observation_context,
            self_state_context=self_state_context,
            relationship_context=relationship_context,
            prediction_error_context=prediction_error_context,
            default_mode_context=default_mode_context,
        )
        return {
            "time_context": time_context,
            "affect_context": affect_context,
            "drive_state_summary": drive_state_summary,
            "world_state_trace": world_state_trace,
            "foreground_world_state": foreground_world_state,
            "activity_context": activity_context,
            "activity_trace": activity_trace,
            "ongoing_action_summary": ongoing_action_summary,
            "autonomous_run_summaries": autonomous_run_summaries,
            "capability_decision_view": capability_decision_view,
            "agent_skill_context": agent_skill_context,
            "initiative_context": initiative_context,
            "capability_result_context": capability_result_context,
            "self_state_context": self_state_context,
            "people_context": people_context,
            "relationship_context": relationship_context,
            "prediction_error_context": prediction_error_context,
            "default_mode_context": default_mode_context,
            "workspace_context": workspace_context,
        }

    def _build_self_state_context(
        self,
        *,
        foreground_world_state: list[dict[str, Any]] | None,
        ongoing_action_summary: dict[str, Any] | None,
        capability_result_context: dict[str, Any] | None,
        visual_observation_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        sensory_confidence: list[dict[str, Any]] = []
        if isinstance(visual_observation_context, dict):
            visual_entry: dict[str, Any] = {
                "channel": "visual",
                "source": "visual_observation_context",
                "image_interpreted": visual_observation_context.get("image_interpreted"),
            }
            for key in ("source", "source_kind", "source_owner", "confidence_hint"):
                value = visual_observation_context.get(key)
                if value is not None:
                    visual_entry[key] = value
            summary_text = self._workspace_item_summary(
                visual_observation_context,
                ("visual_summary_text", "summary_text"),
            )
            if summary_text is not None:
                visual_entry["summary_text"] = summary_text
            sensory_confidence.append(visual_entry)
        for index, item in enumerate(foreground_world_state or []):
            if not isinstance(item, dict):
                continue
            confidence_hint = self._workspace_text(item.get("confidence_hint"))
            if confidence_hint is None:
                continue
            sensory_entry: dict[str, Any] = {
                "channel": item.get("state_type") or f"world_state:{index}",
                "source": "foreground_world_state",
                "confidence_hint": confidence_hint,
            }
            for key in ("scope", "source_owner", "summary_text"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    sensory_entry[key] = value.strip()
            sensory_confidence.append(sensory_entry)

        agency_confidence: list[dict[str, Any]] = []
        if isinstance(capability_result_context, dict):
            source_request_summary = capability_result_context.get("source_request_summary")
            source_capability_id = capability_result_context.get("source_capability_id")
            if isinstance(source_request_summary, dict):
                source_capability_id = source_request_summary.get("capability_id") or source_capability_id
            observation_summary = capability_result_context.get("observation_summary")
            agency_entry: dict[str, Any] = {
                "source": "capability_result_context",
                "capability_id": source_capability_id,
            }
            if isinstance(observation_summary, dict):
                for key in ("status", "status_text", "error", "result_status"):
                    value = observation_summary.get(key)
                    if value is not None:
                        agency_entry[key] = value
            agency_confidence.append(agency_entry)

        focus_stability: dict[str, Any] | None = None
        if isinstance(ongoing_action_summary, dict):
            focus_stability = {
                "source": "ongoing_action_summary",
                "status": ongoing_action_summary.get("status"),
                "capability_id": ongoing_action_summary.get("capability_id"),
                "summary_text": self._workspace_item_summary(
                    ongoing_action_summary,
                    ("reason_summary", "current_step_summary", "summary_text"),
                ),
            }

        payload: dict[str, Any] = {
            "state_boundary": "self_state_context は AI 本体側の感覚信頼度、働きかけやすさ、継続行動の安定を表す短期派生 view であり、mood_state と統合しない。",
        }
        if sensory_confidence:
            payload["sensory_confidence"] = sensory_confidence
        if agency_confidence:
            payload["agency_confidence"] = agency_confidence
        if focus_stability:
            payload["focus_stability"] = focus_stability
        return payload if len(payload) > 1 else None

    def _build_relationship_context(
        self,
        *,
        recall_pack: dict[str, Any],
        affect_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        relationship_items = self._relationship_context_items(
            recall_pack=recall_pack,
        )
        affect_items = [
            item
            for item in affect_context.get("affect_states", [])
            if isinstance(item, dict)
            and item.get("target_scope_type") in {"relationship", "entity"}
        ]
        payload: dict[str, Any] = {
            "state_boundary": "relationship_context は recall_pack と affect_context から派生する現在 view であり、関係記憶の正本ではない。",
        }
        if relationship_items:
            payload["relationship_items"] = relationship_items
        if affect_items:
            payload["affect_items"] = affect_items
        return payload if len(payload) > 1 else None

    def _build_people_context(
        self,
        *,
        state: dict[str, Any],
        current_input: CurrentInput,
        structured_sources: list[Any],
    ) -> list[dict[str, str]]:
        # 人物候補は閉じた schema field からだけ集め、自然文からは推定しない。
        ordered_refs: list[str] = []
        display_names: dict[str, str] = {}
        if current_input.interaction_context is not None:
            for participant in current_input.interaction_context.participants:
                ordered_refs.append(participant.person_ref)
                display_names[participant.person_ref] = participant.display_name
        for source in structured_sources:
            ordered_refs.extend(self._structured_person_refs(source))
            for person in self._observed_persons_from_structured_source(source):
                ordered_refs.append(person["person_ref"])
                display_names.setdefault(person["person_ref"], person["display_name"])
        person_refs = list(dict.fromkeys(ordered_refs))
        if not person_refs:
            return []

        # 音声人物の呼び名は config DB の割当を正本にする。
        for person_ref in person_refs:
            if not person_ref.startswith("person:voice:"):
                continue
            speaker = self.store.get_voice_speaker(person_ref)
            if speaker is not None:
                display_names[person_ref] = speaker["display_name"]

        memory_set_id = state.get("selected_memory_set_id")
        if not isinstance(memory_set_id, str) or not memory_set_id.strip():
            return []
        records = self.store.get_entity_registry_records(
            memory_set_id=memory_set_id,
            entity_refs=person_refs,
        )
        for record in records:
            entity_ref = record.get("entity_ref")
            display_name = record.get("display_name")
            if (
                isinstance(entity_ref, str)
                and entity_ref.startswith("person:")
                and isinstance(display_name, str)
                and display_name.strip()
            ):
                display_names.setdefault(entity_ref, display_name.strip())

        items: list[dict[str, str]] = []
        for person_ref in person_refs:
            display_name = display_names.get(person_ref)
            if display_name is None:
                continue
            items.append(
                {
                    "person_ref": person_ref,
                    "display_name": display_name,
                }
            )
        return items

    def _structured_person_refs(self, value: Any) -> list[str]:
        # ID と scope key の閉じた形式だけを再帰的に走査する。
        refs: list[str] = []
        if isinstance(value, list):
            for item in value:
                refs.extend(self._structured_person_refs(item))
            return refs
        if not isinstance(value, dict):
            return refs
        for key, item in value.items():
            if key in PERSON_REFERENCE_FIELDS:
                if isinstance(item, str) and item.startswith("person:") and item != "person:":
                    refs.append(item)
                continue
            if key in PERSON_REFERENCE_LIST_FIELDS:
                if isinstance(item, list):
                    refs.extend(
                        ref
                        for ref in item
                        if isinstance(ref, str) and ref.startswith("person:") and ref != "person:"
                    )
                continue
            if key in PERSON_SCOPE_FIELDS:
                refs.extend(self._person_refs_from_scope_value(item))
                continue
            refs.extend(self._structured_person_refs(item))
        return refs

    def _observed_persons_from_structured_source(self, value: Any) -> list[dict[str, str]]:
        persons: list[dict[str, str]] = []
        if isinstance(value, list):
            for item in value:
                persons.extend(self._observed_persons_from_structured_source(item))
            return persons
        if not isinstance(value, dict):
            return persons
        raw_persons = value.get("observed_persons")
        if isinstance(raw_persons, list):
            for item in raw_persons:
                if not isinstance(item, dict):
                    continue
                person_ref = item.get("person_ref")
                display_name = item.get("display_name")
                if not isinstance(person_ref, str) or not person_ref.startswith("person:"):
                    continue
                if not isinstance(display_name, str) or not display_name.strip():
                    continue
                persons.append(
                    {
                        "person_ref": person_ref,
                        "display_name": display_name.strip(),
                    }
                )
        for key, item in value.items():
            if key == "observed_persons":
                continue
            if key in PERSON_REFERENCE_FIELDS or key in PERSON_REFERENCE_LIST_FIELDS or key in PERSON_SCOPE_FIELDS:
                continue
            persons.extend(self._observed_persons_from_structured_source(item))
        return persons

    def _person_refs_from_scope_value(self, value: Any) -> list[str]:
        if not isinstance(value, str):
            return []
        if value.startswith("person:") and value != "person:":
            return [value]
        if value.startswith("self|person:") and value != "self|person:":
            return [value.removeprefix("self|")]
        return []

    def _relationship_context_items(self, *, recall_pack: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for section in ("relationship_model", "person_model", "active_commitments", "active_topics"):
            section_items = recall_pack.get(section)
            if not isinstance(section_items, list):
                continue
            for index, item in enumerate(section_items[:WORKSPACE_DERIVED_ITEMS_PER_SECTION]):
                if not isinstance(item, dict):
                    continue
                summary_text = self._workspace_item_summary(
                    item,
                    ("summary_text", "outcome_text"),
                )
                if summary_text is None:
                    continue
                items.append(
                    {
                        "item_ref": self._workspace_item_ref(
                            item,
                            ("memory_unit_id", "episode_id", "event_id"),
                            fallback=f"{section}:{index}",
                        ),
                        "source": f"recall_pack.{section}",
                        "summary_text": summary_text,
                        "metadata": self._workspace_metadata(
                            item,
                            ("memory_type", "scope_type", "scope_key", "commitment_state", "retrieval_lane"),
                        ),
                    }
                )
        return items

    def _build_prediction_error_context(
        self,
        *,
        world_state_trace: Any,
        foreground_world_state: list[dict[str, Any]] | None,
        capability_result_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        signals: list[dict[str, Any]] = []
        previous = (
            world_state_trace.previous_foreground_world_state
            if world_state_trace is not None
            and isinstance(getattr(world_state_trace, "previous_foreground_world_state", None), list)
            else []
        )
        current = foreground_world_state or (
            world_state_trace.foreground_world_state
            if world_state_trace is not None
            and isinstance(getattr(world_state_trace, "foreground_world_state", None), list)
            else []
        )
        if previous or current:
            changed = self._foreground_world_state_signature(previous) != self._foreground_world_state_signature(current)
            signals.append(
                {
                    "signal_kind": "world_state_difference",
                    "summary_text": "foreground_world_state の構造化署名に差分候補がある。",
                    "changed": changed,
                    "previous_count": len(previous),
                    "current_count": len(current),
                    "previous_summaries": previous[:PREDICTION_ERROR_SUMMARY_LIMIT],
                    "current_summaries": current[:PREDICTION_ERROR_SUMMARY_LIMIT],
                }
            )
        if isinstance(capability_result_context, dict):
            observation_summary = capability_result_context.get("observation_summary")
            signal: dict[str, Any] = {
                "signal_kind": "capability_result",
                "summary_text": "capability result を受け取り、実行結果の差分候補として扱う。",
                "source_capability_id": capability_result_context.get("source_capability_id"),
            }
            if isinstance(observation_summary, dict):
                for key in ("status", "status_text", "error", "result_status"):
                    value = observation_summary.get(key)
                    if value is not None:
                        signal[key] = value
            signals.append(signal)
        if not signals:
            return None
        return {
            "state_boundary": "prediction_error_context は期待との差分候補を扱う派生 view であり、世界状態や記憶の正本ではない。",
            "signals": signals,
        }

    def _build_default_mode_context(
        self,
        *,
        recall_pack: dict[str, Any],
        affect_context: dict[str, Any],
    ) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        for section in ("active_commitments", "active_topics", "relationship_model", "episodic_evidence"):
            items = recall_pack.get(section)
            if not isinstance(items, list):
                continue
            for index, item in enumerate(items[:WORKSPACE_DERIVED_ITEMS_PER_SECTION]):
                if not isinstance(item, dict):
                    continue
                summary_text = self._workspace_item_summary(
                    item,
                    ("summary_text", "outcome_text"),
                )
                if summary_text is None:
                    continue
                candidates.append(
                    {
                        "candidate_ref": f"default_mode:{section}:{self._workspace_item_ref(item, ('memory_unit_id', 'episode_id', 'event_id'), fallback=str(index))}",
                        "source": f"recall_pack.{section}",
                        "summary_text": summary_text,
                        "resurfacing_policy": "即発話せず、workspace の前景化候補として扱う。",
                    }
                )
        for index, item in enumerate(affect_context.get("recent_episode_affects", [])[:WORKSPACE_DERIVED_ITEMS_PER_SECTION]):
            if not isinstance(item, dict):
                continue
            summary_text = self._workspace_item_summary(
                item,
                ("summary_text", "affect_label"),
            )
            if summary_text is None:
                continue
            candidates.append(
                {
                    "candidate_ref": f"default_mode:recent_episode_affect:{index}",
                    "source": "affect_context.recent_episode_affects",
                    "summary_text": summary_text,
                    "resurfacing_policy": "即発話せず、workspace の前景化候補として扱う。",
                }
            )
        if not candidates:
            return None
        return {
            "state_boundary": "default_mode_context は静かな再浮上候補の派生 view であり、自律発話の決定ではない。",
            "resurfacing_candidates": candidates[:DEFAULT_MODE_CANDIDATE_LIMIT],
            "total_candidate_count": len(candidates),
        }

    def _build_workspace_context(
        self,
        *,
        current_input: CurrentInput,
        due_standing_concerns: list[dict[str, Any]] | None = None,
        recall_pack: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        activity_context: dict[str, Any] | None,
        ongoing_action_summary: dict[str, Any] | None,
        autonomous_run_summaries: list[dict[str, Any]] | None,
        capability_decision_view: list[dict[str, Any]] | None,
        initiative_context: InitiativeContext | None,
        capability_result_context: dict[str, Any] | None,
        visual_observation_context: dict[str, Any] | None,
        self_state_context: dict[str, Any] | None,
        relationship_context: dict[str, Any] | None,
        prediction_error_context: dict[str, Any] | None,
        default_mode_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        used_refs: set[str] = set()
        source_counts: dict[str, int] = {}
        self._append_workspace_visual_observation_candidates(
            candidates=candidates,
            used_refs=used_refs,
            source_counts=source_counts,
            initiative_context=initiative_context,
        )
        self._append_workspace_candidate(
            candidates=candidates,
            used_refs=used_refs,
            source_counts=source_counts,
            factor_ref=f"current_input:{current_input.source_kind}",
            kind="current_input",
            source="current_input",
            summary_text=current_input.text.strip() or f"{current_input.source_kind} trigger",
            metadata={
                "sender_kind": current_input.sender_kind,
                "sender_ref": current_input.sender_ref,
                "source_kind": current_input.source_kind,
                "response_target_refs": list(current_input.response_target_refs),
            },
        )
        self._append_workspace_context_item(
            candidates=candidates,
            used_refs=used_refs,
            source_counts=source_counts,
            factor_ref="capability_result:current",
            kind="capability_result",
            source="capability_result_context",
            item=capability_result_context,
            summary_keys=("status_text", "result_summary_text", "summary_text", "error"),
            metadata_keys=("capability_id", "request_id", "result_status", "response_target_refs"),
        )
        for concern in due_standing_concerns or []:
            concern_id = str(concern.get("concern_id") or "").strip()
            if not concern_id:
                continue
            self._append_workspace_candidate(
                candidates=candidates,
                used_refs=used_refs,
                source_counts=source_counts,
                factor_ref=standing_concern_factor_ref(concern_id),
                kind="standing_concern",
                source="standing_concerns",
                summary_text=str(concern.get("concern_summary") or "").strip() or None,
                metadata={"concern_id": concern_id},
            )
        self._append_workspace_initiative_candidates(
            candidates=candidates,
            used_refs=used_refs,
            source_counts=source_counts,
            initiative_context=initiative_context,
        )
        self._append_workspace_suppression_candidates(
            candidates=candidates,
            used_refs=used_refs,
            source_counts=source_counts,
            initiative_context=initiative_context,
        )
        self._append_workspace_context_item(
            candidates=candidates,
            used_refs=used_refs,
            source_counts=source_counts,
            factor_ref="ongoing_action:current",
            kind="ongoing_action",
            source="ongoing_action_summary",
            item=ongoing_action_summary,
            summary_keys=("reason_summary", "current_step_summary", "summary_text", "status"),
            metadata_keys=("action_id", "status", "capability_id"),
        )
        for index, item in enumerate(autonomous_run_summaries or []):
            if not isinstance(item, dict):
                continue
            run_id = self._workspace_item_ref(item, ("run_id",), fallback=f"run:{index}")
            self._append_workspace_context_item(
                candidates=candidates,
                used_refs=used_refs,
                source_counts=source_counts,
                factor_ref=f"autonomous_run:{run_id}",
                kind="autonomous_run",
                source="autonomous_run_summaries",
                item=item,
                summary_keys=("objective_summary", "current_step_summary", "history_summary", "status"),
                metadata_keys=("run_id", "status", "next_run_at"),
            )
        for index, item in enumerate(foreground_world_state or []):
            if not isinstance(item, dict):
                continue
            state_ref = self._workspace_item_ref(
                item,
                ("world_state_id", "state_id", "integration_key", "scope"),
                fallback=f"state:{index}",
            )
            self._append_workspace_context_item(
                candidates=candidates,
                used_refs=used_refs,
                source_counts=source_counts,
                factor_ref=f"world_state:{state_ref}",
                kind="world_state",
                source="foreground_world_state",
                item=item,
                summary_keys=("summary_text", "visual_summary_text", "reason_summary"),
                metadata_keys=("state_type", "scope", "summary_source", "confidence_hint", "salience_hint", "ttl_hint"),
            )
        self._append_workspace_activity_candidates(
            candidates=candidates,
            used_refs=used_refs,
            source_counts=source_counts,
            activity_context=activity_context,
        )
        for index, item in enumerate(drive_state_summary or []):
            if not isinstance(item, dict):
                continue
            drive_ref = self._workspace_item_ref(item, ("drive_id",), fallback=f"drive:{index}")
            self._append_workspace_context_item(
                candidates=candidates,
                used_refs=used_refs,
                source_counts=source_counts,
                factor_ref=f"drive_state:{drive_ref}",
                kind="drive_state",
                source="drive_state_summary",
                item=item,
                summary_keys=("summary_text",),
                metadata_keys=("drive_id", "drive_kind", "salience", "support_strength", "focus_scope_type", "focus_scope_key"),
            )
        for index, item in enumerate(capability_decision_view or []):
            if not isinstance(item, dict):
                continue
            if item.get("available") is not True:
                continue
            capability_id = self._workspace_item_ref(item, ("id",), fallback=f"capability:{index}")
            self._append_workspace_context_item(
                candidates=candidates,
                used_refs=used_refs,
                source_counts=source_counts,
                factor_ref=f"capability:{capability_id}",
                kind="capability",
                source="capability_decision_view",
                item=item,
                summary_keys=("what_it_does", "fresh_world_state_policy"),
                metadata_keys=("id", "kind", "risk_level", "readiness"),
            )
        self._append_workspace_context_item(
            candidates=candidates,
            used_refs=used_refs,
            source_counts=source_counts,
            factor_ref="visual_observation:current",
            kind="visual_observation",
            source="visual_observation_context",
            item=visual_observation_context,
            summary_keys=("visual_summary_text", "summary_text"),
            metadata_keys=("source", "source_kind", "source_owner", "image_interpreted"),
        )
        self._append_workspace_derived_context_candidates(
            candidates=candidates,
            used_refs=used_refs,
            source_counts=source_counts,
            self_state_context=self_state_context,
            relationship_context=relationship_context,
            prediction_error_context=prediction_error_context,
            default_mode_context=default_mode_context,
        )
        self._append_workspace_memory_candidates(
            candidates=candidates,
            used_refs=used_refs,
            source_counts=source_counts,
            recall_pack=recall_pack,
        )
        limited_candidates = self._limit_workspace_candidates(candidates)
        return {
            "workspace_candidates": limited_candidates,
            "selection_policy": {
                "primary_factor_count": 1,
                "supporting_factor_ref_limit": WORKSPACE_SUPPORTING_SELECTION_LIMIT,
                "suppressed_factor_limit": WORKSPACE_SUPPRESSED_SELECTION_LIMIT,
                "meaning_selection_owner": "decision_generation",
            },
            "candidate_count": len(limited_candidates),
            "total_candidate_count": len(candidates),
            "dropped_candidate_count": max(0, len(candidates) - len(limited_candidates)),
            "source_counts": source_counts,
            "state_boundary": "workspace_context は判断用の派生 view であり、events / episodes / memory_units / affect などの正本を更新しない。",
        }

    def _append_workspace_derived_context_candidates(
        self,
        *,
        candidates: list[dict[str, Any]],
        used_refs: set[str],
        source_counts: dict[str, int],
        self_state_context: dict[str, Any] | None,
        relationship_context: dict[str, Any] | None,
        prediction_error_context: dict[str, Any] | None,
        default_mode_context: dict[str, Any] | None,
    ) -> None:
        if isinstance(self_state_context, dict):
            for source_key in ("sensory_confidence", "agency_confidence"):
                entries = self_state_context.get(source_key)
                if not isinstance(entries, list):
                    continue
                for index, entry in enumerate(entries):
                    if not isinstance(entry, dict):
                        continue
                    self._append_workspace_context_item(
                        candidates=candidates,
                        used_refs=used_refs,
                        source_counts=source_counts,
                        factor_ref=f"self_state:{source_key}:{index}",
                        kind="self_state",
                        source=f"self_state_context.{source_key}",
                        item=entry,
                        summary_keys=("summary_text", "status_text", "error", "confidence_hint"),
                        metadata_keys=("channel", "source", "capability_id", "image_interpreted", "source_owner"),
                    )
            focus_stability = self_state_context.get("focus_stability")
            self._append_workspace_context_item(
                candidates=candidates,
                used_refs=used_refs,
                source_counts=source_counts,
                factor_ref="self_state:focus_stability",
                kind="self_state",
                source="self_state_context.focus_stability",
                item=focus_stability if isinstance(focus_stability, dict) else None,
                summary_keys=("summary_text", "status"),
                metadata_keys=("source", "status", "capability_id"),
            )
        if isinstance(relationship_context, dict):
            relationship_items = relationship_context.get("relationship_items")
            if isinstance(relationship_items, list):
                for index, item in enumerate(relationship_items):
                    if not isinstance(item, dict):
                        continue
                    item_ref = self._workspace_item_ref(item, ("item_ref",), fallback=str(index))
                    self._append_workspace_context_item(
                        candidates=candidates,
                        used_refs=used_refs,
                        source_counts=source_counts,
                        factor_ref=f"relationship:{item_ref}",
                        kind="relationship",
                        source=str(item.get("source") or "relationship_context"),
                        item=item,
                        summary_keys=("summary_text",),
                        metadata_keys=("source",),
                    )
            affect_items = relationship_context.get("affect_items")
            if isinstance(affect_items, list):
                for index, item in enumerate(affect_items):
                    if not isinstance(item, dict):
                        continue
                    self._append_workspace_context_item(
                        candidates=candidates,
                        used_refs=used_refs,
                        source_counts=source_counts,
                        factor_ref=f"relationship_affect:{index}",
                        kind="relationship",
                        source="relationship_context.affect_items",
                        item=item,
                        summary_keys=("summary_text", "affect_label"),
                        metadata_keys=("target_scope_type", "target_scope_key", "intensity", "confidence"),
                    )
        if isinstance(prediction_error_context, dict):
            signals = prediction_error_context.get("signals")
            if isinstance(signals, list):
                for index, signal in enumerate(signals):
                    if not isinstance(signal, dict):
                        continue
                    self._append_workspace_context_item(
                        candidates=candidates,
                        used_refs=used_refs,
                        source_counts=source_counts,
                        factor_ref=f"prediction_error:{index}",
                        kind="prediction_error",
                        source="prediction_error_context.signals",
                        item=signal,
                        summary_keys=("summary_text", "status_text", "error"),
                        metadata_keys=("changed", "previous_count", "current_count", "source_capability_id"),
                    )
        if isinstance(default_mode_context, dict):
            resurfacing_candidates = default_mode_context.get("resurfacing_candidates")
            if isinstance(resurfacing_candidates, list):
                for item in resurfacing_candidates:
                    if not isinstance(item, dict):
                        continue
                    item_ref = self._workspace_item_ref(item, ("candidate_ref",), fallback="candidate")
                    self._append_workspace_context_item(
                        candidates=candidates,
                        used_refs=used_refs,
                        source_counts=source_counts,
                        factor_ref=item_ref,
                        kind="default_mode",
                        source=str(item.get("source") or "default_mode_context"),
                        item=item,
                        summary_keys=("summary_text",),
                        metadata_keys=("source", "resurfacing_policy"),
                    )

    def _append_workspace_suppression_candidates(
        self,
        *,
        candidates: list[dict[str, Any]],
        used_refs: set[str],
        source_counts: dict[str, int],
        initiative_context: InitiativeContext | None,
    ) -> None:
        if initiative_context is None:
            return
        suppression_summary = initiative_context.suppression_summary
        if not isinstance(suppression_summary, dict):
            return
        if suppression_summary.get("visual_repetition_present") is not True:
            return
        self._append_workspace_candidate(
            candidates=candidates,
            used_refs=used_refs,
            source_counts=source_counts,
            factor_ref="suppression:visual_repetition",
            kind="suppression",
            source="initiative_context.suppression_summary",
            summary_text=(
                "視覚観測に same_as_recent_speech が含まれており、"
                "同じ内容を繰り返し主題化しないための控える候補。"
            ),
            metadata=self._workspace_metadata(
                suppression_summary,
                (
                    "suppression_level",
                    "visual_repetition_present",
                    "same_as_recent_speech_present",
                    "all_visual_observations_repeated",
                    "visual_observation_count",
                    "repeated_visual_observation_count",
                ),
            ),
        )

    def _append_workspace_visual_observation_candidates(
        self,
        *,
        candidates: list[dict[str, Any]],
        used_refs: set[str],
        source_counts: dict[str, int],
        initiative_context: InitiativeContext | None,
    ) -> None:
        if initiative_context is None:
            return
        foreground_signal_summary = initiative_context.foreground_signal_summary
        if not isinstance(foreground_signal_summary, dict):
            return
        visual_observations = foreground_signal_summary.get("visual_observations")
        if not isinstance(visual_observations, list):
            return
        for index, observation in enumerate(visual_observations):
            if not isinstance(observation, dict):
                continue
            if observation.get("change_state") not in {"first_seen", "changed"}:
                continue
            observation_ref = self._workspace_item_ref(
                observation,
                ("observation_id", "vision_source_id"),
                fallback=f"wake:{index}",
            )
            summary_text = self._workspace_item_summary(
                observation,
                ("reason_summary", "summary_text", "source_label", "source_kind"),
            )
            self._append_workspace_candidate(
                candidates=candidates,
                used_refs=used_refs,
                source_counts=source_counts,
                factor_ref=f"visual_observation_signal:{observation_ref}",
                kind="visual_observation",
                source="initiative_context.foreground_signal_summary.visual_observations",
                summary_text=summary_text,
                metadata=self._workspace_metadata(
                    observation,
                    (
                        "change_state",
                        "change_basis",
                        "same_as_recent_speech",
                        "vision_source_id",
                        "source_kind",
                        "source_label",
                        "source_owner",
                    ),
                ),
            )

    def _append_workspace_initiative_candidates(
        self,
        *,
        candidates: list[dict[str, Any]],
        used_refs: set[str],
        source_counts: dict[str, int],
        initiative_context: InitiativeContext | None,
    ) -> None:
        if initiative_context is None:
            return
        for family in initiative_context.candidate_families:
            summary_text = (
                family.reason_summary
                or family.preferred_result_reason_summary
                or family.blocking_reason_summary
                or family.family
            )
            self._append_workspace_candidate(
                candidates=candidates,
                used_refs=used_refs,
                source_counts=source_counts,
                factor_ref=f"initiative:{family.family}",
                kind="initiative_candidate",
                source="initiative_context",
                summary_text=summary_text,
                metadata={
                    "family": family.family,
                    "available": family.available,
                    "selected": family.selected,
                    "preferred_result_kind": family.preferred_result_kind,
                    "preferred_capability_id": family.preferred_capability_id,
                },
            )

    def _append_workspace_activity_candidates(
        self,
        *,
        candidates: list[dict[str, Any]],
        used_refs: set[str],
        source_counts: dict[str, int],
        activity_context: dict[str, Any] | None,
    ) -> None:
        if not isinstance(activity_context, dict):
            return
        self._append_workspace_activity_transition_candidate(
            candidates=candidates,
            used_refs=used_refs,
            source_counts=source_counts,
            activity_context=activity_context,
        )
        for key in ("current_activity", "previous_activity"):
            activity = activity_context.get(key)
            if not isinstance(activity, dict):
                continue
            summary_text = self._workspace_item_summary(
                activity,
                ("reason_summary", "label", "target", "actor"),
            )
            self._append_workspace_candidate(
                candidates=candidates,
                used_refs=used_refs,
                source_counts=source_counts,
                factor_ref=f"activity:{key}",
                kind="activity",
                source="activity_context",
                summary_text=summary_text,
                metadata=self._workspace_metadata(
                    activity,
                    (
                        "label",
                        "actor",
                        "target",
                        "transition",
                        "confidence",
                        "salience",
                        "started_age_label",
                        "duration_label",
                        "age_label",
                        "ended_age_label",
                    ),
                ),
            )

    def _append_workspace_activity_transition_candidate(
        self,
        *,
        candidates: list[dict[str, Any]],
        used_refs: set[str],
        source_counts: dict[str, int],
        activity_context: dict[str, Any],
    ) -> None:
        current_activity = activity_context.get("current_activity")
        previous_activity = activity_context.get("previous_activity")
        if not isinstance(current_activity, dict) or not isinstance(previous_activity, dict):
            return
        transition = self._workspace_text(current_activity.get("transition"))
        if transition not in {"start", "switch"}:
            return
        previous_summary = self._workspace_item_summary(
            previous_activity,
            ("reason_summary", "label", "target", "actor", "duration_label"),
        )
        current_summary = self._workspace_item_summary(
            current_activity,
            ("reason_summary", "label", "target", "actor"),
        )
        if previous_summary is None or current_summary is None:
            return
        duration_label = self._workspace_text(previous_activity.get("duration_label"))
        summary_parts = [
            f"直前までの活動: {previous_summary}",
            f"現在活動: {current_summary}",
            f"activity transition={transition}",
        ]
        if duration_label is not None:
            summary_parts.append(f"前活動の継続時間: {duration_label}")
        self._append_workspace_candidate(
            candidates=candidates,
            used_refs=used_refs,
            source_counts=source_counts,
            factor_ref="activity:transition",
            kind="activity_transition",
            source="activity_context",
            summary_text=" / ".join(summary_parts),
            metadata=self._workspace_metadata(
                {
                    "transition": transition,
                    "previous_label": previous_activity.get("label"),
                    "previous_actor": previous_activity.get("actor"),
                    "previous_target": previous_activity.get("target"),
                    "previous_started_age_label": previous_activity.get("started_age_label"),
                    "previous_duration_label": previous_activity.get("duration_label"),
                    "previous_ended_age_label": previous_activity.get("ended_age_label"),
                    "current_label": current_activity.get("label"),
                    "current_actor": current_activity.get("actor"),
                    "current_target": current_activity.get("target"),
                    "current_started_age_label": current_activity.get("started_age_label"),
                    "current_duration_label": current_activity.get("duration_label"),
                    "current_age_label": current_activity.get("age_label"),
                },
                (
                    "transition",
                    "previous_label",
                    "previous_actor",
                    "previous_target",
                    "previous_started_age_label",
                    "previous_duration_label",
                    "previous_ended_age_label",
                    "current_label",
                    "current_actor",
                    "current_target",
                    "current_started_age_label",
                    "current_duration_label",
                    "current_age_label",
                ),
            ),
        )

    def _append_workspace_memory_candidates(
        self,
        *,
        candidates: list[dict[str, Any]],
        used_refs: set[str],
        source_counts: dict[str, int],
        recall_pack: dict[str, Any],
    ) -> None:
        for section in WORKSPACE_MEMORY_SECTIONS:
            items = recall_pack.get(section)
            if not isinstance(items, list):
                continue
            for index, item in enumerate(items[:WORKSPACE_MEMORY_ITEMS_PER_SECTION]):
                if not isinstance(item, dict):
                    continue
                item_ref = self._workspace_item_ref(
                    item,
                    (
                        "memory_unit_id",
                        "episode_id",
                        "event_id",
                        "visual_observation_id",
                        "compare_key",
                    ),
                    fallback=f"{section}:{index}",
                )
                self._append_workspace_context_item(
                    candidates=candidates,
                    used_refs=used_refs,
                    source_counts=source_counts,
                    factor_ref=f"memory:{section}:{item_ref}",
                    kind="memory",
                    source=f"recall_pack.{section}",
                    item=item,
                    summary_keys=(
                        "summary_text",
                        "detailed_summary_text",
                        "anchor",
                        "topic",
                        "decision_or_result",
                        "tone_or_note",
                    ),
                    metadata_keys=("memory_type", "scope_type", "scope_key", "primary_scope_type", "primary_scope_key", "retrieval_lane"),
                )

    def _limit_workspace_candidates(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        limited = candidates[:WORKSPACE_CANDIDATE_LIMIT]
        if len(candidates) <= WORKSPACE_CANDIDATE_LIMIT:
            return limited
        kept_refs = {
            candidate["factor_ref"]
            for candidate in limited
            if isinstance(candidate, dict) and isinstance(candidate.get("factor_ref"), str)
        }
        for candidate in candidates[WORKSPACE_CANDIDATE_LIMIT:]:
            if not isinstance(candidate, dict):
                continue
            if candidate.get("kind") not in WORKSPACE_LIMIT_RETAINED_KINDS:
                continue
            factor_ref = candidate.get("factor_ref")
            if not isinstance(factor_ref, str) or factor_ref in kept_refs:
                continue
            limited.append(candidate)
            kept_refs.add(factor_ref)
        return limited

    def _append_workspace_context_item(
        self,
        *,
        candidates: list[dict[str, Any]],
        used_refs: set[str],
        source_counts: dict[str, int],
        factor_ref: str,
        kind: str,
        source: str,
        item: dict[str, Any] | None,
        summary_keys: tuple[str, ...],
        metadata_keys: tuple[str, ...],
    ) -> None:
        if not isinstance(item, dict):
            return
        summary_text = self._workspace_item_summary(item, summary_keys)
        self._append_workspace_candidate(
            candidates=candidates,
            used_refs=used_refs,
            source_counts=source_counts,
            factor_ref=factor_ref,
            kind=kind,
            source=source,
            summary_text=summary_text,
            metadata=self._workspace_metadata(item, metadata_keys),
        )

    def _append_workspace_candidate(
        self,
        *,
        candidates: list[dict[str, Any]],
        used_refs: set[str],
        source_counts: dict[str, int],
        factor_ref: str,
        kind: str,
        source: str,
        summary_text: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        text = self._workspace_text(summary_text)
        if text is None:
            return
        normalized_ref = self._unique_workspace_factor_ref(factor_ref, used_refs)
        candidate: dict[str, Any] = {
            "factor_ref": normalized_ref,
            "kind": kind,
            "source": source,
            "summary_text": text,
        }
        if metadata:
            candidate["metadata"] = metadata
        candidates.append(candidate)
        source_counts[source] = source_counts.get(source, 0) + 1

    def _workspace_item_summary(self, item: dict[str, Any], keys: tuple[str, ...]) -> str | None:
        parts: list[str] = []
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        if not parts:
            return None
        return " / ".join(parts)

    def _workspace_metadata(self, item: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any] | None:
        metadata: dict[str, Any] = {}
        for key in keys:
            value = item.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    metadata[key] = stripped
                continue
            if isinstance(value, (int, float, bool, list, dict)):
                metadata[key] = value
        return metadata or None

    def _workspace_item_ref(
        self,
        item: dict[str, Any],
        keys: tuple[str, ...],
        *,
        fallback: str,
    ) -> str:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return fallback

    def _unique_workspace_factor_ref(self, factor_ref: str, used_refs: set[str]) -> str:
        base_ref = factor_ref.strip() or "workspace:factor"
        if base_ref not in used_refs:
            used_refs.add(base_ref)
            return base_ref
        suffix = 2
        while f"{base_ref}:{suffix}" in used_refs:
            suffix += 1
        unique_ref = f"{base_ref}:{suffix}"
        used_refs.add(unique_ref)
        return unique_ref

    def _workspace_text(self, value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None

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
        activity_context: dict[str, Any] | None,
        ongoing_action_summary: dict[str, Any] | None,
        autonomous_run_summaries: list[dict[str, Any]] | None,
        capability_decision_view: list[dict[str, Any]] | None,
        agent_skill_context: dict[str, Any] | None,
        initiative_context: InitiativeContext | None,
        capability_result_context: dict[str, Any] | None,
        self_state_context: dict[str, Any] | None,
        people_context: list[dict[str, str]],
        relationship_context: dict[str, Any] | None,
        prediction_error_context: dict[str, Any] | None,
        default_mode_context: dict[str, Any] | None,
        workspace_context: dict[str, Any] | None,
        visual_observation_context: dict[str, Any] | None,
        reference_context: dict[str, Any] | None,
        recall_hint: dict[str, Any],
        recall_pack: dict[str, Any],
        model_config: dict[str, Any],
        persona_context: Any,
        cycle_label: str,
        pre_send_check_feedback: str | None = None,
    ) -> dict[str, Any]:
        # decision生成
        if self._should_compare_self_activity_separately(
            trigger_kind=trigger_kind,
            workspace_context=workspace_context,
            initiative_context=initiative_context,
        ):
            return self._run_separated_activity_decisions(
                input_text=input_text,
                current_input=current_input,
                trigger_kind=trigger_kind,
                recent_turns=recent_turns,
                time_context=time_context,
                affect_context=affect_context,
                drive_state_summary=drive_state_summary,
                foreground_world_state=foreground_world_state,
                activity_context=activity_context,
                ongoing_action_summary=ongoing_action_summary,
                autonomous_run_summaries=autonomous_run_summaries,
                capability_decision_view=capability_decision_view,
                agent_skill_context=agent_skill_context,
                initiative_context=initiative_context,
                capability_result_context=capability_result_context,
                visual_observation_context=visual_observation_context,
                self_state_context=self_state_context,
                people_context=people_context,
                relationship_context=relationship_context,
                prediction_error_context=prediction_error_context,
                default_mode_context=default_mode_context,
                workspace_context=workspace_context,
                recall_hint=recall_hint,
                recall_pack=recall_pack,
                reference_context=reference_context,
                model_config=model_config,
                persona_context=persona_context,
                cycle_label=cycle_label,
                pre_send_check_feedback=pre_send_check_feedback,
            )
        decision_context = self._build_decision_context(
            input_text=input_text,
            current_input=current_input,
            trigger_kind=trigger_kind,
            recent_turns=recent_turns,
            time_context=time_context,
            affect_context=affect_context,
            drive_state_summary=drive_state_summary,
            foreground_world_state=foreground_world_state,
            activity_context=activity_context,
            ongoing_action_summary=ongoing_action_summary,
            autonomous_run_summaries=autonomous_run_summaries,
            capability_decision_view=capability_decision_view,
            agent_skill_context=agent_skill_context,
            initiative_context=initiative_context,
            capability_result_context=capability_result_context,
            visual_observation_context=visual_observation_context,
            self_state_context=self_state_context,
            people_context=people_context,
            relationship_context=relationship_context,
            prediction_error_context=prediction_error_context,
            default_mode_context=default_mode_context,
            workspace_context=workspace_context,
            recall_hint=recall_hint,
            recall_pack=recall_pack,
            reference_context=reference_context,
            pre_send_check_feedback=pre_send_check_feedback,
        )
        decision = self.llm.generate_decision(
            model_config=model_config,
            persona_context=persona_context,
            context=decision_context,
        )
        debug_log(
            "Pipeline",
            f"{cycle_label} decision done kinds={self._decision_kind_log(decision)} reason={self._clamp(decision['reason_summary'])}",
        )
        return decision

    def _run_pipeline_output(
        self,
        *,
        state: dict[str, Any],
        cycle_id: str | None,
        input_text: str,
        current_input: CurrentInput,
        recent_turns: list[dict[str, Any]],
        time_context: dict[str, Any],
        affect_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        activity_context: dict[str, Any] | None,
        ongoing_action_summary: dict[str, Any] | None,
        initiative_context: InitiativeContext | None,
        agent_skill_context: dict[str, Any] | None,
        self_state_context: dict[str, Any] | None,
        people_context: list[dict[str, str]],
        relationship_context: dict[str, Any] | None,
        prediction_error_context: dict[str, Any] | None,
        workspace_context: dict[str, Any] | None,
        visual_observation_context: dict[str, Any] | None,
        reference_context: dict[str, Any] | None,
        recall_hint: dict[str, Any],
        recall_pack: dict[str, Any],
        model_config: dict[str, Any],
        persona_context: Any,
        decision: dict[str, Any],
        assistant_message_target_client_id: str | None,
        cycle_label: str,
        pre_send_check_attempt: int = 1,
        pre_send_check_prior_attempts: list[dict[str, Any]] | None = None,
        suppress_outward_speech: bool = False,
        suppress_outward_speech_reason: str | None = None,
    ) -> dict[str, Any]:
        self_decision = self._execution_self_decision(decision)
        outward_decision = self._execution_outward_decision(decision)
        dispatched_capability_request_summary: dict[str, Any] | None = None
        ongoing_action_transition_summary: dict[str, Any] | None = None
        autonomous_run_summary: dict[str, Any] | None = None
        autonomous_run_step_result: dict[str, Any] | None = None
        source_current_input = current_input.to_prompt_payload()
        agent_skill_activation = self._agent_skill_activation_summary(agent_skill_context)
        if agent_skill_activation is not None:
            source_current_input["agent_skill_activation"] = agent_skill_activation
        if self_decision.get("kind") == "capability_request":
            dispatch_result = self._dispatch_decision_capability_request(
                state=state,
                current_time=self._now_iso(),
                source_current_input=source_current_input,
                assistant_message_target_client_id=assistant_message_target_client_id,
                decision=self_decision,
                pre_send_check_attempt=pre_send_check_attempt,
                pre_send_check_prior_attempts=pre_send_check_prior_attempts,
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

        speech_payload: dict[str, Any] | None = None
        if self_decision.get("kind") == "autonomous_run":
            start_result = self._start_autonomous_run_from_decision(
                state=state,
                current_time=self._now_iso(),
                decision=self_decision,
                source_current_input=source_current_input,
                source_cycle_id=cycle_id,
                assistant_message_target_client_id=assistant_message_target_client_id,
            )
            run_payload = start_result.get("autonomous_run")
            if isinstance(run_payload, dict):
                autonomous_run_summary = self._autonomous_run_public_summary(
                    run_payload,
                    current_time=self._now_iso(),
                )
            step_result = start_result.get("step_result")
            if isinstance(step_result, dict):
                autonomous_run_step_result = step_result
                step_speech_payload = step_result.get("speech_payload")
                if isinstance(step_speech_payload, dict) and outward_decision.get("kind") != "speech":
                    speech_payload = step_speech_payload
                step_capability_request = step_result.get("capability_request_summary")
                if isinstance(step_capability_request, dict):
                    dispatched_capability_request_summary = step_capability_request
            debug_log(
                "Pipeline",
                (
                    f"{cycle_label} autonomous_run started "
                    f"run={autonomous_run_summary.get('run_id') if isinstance(autonomous_run_summary, dict) else '-'}"
                ),
            )
        speech_suppressed = (
            outward_decision.get("kind") == "speech"
            and current_input.source_kind == "capability_result"
            and not current_input.response_target_refs
        )
        if speech_suppressed:
            self._apply_outward_speech_suppression(
                decision,
                reason_code="capability_result_response_targets_empty",
                reason_summary=(
                    "capability result の source_current_input.response_target_refs が空のため、"
                    "内部観測結果として処理し assistant message を送信しない。"
                ),
            )
            debug_log("Pipeline", f"{cycle_label} speech skipped capability_result_response_targets_empty")
        elif outward_decision.get("kind") == "speech" and current_input.source_kind == "background_thinking" and (
            suppress_outward_speech or self._user_response_cycle_active()
        ):
            reason_summary = (
                suppress_outward_speech_reason
                if suppress_outward_speech and suppress_outward_speech_reason
                else "ユーザー向け応答サイクルが進行中のため、定期思考の自発発話は行わない。"
            )
            reason_code = (
                "background_thinking_user_response_changed"
                if suppress_outward_speech
                else "background_thinking_user_response_active"
            )
            self._apply_outward_speech_suppression(
                decision,
                reason_code=reason_code,
                reason_summary=reason_summary,
            )
            debug_log("Pipeline", f"{cycle_label} speech skipped {reason_code}")
        elif outward_decision.get("kind") == "speech":
            speech_context = self._build_speech_context(
                input_text=input_text,
                current_input=current_input,
                recent_turns=recent_turns,
                time_context=time_context,
                affect_context=affect_context,
                drive_state_summary=drive_state_summary,
                foreground_world_state=foreground_world_state,
                activity_context=activity_context,
                ongoing_action_summary=ongoing_action_summary,
                initiative_context=initiative_context,
                agent_skill_context=agent_skill_context,
                visual_observation_context=visual_observation_context,
                self_state_context=self_state_context,
                people_context=people_context,
                relationship_context=relationship_context,
                prediction_error_context=prediction_error_context,
                workspace_context=workspace_context,
                recall_hint=recall_hint,
                recall_pack=recall_pack,
                reference_context=reference_context,
                decision=outward_decision,
            )
            speech_payload = self.llm.generate_speech(
                model_config=model_config,
                persona_context=persona_context,
                context=speech_context,
            )
            speech_payload = self._apply_disclosure_review(
                model_config=model_config,
                persona_context=self._build_selected_persona_context(
                    state=state,
                    role="disclosure_review",
                ),
                current_input=current_input,
                recall_pack=recall_pack,
                speech_payload=speech_payload,
                decision=decision,
            )
            if speech_payload is None:
                debug_log("Pipeline", f"{cycle_label} speech withheld disclosure_review")
                return {
                    "speech_payload": None,
                    "capability_request_summary": dispatched_capability_request_summary,
                    "ongoing_action_transition_summary": ongoing_action_transition_summary,
                    "autonomous_run_summary": autonomous_run_summary,
                    "autonomous_run_step_result": autonomous_run_step_result,
                }
            debug_log("Pipeline", f"{cycle_label} speech done speech_chars={len(speech_payload['speech_text'])}")
            self._emit_live_log(
                level="INFO",
                component="Result",
                message=f"{cycle_label} speech done speech={self._conversation_log_excerpt(speech_payload['speech_text'])}",
            )
        elif speech_payload is not None:
            debug_log("Pipeline", f"{cycle_label} speech prepared kinds={self._decision_kind_log(decision)}")
        else:
            debug_log("Pipeline", f"{cycle_label} speech skipped kinds={self._decision_kind_log(decision)}")
        return {
            "speech_payload": speech_payload,
            "capability_request_summary": dispatched_capability_request_summary,
            "ongoing_action_transition_summary": ongoing_action_transition_summary,
            "autonomous_run_summary": autonomous_run_summary,
            "autonomous_run_step_result": autonomous_run_step_result,
        }

    def _apply_disclosure_review(
        self,
        *,
        model_config: dict[str, Any],
        persona_context: PersonaContext,
        current_input: CurrentInput,
        recall_pack: dict[str, Any],
        speech_payload: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any] | None:
        sensitive_sources = self._disclosure_review_sources(
            recall_pack=recall_pack,
            current_person_refs=set(current_input.participant_refs),
        )
        if not sensitive_sources:
            return speech_payload
        review = self.llm.generate_disclosure_review(
            model_config=model_config,
            review_context={
                "persona_context": persona_context.to_prompt_payload(),
                "current_input": current_input.to_prompt_payload(),
                "candidate_speech": speech_payload["speech_text"],
                "requires_response": current_input.sender_kind == "person",
                "other_person_sources": [
                    source["source_item"]
                    for source in sensitive_sources
                ],
            },
        )
        outcome = review["outcome"]
        audit = {
            "outcome": outcome,
            "reason_code": review["reason_code"],
            "reviewed_source_refs": [
                source["source_ref"]
                for source in sensitive_sources
            ],
        }
        if outcome == "withhold":
            withheld_reason = "他の人物に由来する記憶の開示判定で発話を見送った。"
            self._apply_outward_speech_suppression(
                decision,
                reason_code="disclosure_review_withheld",
                reason_summary=withheld_reason,
            )
            decision["disclosure_review"] = audit
            return None
        return {
            **speech_payload,
            "speech_text": review["speech_text"].strip(),
            "disclosure_review": audit,
        }

    def _disclosure_review_sources(
        self,
        *,
        recall_pack: dict[str, Any],
        current_person_refs: set[str],
    ) -> list[dict[str, Any]]:
        # 選択済み記憶の構造化provenanceだけでレビュー要否を決める。
        sources: list[dict[str, Any]] = []
        seen: set[str] = set()
        for section_name in (
            "person_model",
            "relationship_model",
            "active_topics",
            "active_commitments",
            "episodic_evidence",
        ):
            section = recall_pack.get(section_name)
            if not isinstance(section, list):
                continue
            for index, item in enumerate(section):
                if not isinstance(item, dict):
                    continue
                qualifiers = item.get("qualifiers")
                source_participant_refs = item.get("source_participant_refs")
                if not isinstance(source_participant_refs, list) and isinstance(qualifiers, dict):
                    source_participant_refs = qualifiers.get("source_participant_refs")
                if not isinstance(source_participant_refs, list):
                    continue
                other_refs = {
                    value
                    for value in source_participant_refs
                    if isinstance(value, str) and value.startswith("person:")
                } - current_person_refs
                if not other_refs:
                    continue
                source_ref = (
                    item.get("memory_unit_id")
                    or item.get("episode_id")
                    or f"{section_name}:{index}"
                )
                source_ref = str(source_ref)
                if source_ref in seen:
                    continue
                seen.add(source_ref)
                sources.append(
                    {
                        "source_ref": source_ref,
                        "source_item": item,
                    }
                )
        return sources

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
        activity_context: dict[str, Any] | None,
        ongoing_action_summary: dict[str, Any] | None,
        autonomous_run_summaries: list[dict[str, Any]] | None,
        capability_decision_view: list[dict[str, Any]] | None,
        agent_skill_context: dict[str, Any] | None,
        initiative_context: InitiativeContext | None,
        capability_result_context: dict[str, Any] | None,
        visual_observation_context: dict[str, Any] | None,
        self_state_context: dict[str, Any] | None,
        people_context: list[dict[str, str]],
        relationship_context: dict[str, Any] | None,
        prediction_error_context: dict[str, Any] | None,
        default_mode_context: dict[str, Any] | None,
        workspace_context: dict[str, Any] | None,
        recall_hint: dict[str, Any],
        recall_pack: dict[str, Any],
        reference_context: dict[str, Any] | None = None,
        pre_send_check_feedback: str | None = None,
        comparison_scope: str = "full",
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
            activity_context=activity_context,
            ongoing_action_summary=ongoing_action_summary,
            autonomous_run_summaries=autonomous_run_summaries,
            capability_decision_view=capability_decision_view,
            agent_skill_context=agent_skill_context,
            initiative_context=initiative_context,
            capability_result_context=capability_result_context,
            visual_observation_context=visual_observation_context,
            self_state_context=self_state_context,
            people_context=people_context,
            relationship_context=relationship_context,
            prediction_error_context=prediction_error_context,
            default_mode_context=default_mode_context,
            workspace_context=workspace_context,
            recall_hint=recall_hint,
            recall_pack=recall_pack,
            reference_context=reference_context,
            pre_send_check_feedback=pre_send_check_feedback,
            comparison_scope=comparison_scope,
        )

    def _build_speech_context(
        self,
        *,
        input_text: str,
        current_input: CurrentInput,
        recent_turns: list[dict[str, Any]],
        time_context: dict[str, Any],
        affect_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        activity_context: dict[str, Any] | None,
        ongoing_action_summary: dict[str, Any] | None,
        initiative_context: InitiativeContext | None,
        visual_observation_context: dict[str, Any] | None,
        self_state_context: dict[str, Any] | None,
        people_context: list[dict[str, str]],
        relationship_context: dict[str, Any] | None,
        prediction_error_context: dict[str, Any] | None,
        workspace_context: dict[str, Any] | None,
        recall_hint: dict[str, Any],
        recall_pack: dict[str, Any],
        decision: dict[str, Any],
        agent_skill_context: dict[str, Any] | None,
        reference_context: dict[str, Any] | None = None,
    ) -> SpeechContext:
        return SpeechContext(
            input_text=input_text,
            current_input=current_input,
            recent_turns=recent_turns,
            time_context=time_context,
            affect_context=affect_context,
            drive_state_summary=drive_state_summary,
            foreground_world_state=foreground_world_state,
            activity_context=activity_context,
            ongoing_action_summary=ongoing_action_summary,
            initiative_context=initiative_context,
            visual_observation_context=visual_observation_context,
            self_state_context=self_state_context,
            people_context=people_context,
            relationship_context=relationship_context,
            prediction_error_context=prediction_error_context,
            workspace_context=workspace_context,
            recall_hint=recall_hint,
            recall_pack=recall_pack,
            reference_context=reference_context,
            decision=decision,
            agent_skill_context=agent_skill_context,
        )
