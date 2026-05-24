from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from otomekairo.capabilities import capability_manifests, capability_readiness_result_digest
from otomekairo.llm import LLMError
from otomekairo.recall import RecallPackSelectionError
from otomekairo.service_common import ServiceError, debug_log


@dataclass(frozen=True, slots=True)
class CapabilityResultPayloadSpec:
    summary_field: str
    accepted_detail_label: str


SIMPLE_CAPABILITY_RESULT_PAYLOAD_SPECS = {
    "external.status": CapabilityResultPayloadSpec(
        summary_field="status_text",
        accepted_detail_label="status_chars",
    ),
    "device.status": CapabilityResultPayloadSpec(
        summary_field="device_state_summary",
        accepted_detail_label="device_summary_chars",
    ),
    "body.status": CapabilityResultPayloadSpec(
        summary_field="body_state_summary",
        accepted_detail_label="body_summary_chars",
    ),
    "environment.status": CapabilityResultPayloadSpec(
        summary_field="environment_summary",
        accepted_detail_label="environment_summary_chars",
    ),
    "location.status": CapabilityResultPayloadSpec(
        summary_field="location_summary",
        accepted_detail_label="location_summary_chars",
    ),
    "social.status": CapabilityResultPayloadSpec(
        summary_field="social_context_summary",
        accepted_detail_label="social_context_summary_chars",
    ),
}


