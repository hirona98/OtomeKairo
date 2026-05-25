from __future__ import annotations

from typing import Any

from otomekairo.llm.contexts import InitiativeContext
from otomekairo.service.capability import CapabilityDispatchError
from otomekairo.world_state.models import WorldStateTrace


class ServiceInputTracePersistMixin:
    def _complete_input_success(
        self,
        *,
        cycle_id: str,
        started_at: str,
        state: dict[str, Any],
        runtime_summary: dict[str, Any],
        input_text: str,
        client_context: dict[str, Any],
        pipeline: dict[str, Any],
        trigger_kind: str = "user_message",
        input_event_kind: str = "conversation_input",
        input_event_role: str = "user",
        consolidate_memory: bool = True,
        pending_intent_selection: dict[str, Any] | None = None,
        observation_summary: dict[str, Any] | None = None,
        capability_request_summary: dict[str, Any] | None = None,
        ongoing_action_transition_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # 結果選択
        decision = pipeline["decision"]
        reply_payload = pipeline["reply_payload"]
        if capability_request_summary is None:
            candidate_summary = pipeline.get("capability_request_summary")
            if isinstance(candidate_summary, dict):
                capability_request_summary = candidate_summary
        if ongoing_action_transition_summary is None:
            candidate_transition = pipeline.get("ongoing_action_transition_summary")
            if isinstance(candidate_transition, dict):
                ongoing_action_transition_summary = candidate_transition
        followup_capability_request_summary = pipeline.get("capability_request_summary")
        if not isinstance(followup_capability_request_summary, dict):
            followup_capability_request_summary = None
        internal_result_kind = decision["kind"]
        result_kind = self._external_result_kind(internal_result_kind)
        finished_at = self._now_iso()
        pending_intent_summary = self._apply_pending_intent_candidate(
            cycle_id=cycle_id,
            memory_set_id=state["selected_memory_set_id"],
            decision=decision,
            occurred_at=finished_at,
        )

        # 永続化
        events = self._persist_cycle_success(
            cycle_id=cycle_id,
            started_at=started_at,
            finished_at=finished_at,
            state=state,
            runtime_summary=runtime_summary,
            input_text=input_text,
            augmented_query_text=pipeline.get("augmented_query_text"),
            client_context=client_context,
            recall_hint=pipeline["recall_hint"],
            recall_pack=pipeline["recall_pack"],
            time_context=pipeline["time_context"],
            affect_context=pipeline["affect_context"],
            drive_state_summary=pipeline.get("drive_state_summary"),
            foreground_world_state=pipeline.get("foreground_world_state"),
            ongoing_action_summary=pipeline.get("ongoing_action_summary"),
            decision=decision,
            result_kind=result_kind,
            reply_payload=reply_payload,
            pending_intent_summary=pending_intent_summary,
            capability_decision_view=pipeline.get("capability_decision_view"),
            initiative_context=pipeline.get("initiative_context"),
            capability_result_context=pipeline.get("capability_result_context"),
            visual_observation_context=pipeline.get("visual_observation_context"),
            world_state_trace=pipeline.get("world_state_trace"),
            trigger_kind=trigger_kind,
            input_event_kind=input_event_kind,
            input_event_role=input_event_role,
            pending_intent_selection=pending_intent_selection,
            observation_summary=observation_summary,
            capability_request_summary=capability_request_summary,
            followup_capability_request_summary=followup_capability_request_summary,
            ongoing_action_transition_summary=ongoing_action_transition_summary,
        )

        # デバッグログ群
        self._emit_input_success_logs(
            cycle_id=cycle_id,
            trigger_kind=trigger_kind,
            input_text=input_text,
            pipeline=pipeline,
            result_kind=result_kind,
            reply_payload=reply_payload,
            pending_intent_selection=pending_intent_selection,
        )

        # memory trace更新
        if consolidate_memory:
            self._finalize_memory_trace(
                cycle_id=cycle_id,
                finished_at=finished_at,
                state=state,
                input_text=input_text,
                events=events,
                pipeline=pipeline,
                trigger_kind=trigger_kind,
                input_event_kind=input_event_kind,
                input_event_role=input_event_role,
                pending_intent_summary=pending_intent_summary,
                pending_intent_selection=pending_intent_selection,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
                followup_capability_request_summary=followup_capability_request_summary,
                ongoing_action_transition_summary=ongoing_action_transition_summary,
            )
        else:
            skipped_memory_trace = self._skipped_memory_trace(f"{trigger_kind}_cycle")
            self._update_cycle_trace_memory_trace(
                cycle_id=cycle_id,
                memory_trace=skipped_memory_trace,
            )
            self._emit_memory_trace_logs(
                cycle_id=cycle_id,
                memory_trace=skipped_memory_trace,
            )

        # 応答
        return {
            "cycle_id": cycle_id,
            "result_kind": result_kind,
            "reply": {"text": reply_payload["reply_text"]} if reply_payload else None,
            "capability_request": capability_request_summary if isinstance(capability_request_summary, dict) else None,
        }

    def _persist_cycle_success(
        self,
        *,
        cycle_id: str,
        started_at: str,
        finished_at: str,
        state: dict[str, Any],
        runtime_summary: dict[str, Any],
        input_text: str,
        augmented_query_text: str | None,
        client_context: dict[str, Any],
        recall_hint: dict[str, Any],
        recall_pack: dict[str, Any],
        time_context: dict[str, Any],
        affect_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        ongoing_action_summary: dict[str, Any] | None,
        decision: dict[str, Any],
        result_kind: str,
        reply_payload: dict[str, Any] | None,
        pending_intent_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
        initiative_context: InitiativeContext | None,
        capability_result_context: dict[str, Any] | None,
        visual_observation_context: dict[str, Any] | None,
        world_state_trace: WorldStateTrace | None,
        trigger_kind: str,
        input_event_kind: str,
        input_event_role: str,
        pending_intent_selection: dict[str, Any] | None = None,
        observation_summary: dict[str, Any] | None = None,
        capability_request_summary: dict[str, Any] | None = None,
        followup_capability_request_summary: dict[str, Any] | None = None,
        ongoing_action_transition_summary: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        memory_set_id = state["selected_memory_set_id"]
        events = self._build_cycle_events(
            cycle_id=cycle_id,
            memory_set_id=memory_set_id,
            input_event_kind=input_event_kind,
            input_event_role=input_event_role,
            input_text=input_text,
            started_at=started_at,
            finished_at=finished_at,
            decision=decision,
            result_kind=result_kind,
            reply_payload=reply_payload,
            pending_intent_summary=pending_intent_summary,
        )
        events.extend(
            self._build_event_evidence_audit_events(
                cycle_id=cycle_id,
                memory_set_id=memory_set_id,
                created_at=finished_at,
                recall_pack=recall_pack,
            )
        )
        retrieval_run = self._build_retrieval_run_success(
            cycle_id=cycle_id,
            memory_set_id=memory_set_id,
            started_at=started_at,
            finished_at=finished_at,
            recall_hint=recall_hint,
            recall_pack=recall_pack,
        )
        cycle_summary = self._build_cycle_summary(
            cycle_id=cycle_id,
            started_at=started_at,
            finished_at=finished_at,
            state=state,
            trigger_kind=trigger_kind,
            result_kind=result_kind,
            failed=False,
        )
        cycle_trace = self._build_cycle_trace(
            cycle_id=cycle_id,
            cycle_summary=cycle_summary,
            input_text=input_text,
            augmented_query_text=augmented_query_text,
            client_context=client_context,
            runtime_summary=runtime_summary,
            foreground_world_state=foreground_world_state,
            recall_trace=self._build_success_recall_trace(recall_hint, recall_pack),
            decision_trace=self._build_success_decision_trace(
                state=state,
                input_text=input_text,
                augmented_query_text=augmented_query_text,
                time_context=time_context,
                affect_context=affect_context,
                drive_state_summary=drive_state_summary,
                foreground_world_state=foreground_world_state,
                ongoing_action_summary=ongoing_action_summary,
                capability_decision_view=capability_decision_view,
                initiative_context=initiative_context,
                capability_result_context=capability_result_context,
                visual_observation_context=visual_observation_context,
                recall_pack=recall_pack,
                decision=decision,
                pending_intent_summary=pending_intent_summary,
            ),
            world_state_trace=world_state_trace,
            result_trace=self._build_success_result_trace(
                trigger_kind=trigger_kind,
                input_text=input_text,
                started_at=started_at,
                finished_at=finished_at,
                decision=decision,
                result_kind=result_kind,
                reply_payload=reply_payload,
                pending_intent_summary=pending_intent_summary,
                pending_intent_selection=pending_intent_selection,
                initiative_context=initiative_context,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
                followup_capability_request_summary=followup_capability_request_summary,
                ongoing_action_transition_summary=ongoing_action_transition_summary,
            ),
            memory_trace=self._pending_memory_trace(),
            pending_intent_selection=pending_intent_selection,
            observation_summary=observation_summary,
            ongoing_action_summary=ongoing_action_summary,
            initiative_context=initiative_context,
        )
        self.store.persist_cycle_records(
            events=events,
            retrieval_run=retrieval_run,
            cycle_summary=cycle_summary,
            cycle_trace=cycle_trace,
        )
        return events

    def _persist_cycle_failure(
        self,
        *,
        cycle_id: str,
        started_at: str,
        finished_at: str,
        state: dict[str, Any],
        runtime_summary: dict[str, Any],
        input_text: str,
        client_context: dict[str, Any],
        failure_reason: str,
        trigger_kind: str = "user_message",
        input_event_kind: str = "conversation_input",
        input_event_role: str = "user",
        recall_trace: dict[str, Any] | None = None,
        failure_event_kind: str = "recall_hint_failure",
        failure_event_payload: dict[str, Any] | None = None,
        pending_intent_selection: dict[str, Any] | None = None,
        drive_state_summary: list[dict[str, Any]] | None = None,
        ongoing_action_summary: dict[str, Any] | None = None,
        capability_decision_view: list[dict[str, Any]] | None = None,
        initiative_context: InitiativeContext | None = None,
        observation_summary: dict[str, Any] | None = None,
        capability_request_summary: dict[str, Any] | None = None,
        followup_capability_request_summary: dict[str, Any] | None = None,
        ongoing_action_transition_summary: dict[str, Any] | None = None,
    ) -> None:
        memory_set_id = state["selected_memory_set_id"]
        events = self._build_cycle_events(
            cycle_id=cycle_id,
            memory_set_id=memory_set_id,
            input_event_kind=input_event_kind,
            input_event_role=input_event_role,
            input_text=input_text,
            started_at=started_at,
            finished_at=finished_at,
            failure_reason=failure_reason,
            failure_event_kind=failure_event_kind,
            failure_event_payload=failure_event_payload,
        )
        retrieval_run = self._build_retrieval_run_failure(
            cycle_id=cycle_id,
            memory_set_id=memory_set_id,
            started_at=started_at,
            finished_at=finished_at,
            failure_reason=failure_reason,
        )
        cycle_summary = self._build_cycle_summary(
            cycle_id=cycle_id,
            started_at=started_at,
            finished_at=finished_at,
            state=state,
            trigger_kind=trigger_kind,
            result_kind="internal_failure",
            failed=True,
        )
        cycle_trace = self._build_cycle_trace(
            cycle_id=cycle_id,
            cycle_summary=cycle_summary,
            input_text=input_text,
            augmented_query_text=None,
            client_context=client_context,
            runtime_summary=runtime_summary,
            foreground_world_state=None,
            recall_trace=recall_trace or self._build_failure_recall_trace(),
            decision_trace=self._build_failure_decision_trace(
                state=state,
                input_text=input_text,
                failure_reason=failure_reason,
                drive_state_summary=drive_state_summary,
                ongoing_action_summary=ongoing_action_summary,
                capability_decision_view=capability_decision_view,
                initiative_context=initiative_context,
            ),
            world_state_trace=None,
            result_trace=self._build_failure_result_trace(
                trigger_kind=trigger_kind,
                input_text=input_text,
                started_at=started_at,
                finished_at=finished_at,
                failure_reason=failure_reason,
                pending_intent_selection=pending_intent_selection,
                initiative_context=initiative_context,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
                followup_capability_request_summary=followup_capability_request_summary,
                ongoing_action_transition_summary=ongoing_action_transition_summary,
            ),
            memory_trace={},
            pending_intent_selection=pending_intent_selection,
            observation_summary=observation_summary,
            ongoing_action_summary=ongoing_action_summary,
            initiative_context=initiative_context,
        )
        self.store.persist_cycle_records(
            events=events,
            retrieval_run=retrieval_run,
            cycle_summary=cycle_summary,
            cycle_trace=cycle_trace,
        )

    def _exception_capability_dispatch_trace(
        self,
        exc: Exception,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if not isinstance(exc, CapabilityDispatchError):
            return None, None
        capability_request_summary = exc.capability_request_summary
        ongoing_action_transition_summary = exc.ongoing_action_transition_summary
        if not isinstance(capability_request_summary, dict):
            capability_request_summary = None
        if not isinstance(ongoing_action_transition_summary, dict):
            ongoing_action_transition_summary = None
        return capability_request_summary, ongoing_action_transition_summary
