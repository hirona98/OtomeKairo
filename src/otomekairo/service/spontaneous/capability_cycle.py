from __future__ import annotations

import threading
from typing import Any

from otomekairo.llm.client import LLMError
from otomekairo.recall.builder import RecallPackSelectionError
from otomekairo.service.common import ServiceError, debug_log


class ServiceSpontaneousCapabilityCycleMixin:
    def _submit_async_capability_result_response(
        self,
        *,
        state: dict[str, Any],
        capability_id: str,
        request_id: str,
        client_id: str,
        result_payload: dict[str, Any],
        accepted_at: str,
        log_channel: str,
        accepted_detail: str,
    ) -> dict[str, Any]:
        try:
            response = self._accept_capability_result(
                capability_id=capability_id,
                request_id=request_id,
                client_id=client_id,
                result_payload=result_payload,
                current_time=accepted_at,
            )
        except ValueError as exc:
            cooldown_seconds = int(self._capability_state_policy(capability_id).get("error_cooldown_seconds") or 0)
            self._mark_capability_runtime_failure(
                capability_id=capability_id,
                current_time=accepted_at,
                failure_summary=str(exc),
                cooldown_seconds=cooldown_seconds,
            )
            if "client_id" in str(exc):
                raise ServiceError(
                    409,
                    "capability_result_client_id_mismatch",
                    "client_id does not match the pending capability target.",
                ) from exc
            raise ServiceError(400, "invalid_capability_result", str(exc)) from exc
        if response is None:
            debug_log(
                log_channel,
                f"capability response ignored request={request_id} capability={capability_id} client={client_id}",
            )
            return {}
        debug_log(
            log_channel,
            (
                f"capability response accepted request={request_id} capability={capability_id} client={client_id} "
                f"{accepted_detail}"
            ),
        )
        request_record = response.get("request_record")
        if isinstance(request_record, dict) and request_record.get("wait_for_response"):
            return {}
        self._start_async_capability_result_cycle(
            state=state,
            capability_response=response,
            started_at=accepted_at,
        )

        return {}

    def _start_async_capability_result_cycle(
        self,
        *,
        state: dict[str, Any],
        capability_response: dict[str, Any],
        started_at: str,
    ) -> None:
        capability_id = self._capability_result_capability_id(capability_response)
        request_record = capability_response.get("request_record")
        request_id = request_record.get("request_id") if isinstance(request_record, dict) else None
        request_label = request_id if isinstance(request_id, str) and request_id.strip() else "-"

        def run_cycle() -> None:
            try:
                self._execute_async_capability_result_cycle(
                    state=state,
                    capability_response=capability_response,
                    started_at=started_at,
                )
            except Exception as exc:  # noqa: BLE001
                debug_log(
                    "CapabilityResult",
                    (
                        f"async cycle crashed request={request_label} capability={capability_id} "
                        f"error={type(exc).__name__}: {self._clamp(str(exc))}"
                    ),
                )
                cooldown_seconds = int(self._capability_state_policy(capability_id).get("error_cooldown_seconds") or 0)
                self._mark_capability_runtime_failure(
                    capability_id=capability_id,
                    current_time=self._now_iso(),
                    failure_summary=str(exc),
                    cooldown_seconds=cooldown_seconds,
                )

        thread = threading.Thread(
            target=run_cycle,
            name="otomekairo-capability-result",
            daemon=True,
        )
        thread.start()
        debug_log(
            "CapabilityResult",
            f"async cycle queued request={request_label} capability={capability_id}",
        )

    def _execute_async_capability_result_cycle(
        self,
        *,
        state: dict[str, Any],
        capability_response: dict[str, Any],
        started_at: str,
    ) -> None:
        request_record = capability_response.get("request_record")
        capability_id = self._capability_result_capability_id(capability_response)
        image_count = self._capability_result_payload_image_count(capability_response)
        capability_request_summary = self._capability_request_summary(request_record)
        self._activate_capability_ongoing_action(
            request_record=request_record,
            current_time=started_at,
            active_step_summary=self._capability_result_active_step_summary(
                capability_id=capability_id,
                result_payload=capability_response,
            ),
        )
        cycle_id = self._new_cycle_id()
        recent_turns = self._load_recent_turns(state)
        runtime_summary = self._build_runtime_summary(state)
        pending_intent_selection = self._empty_pending_intent_selection_trace()
        client_context = self._build_capability_result_client_context(capability_response)
        observation_summary = self._capability_result_observation_summary(capability_response)
        input_text = self._build_capability_result_input_text(
            client_context=client_context,
            capability_response=capability_response,
        )
        pipeline: dict[str, Any] | None = None
        ongoing_action_transition_summary: dict[str, Any] | None = None
        image_count_summary = f"images={image_count} " if image_count is not None else ""
        debug_log(
            "CapabilityResult",
            (
                f"{self._short_cycle_id(cycle_id)} start capability={capability_id} "
                f"recent_turns={len(recent_turns)} {image_count_summary}"
                f"error={bool(capability_response.get('error'))}"
            ),
        )

        try:
            client_context, observation_summary, input_text = self._prepare_capability_result_context(
                state=state,
                started_at=started_at,
                capability_id=capability_id,
                client_context=client_context,
                observation_summary=observation_summary,
                input_text=input_text,
                capability_response=capability_response,
            )
            pipeline = self._run_input_pipeline(
                state=state,
                started_at=started_at,
                input_text=input_text,
                recent_turns=recent_turns,
                cycle_id=cycle_id,
                trigger_kind="capability_result",
                client_context=client_context,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
            )
            ongoing_action_transition_summary = self._resolve_capability_result_followup_ongoing_action(
                request_record=request_record,
                current_time=self._now_iso(),
                capability_id=capability_id,
                result_payload=capability_response,
                decision=pipeline["decision"],
                pipeline_ongoing_action_transition=pipeline.get("ongoing_action_transition_summary"),
            )
            response = self._complete_input_success(
                cycle_id=cycle_id,
                started_at=started_at,
                state=state,
                runtime_summary=runtime_summary,
                input_text=input_text,
                client_context=client_context,
                pipeline=pipeline,
                trigger_kind="capability_result",
                input_event_kind="capability_result",
                input_event_role="system",
                consolidate_memory=self._should_consolidate_spontaneous_cycle(
                    trigger_kind="capability_result",
                    pipeline=pipeline,
                    observation_summary=observation_summary,
                    client_context=client_context,
                ),
                pending_intent_selection=pending_intent_selection,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
                ongoing_action_transition_summary=ongoing_action_transition_summary,
            )
            self._emit_capability_result_assistant_message_event(
                cycle_id=cycle_id,
                capability_response=capability_response,
                pipeline=pipeline,
            )
            self._apply_capability_runtime_state_followup(
                capability_id=capability_id,
                current_time=self._now_iso(),
                observation_summary=observation_summary,
                result_payload=capability_response,
                ongoing_action_transition_summary=ongoing_action_transition_summary,
            )
            debug_log(
                "CapabilityResult",
                f"{self._short_cycle_id(cycle_id)} done result={response['result_kind']}",
            )
        except RecallPackSelectionError as exc:
            if ongoing_action_transition_summary is None:
                ongoing_action_transition_summary = self._interrupt_capability_result_ongoing_action(
                    request_record=request_record,
                    current_time=self._now_iso(),
                    failure_reason=str(exc),
                )
            debug_log(
                "CapabilityResult",
                (
                    f"{self._short_cycle_id(cycle_id)} failed stage={exc.failure_stage} "
                    f"error={type(exc).__name__}: {self._clamp(str(exc))}"
                ),
            )
            self._persist_cycle_failure(
                cycle_id=cycle_id,
                started_at=started_at,
                finished_at=self._now_iso(),
                state=state,
                runtime_summary=runtime_summary,
                input_text=input_text,
                client_context=client_context,
                failure_reason=str(exc),
                trigger_kind="capability_result",
                input_event_kind="capability_result",
                input_event_role="system",
                recall_trace=self._build_failure_recall_trace(
                    recall_hint=exc.recall_hint_summary,
                    recall_pack_selection=exc.recall_pack_selection,
                ),
                failure_event_kind="recall_pack_selection_failure",
                failure_event_payload={
                    "failure_stage": exc.failure_stage,
                },
                pending_intent_selection=pending_intent_selection,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
                ongoing_action_transition_summary=ongoing_action_transition_summary,
            )
            self._emit_input_failure_logs(
                cycle_id=cycle_id,
                trigger_kind="capability_result",
                input_text=input_text,
                failure_reason=str(exc),
                pending_intent_selection=pending_intent_selection,
            )
            self._apply_capability_runtime_state_followup(
                capability_id=capability_id,
                current_time=self._now_iso(),
                observation_summary=observation_summary,
                result_payload=capability_response,
                ongoing_action_transition_summary=ongoing_action_transition_summary,
                failure_reason=str(exc),
            )
        except (LLMError, KeyError, ValueError) as exc:
            failed_followup_capability_request_summary, failed_transition_summary = (
                self._exception_capability_dispatch_trace(exc)
            )
            if ongoing_action_transition_summary is None:
                if isinstance(failed_transition_summary, dict):
                    ongoing_action_transition_summary = failed_transition_summary
                else:
                    ongoing_action_transition_summary = self._interrupt_capability_result_ongoing_action(
                        request_record=request_record,
                        current_time=self._now_iso(),
                        failure_reason=str(exc),
                    )
            debug_log(
                "CapabilityResult",
                f"{self._short_cycle_id(cycle_id)} failed error={type(exc).__name__}: {self._clamp(str(exc))}",
            )
            self._persist_cycle_failure(
                cycle_id=cycle_id,
                started_at=started_at,
                finished_at=self._now_iso(),
                state=state,
                runtime_summary=runtime_summary,
                input_text=input_text,
                client_context=client_context,
                failure_reason=str(exc),
                trigger_kind="capability_result",
                input_event_kind="capability_result",
                input_event_role="system",
                pending_intent_selection=pending_intent_selection,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
                followup_capability_request_summary=failed_followup_capability_request_summary,
                ongoing_action_transition_summary=ongoing_action_transition_summary,
            )
            self._emit_input_failure_logs(
                cycle_id=cycle_id,
                trigger_kind="capability_result",
                input_text=input_text,
                failure_reason=str(exc),
                pending_intent_selection=pending_intent_selection,
            )
            self._apply_capability_runtime_state_followup(
                capability_id=capability_id,
                current_time=self._now_iso(),
                observation_summary=observation_summary,
                result_payload=capability_response,
                ongoing_action_transition_summary=ongoing_action_transition_summary,
                failure_reason=str(exc),
            )

    def _resolve_capability_result_followup_ongoing_action(
        self,
        *,
        request_record: Any,
        current_time: str,
        capability_id: str,
        result_payload: dict[str, Any],
        decision: dict[str, Any],
        pipeline_ongoing_action_transition: Any,
    ) -> dict[str, Any] | None:
        if isinstance(pipeline_ongoing_action_transition, dict):
            return pipeline_ongoing_action_transition

        decision_kind = str(decision.get("kind") or "").strip()
        if decision_kind == "pending_intent":
            result_error = result_payload.get("error") not in {None, ""}
            return self._finish_capability_ongoing_action(
                request_record=request_record,
                current_time=current_time,
                terminal_kind="on_hold",
                reason_code="followup_pending_intent",
                terminal_reason=self._capability_terminal_transition_reason_summary(
                    reason_code="followup_pending_intent",
                    result_error=result_error,
                ),
                final_step_summary="後で再評価するため pending_intent に切り替えた。",
                transition_source="capability_result_followup",
                decision_kind=decision_kind,
                result_error=result_error,
                detail_summary=self._capability_result_followup_detail_summary(
                    capability_id=capability_id,
                    decision=decision,
                    result_payload=result_payload,
                ),
            )

        terminal_kind = "interrupted" if result_payload.get("error") not in {None, ""} else "completed"
        reason_code = self._capability_result_followup_reason_code(
            decision=decision,
            result_payload=result_payload,
        )
        return self._finish_capability_ongoing_action(
            request_record=request_record,
            current_time=current_time,
            terminal_kind=terminal_kind,
            reason_code=reason_code,
            terminal_reason=self._capability_result_followup_terminal_reason(
                capability_id=capability_id,
                result_payload=result_payload,
                decision=decision,
            ),
            final_step_summary=self._capability_result_followup_terminal_step_summary(
                capability_id=capability_id,
                result_payload=result_payload,
                decision=decision,
            ),
            transition_source="capability_result_followup",
            decision_kind=decision_kind or None,
            result_error=result_payload.get("error") not in {None, ""},
            detail_summary=self._capability_result_followup_detail_summary(
                capability_id=capability_id,
                decision=decision,
                observation_summary=None,
                result_payload=result_payload,
            ),
        )

    def _interrupt_capability_result_ongoing_action(
        self,
        *,
        request_record: Any,
        current_time: str,
        failure_reason: str,
    ) -> dict[str, Any] | None:
        return self._finish_capability_ongoing_action(
            request_record=request_record,
            current_time=current_time,
            terminal_kind="interrupted",
            reason_code="followup_failed",
            terminal_reason=self._capability_terminal_transition_reason_summary(
                reason_code="followup_failed",
                result_error=True,
            ),
            final_step_summary="result 後の判断に失敗したため終了した。",
            transition_source="capability_result_followup",
            result_error=True,
            detail_summary=failure_reason,
        )

    def _emit_capability_result_assistant_message_event(
        self,
        *,
        cycle_id: str,
        capability_response: dict[str, Any],
        pipeline: dict[str, Any],
    ) -> None:
        reply_payload = pipeline.get("reply_payload")
        if not isinstance(reply_payload, dict):
            debug_log("CapabilityResult", f"{self._short_cycle_id(cycle_id)} assistant_message skipped no_reply")
            return

        target_client_id = capability_response.get("client_id")
        if not isinstance(target_client_id, str) or not target_client_id.strip():
            debug_log("CapabilityResult", f"{self._short_cycle_id(cycle_id)} assistant_message skipped no_client")
            return

        request_record = capability_response.get("request_record")
        request_id = capability_response.get("request_id")
        capability_id = self._capability_result_capability_id(capability_response)
        if isinstance(request_record, dict):
            request_id = request_record.get("request_id", request_id)
        event = {
            "event_id": self._next_stream_event_id(),
            "type": "assistant_message",
            "data": {
                "cycle_id": cycle_id,
                "source_kind": "capability_result",
                "request_id": request_id,
                "capability_id": capability_id,
                "system_text": f"[capability_result] {capability_id}",
                "message": reply_payload["reply_text"],
            },
        }
        sent = self._event_stream_registry.send_to_client(target_client_id.strip(), event)
        debug_log(
            "CapabilityResult",
            (
                f"{self._short_cycle_id(cycle_id)} assistant_message sent={sent} "
                f"client={target_client_id.strip()} "
                f"reply_chars={len(reply_payload['reply_text'])}"
            ),
        )