class ServiceSpontaneousCapabilityResultMixin:
    def _normalize_capability_result_payload(
        self,
        *,
        capability_id: str,
        result_payload: dict[str, Any],
    ) -> dict[str, Any]:
        if capability_id == "vision.capture":
            images = result_payload.get("images", [])
            client_context = result_payload.get("client_context")
            error = result_payload.get("error")
            if client_context is not None and not isinstance(client_context, dict):
                raise ServiceError(400, "invalid_capability_result", "vision.capture result.client_context must be an object.")
            if error is not None and not isinstance(error, str):
                raise ServiceError(400, "invalid_capability_result", "vision.capture result.error must be a string or null.")
            normalized_images = self._normalize_vision_capture_result_images(images)
            return {
                "images": normalized_images,
                "client_context": client_context or {},
                "error": error.strip() if isinstance(error, str) and error.strip() else None,
            }
        spec = SIMPLE_CAPABILITY_RESULT_PAYLOAD_SPECS.get(capability_id)
        if spec is not None:
            return self._normalize_simple_capability_result_payload(
                capability_id=capability_id,
                result_payload=result_payload,
                spec=spec,
            )
        if capability_id == "schedule.status":
            schedule_summary = result_payload.get("schedule_summary")
            schedule_slots = result_payload.get("schedule_slots")
            client_context = result_payload.get("client_context")
            error = result_payload.get("error")
            if not isinstance(schedule_summary, str) or not schedule_summary.strip():
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "schedule.status result.schedule_summary must be a non-empty string.",
                )
            if not isinstance(schedule_slots, list):
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "schedule.status result.schedule_slots must be an array.",
                )
            if client_context is not None and not isinstance(client_context, dict):
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "schedule.status result.client_context must be an object.",
                )
            if error is not None and not isinstance(error, str):
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "schedule.status result.error must be a string or null.",
                )
            return {
                "schedule_summary": schedule_summary.strip(),
                "schedule_slots": self._normalize_capability_result_schedule_slots(schedule_slots),
                "client_context": client_context or {},
                "error": error.strip() if isinstance(error, str) and error.strip() else None,
            }
        payload = dict(result_payload)
        client_context = payload.get("client_context")
        if client_context is not None and not isinstance(client_context, dict):
            raise ServiceError(400, "invalid_capability_result", "result.client_context must be an object.")
        error = payload.get("error")
        if error is not None and not isinstance(error, str):
            raise ServiceError(400, "invalid_capability_result", "result.error must be a string or null.")
        if isinstance(client_context, dict):
            payload["client_context"] = client_context
        if "error" in payload:
            payload["error"] = error.strip() if isinstance(error, str) and error.strip() else None
        return payload

    def _normalize_simple_capability_result_payload(
        self,
        *,
        capability_id: str,
        result_payload: dict[str, Any],
        spec: CapabilityResultPayloadSpec,
    ) -> dict[str, Any]:
        summary_text = result_payload.get(spec.summary_field)
        client_context = result_payload.get("client_context")
        error = result_payload.get("error")
        if not isinstance(summary_text, str) or not summary_text.strip():
            raise ServiceError(
                400,
                "invalid_capability_result",
                f"{capability_id} result.{spec.summary_field} must be a non-empty string.",
            )
        if client_context is not None and not isinstance(client_context, dict):
            raise ServiceError(
                400,
                "invalid_capability_result",
                f"{capability_id} result.client_context must be an object.",
            )
        if error is not None and not isinstance(error, str):
            raise ServiceError(
                400,
                "invalid_capability_result",
                f"{capability_id} result.error must be a string or null.",
            )
        return {
            spec.summary_field: summary_text.strip(),
            "client_context": client_context or {},
            "error": error.strip() if isinstance(error, str) and error.strip() else None,
        }

    def _normalize_vision_capture_result_images(self, images: Any) -> list[str]:
        try:
            return self._normalize_visual_observation_images(images, allow_missing=False)
        except ServiceError as exc:
            if exc.error_code != "invalid_images":
                raise
            raise ServiceError(
                400,
                "invalid_capability_result",
                f"vision.capture result.{exc.message}",
            ) from exc

    def _normalize_capability_result_schedule_slots(self, raw_slots: list[Any]) -> list[dict[str, Any]]:
        normalized_slots: list[dict[str, Any]] = []
        seen_slot_keys: set[str] = set()
        for item in raw_slots:
            if not isinstance(item, dict):
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "schedule.status result.schedule_slots must contain objects.",
                )
            slot_key = item.get("slot_key")
            summary_text = item.get("summary_text")
            if not isinstance(slot_key, str) or not slot_key.strip():
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "schedule.status result.schedule_slots[].slot_key must be a non-empty string.",
                )
            if not isinstance(summary_text, str) or not summary_text.strip():
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "schedule.status result.schedule_slots[].summary_text must be a non-empty string.",
                )
            normalized_slot_key = self._clamp(slot_key.strip(), limit=160)
            if normalized_slot_key in seen_slot_keys:
                continue
            seen_slot_keys.add(normalized_slot_key)
            slot_payload: dict[str, Any] = {
                "slot_key": normalized_slot_key,
                "summary_text": self._clamp(summary_text.strip(), limit=160),
            }
            for key in ("not_before", "expires_at"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    slot_payload[key] = value.strip()
            normalized_slots.append(slot_payload)
        return normalized_slots[:4]

    def _capability_result_log_channel(self, capability_id: str) -> str:
        return "CapabilityResult"

    def _capability_result_accepted_detail(self, *, capability_id: str, result_payload: dict[str, Any]) -> str:
        if capability_id == "vision.capture":
            images = result_payload.get("images")
            image_count = len(images) if isinstance(images, list) else 0
            return f"images={image_count} error={bool(result_payload.get('error'))}"
        spec = SIMPLE_CAPABILITY_RESULT_PAYLOAD_SPECS.get(capability_id)
        if spec is not None:
            summary_text = result_payload.get(spec.summary_field)
            summary_chars = len(summary_text) if isinstance(summary_text, str) else 0
            return f"{spec.accepted_detail_label}={summary_chars} error={bool(result_payload.get('error'))}"
        return f"result_keys={len(result_payload)} error={bool(result_payload.get('error'))}"

    def _capability_result_context_hook_name(self, capability_id: str) -> str | None:
        hook_name = self._capability_state_policy(capability_id).get("result_context_hook")
        if isinstance(hook_name, str) and hook_name.strip():
            return hook_name.strip()
        return None

    def _capability_followup_hint_hook_name(self, capability_id: str) -> str | None:
        hook_name = self._capability_state_policy(capability_id).get("followup_hint_hook")
        if isinstance(hook_name, str) and hook_name.strip():
            return hook_name.strip()
        return None

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

    def _capability_result_capability_id(self, capability_response: dict[str, Any]) -> str:
        capability_id = capability_response.get("capability_id")
        if isinstance(capability_id, str) and capability_id.strip():
            return capability_id.strip()
        request_record = capability_response.get("request_record")
        if isinstance(request_record, dict):
            capability_id = request_record.get("capability_id")
            if isinstance(capability_id, str) and capability_id.strip():
                return capability_id.strip()
        return "unknown_capability"

    def _capability_result_payload_image_count(self, capability_response: dict[str, Any]) -> int | None:
        images = capability_response.get("images")
        if not isinstance(images, list):
            return None
        return len(images)

    def _capability_result_status_text(self, capability_response: dict[str, Any]) -> str | None:
        status_text = capability_response.get("status_text")
        if not isinstance(status_text, str) or not status_text.strip():
            return None
        return self._clamp(status_text.strip(), limit=160)

    def _build_capability_result_client_context(self, capability_response: dict[str, Any]) -> dict[str, Any]:
        client_context = capability_response.get("client_context", {})
        if not isinstance(client_context, dict):
            client_context = {}

        summary = {
            "source": "capability_result",
            "capability_id": self._capability_result_capability_id(capability_response),
            "client_id": capability_response.get("client_id"),
            "active_app": client_context.get("active_app"),
            "window_title": client_context.get("window_title"),
            "locale": client_context.get("locale"),
            "external_service_summary": client_context.get("external_service_summary"),
            "social_context_summary": client_context.get("social_context_summary"),
            "environment_summary": client_context.get("environment_summary"),
            "location_summary": client_context.get("location_summary"),
            "body_state_summary": client_context.get("body_state_summary"),
            "device_state_summary": client_context.get("device_state_summary"),
            "schedule_summary": client_context.get("schedule_summary"),
        }
        schedule_slots = self._capability_result_schedule_slots(capability_response)
        if schedule_slots is not None:
            summary["schedule_slots"] = schedule_slots
        image_count = self._capability_result_payload_image_count(capability_response)
        if image_count is not None:
            summary["image_count"] = image_count
        return summary

    def _capability_result_schedule_slots(self, capability_response: dict[str, Any]) -> list[dict[str, Any]] | None:
        raw_slots = capability_response.get("schedule_slots")
        if isinstance(raw_slots, list):
            return self._normalize_capability_result_summary_schedule_slots(raw_slots)
        client_context = capability_response.get("client_context", {})
        if not isinstance(client_context, dict):
            return None
        raw_slots = client_context.get("schedule_slots")
        if not isinstance(raw_slots, list):
            return None
        return self._normalize_capability_result_summary_schedule_slots(raw_slots)

    def _normalize_capability_result_summary_schedule_slots(self, raw_slots: list[Any]) -> list[dict[str, Any]] | None:
        normalized_slots: list[dict[str, Any]] = []
        seen_slot_keys: set[str] = set()
        for item in raw_slots:
            if not isinstance(item, dict):
                continue
            slot_key = self._client_context_text(item.get("slot_key"), limit=160)
            summary_text = self._client_context_text(item.get("summary_text"), limit=160)
            if slot_key is None or summary_text is None or slot_key in seen_slot_keys:
                continue
            seen_slot_keys.add(slot_key)
            slot_payload: dict[str, Any] = {
                "slot_key": slot_key,
                "summary_text": summary_text,
            }
            for key in ("not_before", "expires_at"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    slot_payload[key] = value.strip()
            normalized_slots.append(slot_payload)
        if not normalized_slots:
            return None
        return normalized_slots[:4]

    def _capability_result_observation_summary(self, capability_response: dict[str, Any]) -> dict[str, Any]:
        request_record = capability_response.get("request_record")
        capability_id = self._capability_result_capability_id(capability_response)
        summary = {
            "source": "capability_result",
            "capability_id": capability_id,
            "error": capability_response.get("error"),
        }
        image_count = self._capability_result_payload_image_count(capability_response)
        if image_count is not None:
            summary["image_count"] = image_count
        if capability_id == "vision.capture":
            summary["image_interpreted"] = False
        client_id = capability_response.get("client_id")
        if isinstance(client_id, str) and client_id.strip():
            summary["client_id"] = client_id.strip()
        client_context = capability_response.get("client_context", {})
        if isinstance(client_context, dict):
            for key in ("active_app", "window_title", "locale"):
                value = client_context.get(key)
                if isinstance(value, str) and value.strip():
                    summary[key] = value.strip()
        manifest = capability_manifests().get(capability_id, {})
        inspection_fields = manifest.get("inspection_fields", [])
        if isinstance(inspection_fields, list):
            request_input = request_record.get("input") if isinstance(request_record, dict) else {}
            if not isinstance(request_input, dict):
                request_input = {}
            for field in inspection_fields:
                if not isinstance(field, str) or field in summary:
                    continue
                if field == "target_client_id":
                    value = request_record.get("target_client_id") if isinstance(request_record, dict) else None
                else:
                    value = capability_response.get(field)
                    if value is None:
                        value = request_input.get(field)
                    if value is None and isinstance(client_context, dict):
                        value = client_context.get(field)
                if isinstance(value, str):
                    normalized = value.strip()
                    if not normalized:
                        continue
                    summary[field] = self._clamp(normalized, limit=160)
                elif isinstance(value, (int, float, bool)):
                    summary[field] = value
                elif field == "schedule_slots" and isinstance(value, list):
                    normalized_slots = self._normalize_capability_result_summary_schedule_slots(value)
                    if normalized_slots is not None:
                        summary[field] = normalized_slots
        readiness_digest = capability_readiness_result_digest(capability_id, summary)
        if isinstance(readiness_digest, dict):
            summary["readiness_digest"] = readiness_digest
        return summary

    def _prepare_capability_result_context(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        capability_id: str,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        input_text: str,
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        hook_name = self._capability_result_context_hook_name(capability_id)
        if hook_name == "vision_capture":
            client_context, observation_summary = self._interpret_capability_result_capture(
                state=state,
                started_at=started_at,
                client_context=client_context,
                observation_summary=observation_summary,
                input_text=input_text,
                capability_response=capability_response,
            )
            input_text = self._build_capability_result_input_text(
                client_context=client_context,
                capability_response=capability_response,
            )
        elif hook_name == "external_status":
            client_context, observation_summary, input_text = self._prepare_external_status_result_context(
                client_context=client_context,
                observation_summary=observation_summary,
                capability_response=capability_response,
            )
        elif hook_name == "schedule_status":
            client_context, observation_summary, input_text = self._prepare_schedule_status_result_context(
                client_context=client_context,
                observation_summary=observation_summary,
                capability_response=capability_response,
            )
        elif hook_name == "device_status":
            client_context, observation_summary, input_text = self._prepare_device_status_result_context(
                client_context=client_context,
                observation_summary=observation_summary,
                capability_response=capability_response,
            )
        elif hook_name == "body_status":
            client_context, observation_summary, input_text = self._prepare_body_status_result_context(
                client_context=client_context,
                observation_summary=observation_summary,
                capability_response=capability_response,
            )
        elif hook_name == "environment_status":
            client_context, observation_summary, input_text = self._prepare_environment_status_result_context(
                client_context=client_context,
                observation_summary=observation_summary,
                capability_response=capability_response,
            )
        elif hook_name == "location_status":
            client_context, observation_summary, input_text = self._prepare_location_status_result_context(
                client_context=client_context,
                observation_summary=observation_summary,
                capability_response=capability_response,
            )
        elif hook_name == "social_status":
            client_context, observation_summary, input_text = self._prepare_social_status_result_context(
                client_context=client_context,
                observation_summary=observation_summary,
                capability_response=capability_response,
            )
        return client_context, observation_summary, input_text

    def _prepare_external_status_result_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        status_text = self._capability_result_status_text(capability_response)
        enriched_client_context = dict(client_context)
        if status_text is not None and self._client_context_text(enriched_client_context.get("external_service_summary"), limit=160) is None:
            enriched_client_context["external_service_summary"] = status_text
        enriched_observation_summary = dict(observation_summary)
        if status_text is not None:
            enriched_observation_summary["status_text"] = status_text
        input_text = self._build_capability_result_input_text(
            client_context=enriched_client_context,
            capability_response=capability_response,
        )
        return enriched_client_context, enriched_observation_summary, input_text

    def _prepare_schedule_status_result_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        schedule_summary = self._client_context_text(capability_response.get("schedule_summary"), limit=160)
        schedule_slots = self._capability_result_schedule_slots(capability_response)
        enriched_client_context = dict(client_context)
        if schedule_summary is not None:
            enriched_client_context["schedule_summary"] = schedule_summary
        if schedule_slots is not None:
            enriched_client_context["schedule_slots"] = schedule_slots
        enriched_observation_summary = dict(observation_summary)
        if schedule_summary is not None:
            enriched_observation_summary["schedule_summary"] = schedule_summary
        if schedule_slots is not None:
            enriched_observation_summary["schedule_slots"] = schedule_slots
        input_text = self._build_capability_result_input_text(
            client_context=enriched_client_context,
            capability_response=capability_response,
        )
        return enriched_client_context, enriched_observation_summary, input_text

    def _prepare_device_status_result_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        device_state_summary = self._client_context_text(capability_response.get("device_state_summary"), limit=160)
        enriched_client_context = dict(client_context)
        if device_state_summary is not None:
            enriched_client_context["device_state_summary"] = device_state_summary
        enriched_observation_summary = dict(observation_summary)
        if device_state_summary is not None:
            enriched_observation_summary["device_state_summary"] = device_state_summary
        input_text = self._build_capability_result_input_text(
            client_context=enriched_client_context,
            capability_response=capability_response,
        )
        return enriched_client_context, enriched_observation_summary, input_text

    def _prepare_body_status_result_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        body_state_summary = self._client_context_text(capability_response.get("body_state_summary"), limit=160)
        enriched_client_context = dict(client_context)
        if body_state_summary is not None:
            enriched_client_context["body_state_summary"] = body_state_summary
        enriched_observation_summary = dict(observation_summary)
        if body_state_summary is not None:
            enriched_observation_summary["body_state_summary"] = body_state_summary
        input_text = self._build_capability_result_input_text(
            client_context=enriched_client_context,
            capability_response=capability_response,
        )
        return enriched_client_context, enriched_observation_summary, input_text

    def _prepare_environment_status_result_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        environment_summary = self._client_context_text(capability_response.get("environment_summary"), limit=160)
        enriched_client_context = dict(client_context)
        if environment_summary is not None:
            enriched_client_context["environment_summary"] = environment_summary
        enriched_observation_summary = dict(observation_summary)
        if environment_summary is not None:
            enriched_observation_summary["environment_summary"] = environment_summary
        input_text = self._build_capability_result_input_text(
            client_context=enriched_client_context,
            capability_response=capability_response,
        )
        return enriched_client_context, enriched_observation_summary, input_text

    def _prepare_location_status_result_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        location_summary = self._client_context_text(capability_response.get("location_summary"), limit=160)
        enriched_client_context = dict(client_context)
        if location_summary is not None:
            enriched_client_context["location_summary"] = location_summary
        enriched_observation_summary = dict(observation_summary)
        if location_summary is not None:
            enriched_observation_summary["location_summary"] = location_summary
        input_text = self._build_capability_result_input_text(
            client_context=enriched_client_context,
            capability_response=capability_response,
        )
        return enriched_client_context, enriched_observation_summary, input_text

    def _prepare_social_status_result_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        social_context_summary = self._client_context_text(capability_response.get("social_context_summary"), limit=160)
        enriched_client_context = dict(client_context)
        if social_context_summary is not None:
            enriched_client_context["social_context_summary"] = social_context_summary
        enriched_observation_summary = dict(observation_summary)
        if social_context_summary is not None:
            enriched_observation_summary["social_context_summary"] = social_context_summary
        input_text = self._build_capability_result_input_text(
            client_context=enriched_client_context,
            capability_response=capability_response,
        )
        return enriched_client_context, enriched_observation_summary, input_text

    def _capability_result_followup_hint_summary(
        self,
        *,
        capability_id: str,
        observation_summary: dict[str, Any] | None,
        result_payload: dict[str, Any] | None,
    ) -> str | None:
        hook_name = self._capability_followup_hint_hook_name(capability_id)
        if hook_name == "vision_capture":
            visual_summary_text = None
            if isinstance(observation_summary, dict):
                visual_summary_text = observation_summary.get("visual_summary_text")
            if isinstance(visual_summary_text, str) and visual_summary_text.strip():
                return self._clamp(f"視覚観測では {visual_summary_text.strip()}", limit=160)
            image_count = self._capability_result_payload_image_count(result_payload or {})
            if image_count is not None and image_count <= 0:
                return "視覚観測は空で、追加の手掛かりを得られなかった。"
            return None
        if hook_name == "external_status":
            status_text = None
            service = None
            if isinstance(observation_summary, dict):
                status_text = observation_summary.get("status_text")
                service = observation_summary.get("service")
            if isinstance(status_text, str) and status_text.strip():
                if isinstance(service, str) and service.strip():
                    return self._clamp(f"{service.strip()} の状態要約: {status_text.strip()}", limit=160)
                return self._clamp(status_text.strip(), limit=160)
            return None
        if hook_name == "schedule_status":
            schedule_summary = None
            slot_count = None
            if isinstance(observation_summary, dict):
                schedule_summary = observation_summary.get("schedule_summary")
                schedule_slots = observation_summary.get("schedule_slots")
                if isinstance(schedule_slots, list):
                    slot_count = len(schedule_slots)
            if isinstance(schedule_summary, str) and schedule_summary.strip():
                return self._clamp(f"予定要約: {schedule_summary.strip()}", limit=160)
            if isinstance(slot_count, int) and slot_count > 0:
                return f"近い予定が {slot_count} 件ある。"
            return None
        if hook_name == "device_status":
            device_state_summary = None
            if isinstance(observation_summary, dict):
                device_state_summary = observation_summary.get("device_state_summary")
            if isinstance(device_state_summary, str) and device_state_summary.strip():
                return self._clamp(f"端末状態: {device_state_summary.strip()}", limit=160)
            return None
        if hook_name == "body_status":
            body_state_summary = None
            if isinstance(observation_summary, dict):
                body_state_summary = observation_summary.get("body_state_summary")
            if isinstance(body_state_summary, str) and body_state_summary.strip():
                return self._clamp(f"身体状態: {body_state_summary.strip()}", limit=160)
            return None
        if hook_name == "environment_status":
            environment_summary = None
            if isinstance(observation_summary, dict):
                environment_summary = observation_summary.get("environment_summary")
            if isinstance(environment_summary, str) and environment_summary.strip():
                return self._clamp(f"環境状態: {environment_summary.strip()}", limit=160)
            return None
        if hook_name == "location_status":
            location_summary = None
            if isinstance(observation_summary, dict):
                location_summary = observation_summary.get("location_summary")
            if isinstance(location_summary, str) and location_summary.strip():
                return self._clamp(f"場所状態: {location_summary.strip()}", limit=160)
            return None
        if hook_name == "social_status":
            social_context_summary = None
            if isinstance(observation_summary, dict):
                social_context_summary = observation_summary.get("social_context_summary")
            if isinstance(social_context_summary, str) and social_context_summary.strip():
                return self._clamp(f"対人文脈: {social_context_summary.strip()}", limit=160)
            return None
        return None

    def _interpret_capability_result_capture(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        input_text: str,
        capability_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        images = self._normalize_visual_observation_images(
            capability_response.get("images", []),
            allow_missing=True,
        )
        return self._interpret_visual_observation(
            state=state,
            started_at=started_at,
            trigger_kind="capability_result",
            client_context=client_context,
            observation_summary=observation_summary,
            input_text=input_text,
            images=images,
        )

    def _build_capability_result_input_text(
        self,
        *,
        client_context: dict[str, Any],
        capability_response: dict[str, Any],
    ) -> str:
        parts = ["capability result を受信。"]
        capability_id = self._capability_result_capability_id(capability_response)
        source_label = self._client_context_text(client_context.get("source_label"), limit=80)
        source_kind = self._client_context_text(client_context.get("source_kind"), limit=32)
        vision_source_id = self._client_context_text(client_context.get("vision_source_id"), limit=96)
        image_count = self._capability_result_payload_image_count(capability_response)
        parts.append(f"{capability_id} の非同期結果を受け取った。")
        if source_label is not None:
            parts.append(f"観測 source は {source_label}。")
        elif vision_source_id is not None:
            parts.append(f"観測 source id は {vision_source_id}。")
        if source_kind is not None:
            parts.append(f"source kind は {source_kind}。")
        error = capability_response.get("error")
        if isinstance(error, str) and error.strip():
            parts.append(f"結果は error だった。 error={self._clamp(error, limit=120)}")
        status_text = self._capability_result_status_text(capability_response)
        if status_text is not None:
            parts.append(f"結果要約は {status_text}")
        elif capability_id == "schedule.status":
            schedule_summary = self._client_context_text(capability_response.get("schedule_summary"), limit=160)
            if schedule_summary is not None:
                parts.append(f"予定要約は {schedule_summary}")
            else:
                parts.append("予定確認の結果を踏まえて返答や次の行動を決めたい。")
        elif capability_id == "device.status":
            device_state_summary = self._client_context_text(capability_response.get("device_state_summary"), limit=160)
            if device_state_summary is not None:
                parts.append(f"端末状態要約は {device_state_summary}")
            else:
                parts.append("端末状態確認の結果を踏まえて返答や次の行動を決めたい。")
        elif capability_id == "body.status":
            body_state_summary = self._client_context_text(capability_response.get("body_state_summary"), limit=160)
            if body_state_summary is not None:
                parts.append(f"身体状態要約は {body_state_summary}")
            else:
                parts.append("身体状態確認の結果を踏まえて返答や次の行動を決めたい。")
        elif capability_id == "environment.status":
            environment_summary = self._client_context_text(capability_response.get("environment_summary"), limit=160)
            if environment_summary is not None:
                parts.append(f"環境状態要約は {environment_summary}")
            else:
                parts.append("環境状態確認の結果を踏まえて返答や次の行動を決めたい。")
        elif capability_id == "location.status":
            location_summary = self._client_context_text(capability_response.get("location_summary"), limit=160)
            if location_summary is not None:
                parts.append(f"場所状態要約は {location_summary}")
            else:
                parts.append("場所状態確認の結果を踏まえて返答や次の行動を決めたい。")
        elif capability_id == "social.status":
            social_context_summary = self._client_context_text(
                capability_response.get("social_context_summary"),
                limit=160,
            )
            if social_context_summary is not None:
                parts.append(f"対人文脈要約は {social_context_summary}")
            else:
                parts.append("対人文脈確認の結果を踏まえて返答や次の行動を決めたい。")
        elif capability_id == "vision.capture" and image_count is not None and image_count <= 0:
            parts.append("観測結果は空だった。")
        else:
            parts.append("受け取った結果を踏まえて返答や次の行動を決めたい。")
        return " ".join(parts)

    def _capability_result_active_step_summary(self, *, capability_id: str, result_payload: dict[str, Any]) -> str:
        error = result_payload.get("error")
        if isinstance(error, str) and error.strip():
            return f"{capability_id} の error 結果を受け、次の1手を判断中。"
        return f"{capability_id} の結果を受け、次の1手を判断中。"

    def _capability_result_followup_terminal_reason(
        self,
        *,
        capability_id: str,
        result_payload: dict[str, Any],
        decision: dict[str, Any],
    ) -> str:
        has_error = result_payload.get("error") not in {None, ""}
        reason_code = self._capability_result_followup_reason_code(
            decision=decision,
            result_payload=result_payload,
        )
        if reason_code in {"followup_reply", "followup_noop"}:
            return self._capability_terminal_transition_reason_summary(
                reason_code=reason_code,
                result_error=has_error,
            )
        return self._capability_result_terminal_reason(
            capability_id=capability_id,
            result_payload=result_payload,
        )

    def _capability_result_followup_reason_code(
        self,
        *,
        decision: dict[str, Any],
        result_payload: dict[str, Any],
    ) -> str:
        decision_kind = str(decision.get("kind") or "").strip()
        if decision_kind == "reply":
            return "followup_reply"
        if decision_kind == "noop":
            return "followup_noop"
        if result_payload.get("error") not in {None, ""}:
            return "result_error"
        return "result_received"

    def _capability_result_followup_detail_summary(
        self,
        *,
        capability_id: str,
        decision: dict[str, Any],
        observation_summary: dict[str, Any] | None = None,
        result_payload: dict[str, Any] | None = None,
    ) -> str | None:
        decision_reason = self._clamp(str(decision.get("reason_summary") or "").strip(), limit=160)
        if decision_reason:
            return decision_reason
        hook_summary = self._capability_result_followup_hint_summary(
            capability_id=capability_id,
            observation_summary=observation_summary,
            result_payload=result_payload,
        )
        if hook_summary is not None:
            return hook_summary
        if isinstance(result_payload, dict):
            error = result_payload.get("error")
            if isinstance(error, str) and error.strip():
                return self._clamp(error.strip(), limit=160)
        return None

    def _apply_capability_runtime_state_followup(
        self,
        *,
        capability_id: str,
        current_time: str,
        observation_summary: dict[str, Any] | None,
        result_payload: dict[str, Any],
        ongoing_action_transition_summary: dict[str, Any] | None,
        failure_reason: str | None = None,
    ) -> None:
        if not isinstance(ongoing_action_transition_summary, dict):
            return
        final_state = str(ongoing_action_transition_summary.get("final_state") or "").strip()
        if final_state == "waiting_result":
            return
        hook_summary = self._capability_result_followup_hint_summary(
            capability_id=capability_id,
            observation_summary=observation_summary,
            result_payload=result_payload,
        )
        summary_text = hook_summary or str(ongoing_action_transition_summary.get("reason_summary") or "").strip() or failure_reason or capability_id
        state_policy = self._capability_state_policy(capability_id)
        if ongoing_action_transition_summary.get("result_error") is True or final_state == "interrupted":
            self._mark_capability_runtime_failure(
                capability_id=capability_id,
                current_time=current_time,
                failure_summary=summary_text,
                cooldown_seconds=int(state_policy.get("error_cooldown_seconds") or 0),
            )
            return
        if final_state in {"completed", "on_hold"}:
            self._mark_capability_runtime_success(
                capability_id=capability_id,
                current_time=current_time,
                result_summary=summary_text,
                cooldown_seconds=int(state_policy.get("success_cooldown_seconds") or 0),
            )

    def _capability_result_followup_terminal_step_summary(
        self,
        *,
        capability_id: str,
        result_payload: dict[str, Any],
        decision: dict[str, Any],
    ) -> str:
        decision_kind = str(decision.get("kind") or "").strip()
        has_error = result_payload.get("error") not in {None, ""}
        if decision_kind == "reply":
            if has_error:
                return f"{capability_id} の error を受けて reply した。"
            return f"{capability_id} の結果を受けて reply した。"
        if decision_kind == "noop":
            if has_error:
                return f"{capability_id} の error を受けて継続を中断した。"
            return f"{capability_id} の結果を受けて継続を完了した。"
        return self._capability_result_terminal_step_summary(
            capability_id=capability_id,
            result_payload=result_payload,
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
