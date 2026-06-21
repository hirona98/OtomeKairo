from __future__ import annotations
from typing import Any

from otomekairo.llm.contexts import InitiativeContext
from otomekairo.service.capability import CapabilityDispatchError
from otomekairo.service.input.source_owner import visual_source_owner
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
        speech_payload = pipeline["speech_payload"]
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
        result_kind = self._external_result_kind(
            internal_result_kind,
            speech_payload=speech_payload,
            capability_request_summary=capability_request_summary,
        )
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
            activity_context=pipeline.get("activity_context"),
            activity_trace=pipeline.get("activity_trace"),
            ongoing_action_summary=pipeline.get("ongoing_action_summary"),
            decision=decision,
            result_kind=result_kind,
            speech_payload=speech_payload,
            pending_intent_summary=pending_intent_summary,
            capability_decision_view=pipeline.get("capability_decision_view"),
            initiative_context=pipeline.get("initiative_context"),
            capability_result_context=pipeline.get("capability_result_context"),
            visual_observation_context=pipeline.get("visual_observation_context"),
            workspace_context=pipeline.get("workspace_context"),
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
            speech_payload=speech_payload,
            pending_intent_selection=pending_intent_selection,
        )

        # memory trace更新
        memory_trace: dict[str, Any] | None = None
        if consolidate_memory:
            memory_trace = self._finalize_memory_trace(
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
            self._link_autonomous_run_source_commitments(
                state=state,
                pipeline=pipeline,
                memory_trace=memory_trace,
                current_time=finished_at,
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
            "speech": {"text": speech_payload["speech_text"]} if speech_payload else None,
            "capability_request": capability_request_summary if isinstance(capability_request_summary, dict) else None,
            "autonomous_run": pipeline.get("autonomous_run_summary")
            if isinstance(pipeline.get("autonomous_run_summary"), dict)
            else None,
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
        activity_context: dict[str, Any] | None,
        activity_trace: dict[str, Any] | None,
        ongoing_action_summary: dict[str, Any] | None,
        decision: dict[str, Any],
        result_kind: str,
        speech_payload: dict[str, Any] | None,
        pending_intent_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
        initiative_context: InitiativeContext | None,
        capability_result_context: dict[str, Any] | None,
        visual_observation_context: dict[str, Any] | None,
        workspace_context: dict[str, Any] | None,
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
            speech_payload=speech_payload,
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
                activity_context=activity_context,
                ongoing_action_summary=ongoing_action_summary,
                capability_decision_view=capability_decision_view,
                initiative_context=initiative_context,
                capability_result_context=capability_result_context,
                visual_observation_context=visual_observation_context,
                workspace_context=workspace_context,
                recall_pack=recall_pack,
                decision=decision,
                pending_intent_summary=pending_intent_summary,
            ),
            world_state_trace=world_state_trace,
            activity_trace=activity_trace,
            result_trace=self._build_success_result_trace(
                trigger_kind=trigger_kind,
                input_text=input_text,
                started_at=started_at,
                finished_at=finished_at,
                decision=decision,
                result_kind=result_kind,
                speech_payload=speech_payload,
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
            capability_request_summary=capability_request_summary,
            ongoing_action_summary=ongoing_action_summary,
            initiative_context=initiative_context,
        )
        visual_observation_records = self._build_visual_observation_records(
            cycle_id=cycle_id,
            memory_set_id=memory_set_id,
            observed_at=started_at,
            client_context=client_context,
            observation_summary=observation_summary,
        )
        self.store.persist_cycle_records(
            events=events,
            retrieval_run=retrieval_run,
            cycle_summary=cycle_summary,
            cycle_trace=cycle_trace,
            visual_observation_records=visual_observation_records,
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
            activity_trace=None,
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
            capability_request_summary=capability_request_summary,
            ongoing_action_summary=ongoing_action_summary,
            initiative_context=initiative_context,
        )
        visual_observation_records = self._build_visual_observation_records(
            cycle_id=cycle_id,
            memory_set_id=memory_set_id,
            observed_at=started_at,
            client_context=client_context,
            observation_summary=observation_summary,
        )
        self.store.persist_cycle_records(
            events=events,
            retrieval_run=retrieval_run,
            cycle_summary=cycle_summary,
            cycle_trace=cycle_trace,
            visual_observation_records=visual_observation_records,
        )

    def _build_visual_observation_records(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        observed_at: str,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        # 視覚説明が成功している入力だけ永続視覚記録にする。
        if not isinstance(observation_summary, dict):
            return []
        if observation_summary.get("image_interpreted") is not True:
            return []
        detailed_summary_text = observation_summary.get("visual_summary_text")
        if not isinstance(detailed_summary_text, str) or not detailed_summary_text.strip():
            return []

        image_input_kind = observation_summary.get("image_input_kind")
        if not isinstance(image_input_kind, str) or not image_input_kind.strip():
            return []

        source_kind = observation_summary.get("source_kind")
        if not isinstance(source_kind, str) or not source_kind.strip():
            source_value = observation_summary.get("source")
            if not isinstance(source_value, str) or not source_value.strip():
                return []
            source_kind = source_value

        visual_observation_id = observation_summary.get("visual_observation_id")
        if not isinstance(visual_observation_id, str) or not visual_observation_id.strip():
            return []

        source_label = observation_summary.get("source_label")
        vision_source_id = observation_summary.get("vision_source_id")
        confidence_hint = observation_summary.get("visual_confidence_hint")
        active_app = client_context.get("active_app")
        window_title = client_context.get("window_title")

        record = {
            "visual_observation_id": visual_observation_id.strip(),
            "memory_set_id": memory_set_id,
            "cycle_id": cycle_id,
            "observed_at": observed_at,
            "source_kind": source_kind.strip(),
            "source_owner": visual_source_owner(source_kind),
            "source_label": (
                source_label.strip()
                if isinstance(source_label, str) and source_label.strip()
                else None
            ),
            "vision_source_id": (
                vision_source_id.strip()
                if isinstance(vision_source_id, str) and vision_source_id.strip()
                else None
            ),
            "image_input_kind": image_input_kind.strip(),
            "detailed_summary_text": detailed_summary_text.strip(),
            "confidence_hint": (
                confidence_hint.strip()
                if isinstance(confidence_hint, str) and confidence_hint.strip()
                else None
            ),
            "scene_entities": [],
            "activity_labels": [],
            "environment_labels": [],
            "uncertainty_notes": [],
            "redaction_notes": [],
            "related_cycle_id": cycle_id,
            "related_episode_id": None,
            "duplicate_group_id": None,
            "importance_score": self._visual_observation_importance_score(
                trigger_kind=image_input_kind.strip(),
                observation_summary=observation_summary,
            ),
            "retention_status": "active",
            "index": {
                "short_summary_text": detailed_summary_text.strip(),
                "searchable_terms": self._visual_observation_searchable_terms(
                    observation_summary=observation_summary,
                    client_context=client_context,
                ),
                "embedding_text": detailed_summary_text.strip(),
            },
            "client_context_summary": {
                "active_app": active_app.strip() if isinstance(active_app, str) and active_app.strip() else None,
                "window_title": window_title.strip() if isinstance(window_title, str) and window_title.strip() else None,
            },
        }
        return [record]

    def _visual_observation_importance_score(
        self,
        *,
        trigger_kind: str,
        observation_summary: dict[str, Any],
    ) -> float:
        # 会話添付画像はユーザー関心が明示されているため高めにする。
        if trigger_kind == "conversation_attachment":
            return 0.72
        if self._observation_summary_is_vision_capture(observation_summary):
            return 0.48
        return 0.4

    def _visual_observation_searchable_terms(
        self,
        *,
        observation_summary: dict[str, Any],
        client_context: dict[str, Any],
    ) -> list[str]:
        terms: list[str] = []
        for source in (observation_summary, client_context):
            for key in ("source_kind", "source_label", "vision_source_id", "active_app", "window_title"):
                value = source.get(key)
                if isinstance(value, str) and value.strip() and value.strip() not in terms:
                    terms.append(value.strip())
        return terms

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
