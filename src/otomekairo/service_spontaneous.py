from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from typing import Any

from otomekairo.capabilities import capability_manifests
from otomekairo.llm import LLMContractError, LLMError
from otomekairo.recall import RecallPackSelectionError
from otomekairo.service_common import (
    BACKGROUND_DESKTOP_WATCH_POLL_SECONDS,
    BACKGROUND_WAKE_POLL_SECONDS,
    PENDING_INTENT_EXPIRES_HOURS,
    PENDING_INTENT_NOT_BEFORE_MINUTES,
    WAKE_REPLY_COOLDOWN_MINUTES,
    ServiceError,
    debug_log,
)


class PendingIntentSelectionError(LLMError):
    def __init__(
        self,
        message: str,
        *,
        pending_intent_selection: dict[str, Any],
        failure_stage: str,
    ) -> None:
        super().__init__(message)
        self.pending_intent_selection = pending_intent_selection
        self.failure_stage = failure_stage


# 自発Mixin
class ServiceSpontaneousMixin:
    def trigger_wake(self, token: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # クライアントコンテキスト
        client_context = payload.get("client_context", {})
        if not isinstance(client_context, dict):
            raise ServiceError(400, "invalid_client_context", "The client_context field must be an object.")

        # 実行
        debug_log("Wake", f"manual trigger context_keys={self._debug_context_keys(client_context)}")
        return self._execute_wake_cycle(
            state=state,
            client_context=client_context,
            trigger_kind="wake",
        )

    def submit_capability_result(self, token: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        state = self._require_token(token)

        request_id = payload.get("request_id")
        client_id = payload.get("client_id")
        capability_id = payload.get("capability_id")
        result_payload = payload.get("result")

        if not isinstance(request_id, str) or not request_id.strip():
            raise ServiceError(400, "invalid_request_id", "request_id must be a non-empty string.")
        if not isinstance(client_id, str) or not client_id.strip():
            raise ServiceError(400, "invalid_client_id", "client_id must be a non-empty string.")
        if not isinstance(capability_id, str) or not capability_id.strip():
            raise ServiceError(400, "invalid_capability_id", "capability_id must be a non-empty string.")
        if not isinstance(result_payload, dict):
            raise ServiceError(400, "invalid_result_payload", "result must be an object.")

        normalized_request_id = request_id.strip()
        normalized_client_id = client_id.strip()
        normalized_capability_id = capability_id.strip()
        if capability_manifests().get(normalized_capability_id) is None:
            raise ServiceError(400, "invalid_capability_id", "capability_id is unknown.")
        normalized_result_payload = self._normalize_capability_result_payload(
            capability_id=normalized_capability_id,
            result_payload=result_payload,
        )
        accepted_at = self._now_iso()
        return self._submit_async_capability_result_response(
            state=state,
            capability_id=normalized_capability_id,
            request_id=normalized_request_id,
            client_id=normalized_client_id,
            result_payload=normalized_result_payload,
            accepted_at=accepted_at,
            log_channel=self._capability_result_log_channel(normalized_capability_id),
            accepted_detail=self._capability_result_accepted_detail(
                capability_id=normalized_capability_id,
                result_payload=normalized_result_payload,
            ),
        )

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
            if not isinstance(images, list):
                raise ServiceError(400, "invalid_capability_result", "vision.capture result.images must be an array.")
            if client_context is not None and not isinstance(client_context, dict):
                raise ServiceError(400, "invalid_capability_result", "vision.capture result.client_context must be an object.")
            if error is not None and not isinstance(error, str):
                raise ServiceError(400, "invalid_capability_result", "vision.capture result.error must be a string or null.")
            normalized_images: list[str] = []
            for image in images:
                if not isinstance(image, str) or not image.strip():
                    raise ServiceError(
                        400,
                        "invalid_capability_result",
                        "vision.capture result.images must contain non-empty strings.",
                    )
                normalized_images.append(image.strip())
            return {
                "images": normalized_images,
                "client_context": client_context or {},
                "error": error.strip() if isinstance(error, str) and error.strip() else None,
            }
        if capability_id == "external.status":
            status_text = result_payload.get("status_text")
            client_context = result_payload.get("client_context")
            error = result_payload.get("error")
            if not isinstance(status_text, str) or not status_text.strip():
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "external.status result.status_text must be a non-empty string.",
                )
            if client_context is not None and not isinstance(client_context, dict):
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "external.status result.client_context must be an object.",
                )
            if error is not None and not isinstance(error, str):
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "external.status result.error must be a string or null.",
                )
            return {
                "status_text": status_text.strip(),
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

    def _capability_result_log_channel(self, capability_id: str) -> str:
        if capability_id == "vision.capture":
            return "DesktopWatch"
        return "CapabilityResult"

    def _capability_result_accepted_detail(self, *, capability_id: str, result_payload: dict[str, Any]) -> str:
        if capability_id == "vision.capture":
            images = result_payload.get("images")
            image_count = len(images) if isinstance(images, list) else 0
            return f"images={image_count} error={bool(result_payload.get('error'))}"
        if capability_id == "external.status":
            status_text = result_payload.get("status_text")
            status_chars = len(status_text) if isinstance(status_text, str) else 0
            return f"status_chars={status_chars} error={bool(result_payload.get('error'))}"
        return f"result_keys={len(result_payload)} error={bool(result_payload.get('error'))}"

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
        self._execute_async_capability_result_cycle(
            state=state,
            capability_response=response,
            started_at=accepted_at,
        )

        return {}

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
                ),
                pending_intent_selection=pending_intent_selection,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
                ongoing_action_transition_summary=ongoing_action_transition_summary,
            )
            self._emit_capability_result_reply_event(
                capability_response=capability_response,
                pipeline=pipeline,
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
                detail_summary=self._capability_result_followup_detail_summary(decision=decision),
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
                decision=decision,
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
            "body_state_summary": client_context.get("body_state_summary"),
            "device_state_summary": client_context.get("device_state_summary"),
            "schedule_summary": client_context.get("schedule_summary"),
        }
        image_count = self._capability_result_payload_image_count(capability_response)
        if image_count is not None:
            summary["image_count"] = image_count
        return summary

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
        if capability_id == "vision.capture":
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
        elif capability_id == "external.status":
            client_context, observation_summary, input_text = self._prepare_external_status_result_context(
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
        source = self._client_context_text(client_context.get("source"), limit=48)
        image_count = self._capability_result_payload_image_count(capability_response)
        parts.append(f"{capability_id} の非同期結果を受け取った。")
        if isinstance(source, str):
            parts.append(f"入力源は {source}。")
        error = capability_response.get("error")
        if isinstance(error, str) and error.strip():
            parts.append(f"結果は error だった。 error={self._clamp(error, limit=120)}")
        status_text = self._capability_result_status_text(capability_response)
        if status_text is not None:
            parts.append(f"結果要約は {status_text}")
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
        decision: dict[str, Any],
        result_payload: dict[str, Any] | None = None,
    ) -> str | None:
        decision_reason = self._clamp(str(decision.get("reason_summary") or "").strip(), limit=160)
        if decision_reason:
            return decision_reason
        if isinstance(result_payload, dict):
            error = result_payload.get("error")
            if isinstance(error, str) and error.strip():
                return self._clamp(error.strip(), limit=160)
        return None

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

    def _emit_capability_result_reply_event(
        self,
        *,
        capability_response: dict[str, Any],
        pipeline: dict[str, Any],
    ) -> None:
        reply_payload = pipeline.get("reply_payload")
        if not isinstance(reply_payload, dict):
            debug_log("CapabilityResult", "reply_event skipped no_reply")
            return

        target_client_id = capability_response.get("client_id")
        if not isinstance(target_client_id, str) or not target_client_id.strip():
            debug_log("CapabilityResult", "reply_event skipped no_client")
            return

        request_record = capability_response.get("request_record")
        request_id = capability_response.get("request_id")
        capability_id = self._capability_result_capability_id(capability_response)
        if isinstance(request_record, dict):
            request_id = request_record.get("request_id", request_id)
        event = {
            "event_id": self._next_stream_event_id(),
            "type": "capability_result",
            "data": {
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
                f"reply_event sent={sent} client={target_client_id.strip()} "
                f"reply_chars={len(reply_payload['reply_text'])}"
            ),
        )

    def _execute_wake_cycle(
        self,
        *,
        state: dict[str, Any],
        client_context: dict[str, Any],
        trigger_kind: str,
    ) -> dict[str, Any]:
        # 直列化実行
        with self._wake_execution_lock:
            input_event_kind = "background_wake" if trigger_kind == "background_wake" else "wake"
            cycle_id = self._new_cycle_id()
            started_at = self._now_iso()
            recent_turns = self._load_recent_turns(state)
            runtime_summary = self._build_runtime_summary(state)
            pending_intent_selection = self._empty_pending_intent_selection_trace()
            input_text = self._build_wake_input_text(
                state=state,
                client_context=client_context,
                selected_candidate=None,
            )
            debug_log(
                "Wake",
                (
                    f"{self._short_cycle_id(cycle_id)} start trigger={trigger_kind} "
                    f"recent_turns={len(recent_turns)} context_keys={self._debug_context_keys(client_context)}"
                ),
            )

            try:
                # due / cooldown
                due = self._wake_is_due(state=state, current_time=started_at)
                if due["should_skip"]:
                    debug_log("Wake", f"{self._short_cycle_id(cycle_id)} skip due reason={self._clamp(due['reason_summary'])}")
                    pipeline = self._noop_pipeline(
                        state=state,
                        started_at=started_at,
                        reason_summary=due["reason_summary"],
                    )
                    return self._complete_input_success(
                        cycle_id=cycle_id,
                        started_at=started_at,
                        state=state,
                        runtime_summary=runtime_summary,
                        input_text=input_text,
                        client_context=client_context,
                        pipeline=pipeline,
                        trigger_kind=trigger_kind,
                        input_event_kind=input_event_kind,
                        input_event_role="system",
                        consolidate_memory=False,
                        pending_intent_selection=pending_intent_selection,
                    )
                cooldown_reason = self._wake_cooldown_reason(current_time=started_at)
                if cooldown_reason is not None:
                    self._set_last_wake_at(started_at)
                    debug_log(
                        "Wake",
                        f"{self._short_cycle_id(cycle_id)} skip cooldown reason={self._clamp(cooldown_reason)}",
                    )
                    pipeline = self._noop_pipeline(
                        state=state,
                        started_at=started_at,
                        reason_summary=cooldown_reason,
                    )
                    return self._complete_input_success(
                        cycle_id=cycle_id,
                        started_at=started_at,
                        state=state,
                        runtime_summary=runtime_summary,
                        input_text=input_text,
                        client_context=client_context,
                        pipeline=pipeline,
                        trigger_kind=trigger_kind,
                        input_event_kind=input_event_kind,
                        input_event_role="system",
                        consolidate_memory=False,
                        pending_intent_selection=pending_intent_selection,
                    )

                # パイプライン
                selection_result = self._select_due_pending_intent_candidate(
                    state=state,
                    trigger_kind=trigger_kind,
                    client_context=client_context,
                    recent_turns=recent_turns,
                    current_time=started_at,
                )
                selected_candidate = selection_result["selected_candidate"]
                pending_intent_selection = selection_result["pending_intent_selection"]
                debug_log(
                    "Wake",
                    (
                        f"{self._short_cycle_id(cycle_id)} selection "
                        f"pool={pending_intent_selection.get('candidate_pool_count', 0)} "
                        f"eligible={pending_intent_selection.get('eligible_candidate_count', 0)} "
                        f"selected={pending_intent_selection.get('selected_candidate_ref') or '-'}"
                    ),
                )
                pipeline, input_text = self._run_wake_pipeline(
                    state=state,
                    started_at=started_at,
                    trigger_kind=trigger_kind,
                    client_context=client_context,
                    recent_turns=recent_turns,
                    selected_candidate=selected_candidate,
                    pending_intent_selection=pending_intent_selection,
                    cycle_id=cycle_id,
                )

                # 成功
                response = self._complete_input_success(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    pipeline=pipeline,
                    trigger_kind=trigger_kind,
                    input_event_kind=input_event_kind,
                    input_event_role="system",
                    consolidate_memory=self._should_consolidate_spontaneous_cycle(
                        trigger_kind=trigger_kind,
                        pipeline=pipeline,
                        observation_summary=None,
                    ),
                    pending_intent_selection=pending_intent_selection,
                )

                # 返信後処理
                self._record_wake_outcome(
                    current_time=started_at,
                    decision=pipeline["decision"],
                    selected_candidate=selected_candidate,
                )
                debug_log(
                    "Wake",
                    f"{self._short_cycle_id(cycle_id)} done result={response['result_kind']}",
                )
                return response
            except PendingIntentSelectionError as exc:
                debug_log(
                    "Wake",
                    (
                        f"{self._short_cycle_id(cycle_id)} failed stage={exc.failure_stage} "
                        f"error={type(exc).__name__}: {self._clamp(str(exc))}"
                    ),
                )
                # 失敗永続化
                finished_at = self._now_iso()
                self._persist_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind=trigger_kind,
                    input_event_kind=input_event_kind,
                    input_event_role="system",
                    failure_event_kind="pending_intent_selection_failure",
                    failure_event_payload={
                        "failure_stage": exc.failure_stage,
                    },
                    pending_intent_selection=exc.pending_intent_selection,
                )
                self._emit_input_failure_logs(
                    cycle_id=cycle_id,
                    trigger_kind=trigger_kind,
                    input_text=input_text,
                    failure_reason=str(exc),
                    pending_intent_selection=exc.pending_intent_selection,
                )
                return {
                    "cycle_id": cycle_id,
                    "result_kind": "internal_failure",
                    "reply": None,
                    "capability_request": None,
                }
            except RecallPackSelectionError as exc:
                debug_log(
                    "Wake",
                    (
                        f"{self._short_cycle_id(cycle_id)} failed stage={exc.failure_stage} "
                        f"error={type(exc).__name__}: {self._clamp(str(exc))}"
                    ),
                )
                # 失敗永続化
                finished_at = self._now_iso()
                self._persist_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind=trigger_kind,
                    input_event_kind=input_event_kind,
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
                )
                self._emit_input_failure_logs(
                    cycle_id=cycle_id,
                    trigger_kind=trigger_kind,
                    input_text=input_text,
                    failure_reason=str(exc),
                    pending_intent_selection=pending_intent_selection,
                )
                return {
                    "cycle_id": cycle_id,
                    "result_kind": "internal_failure",
                    "reply": None,
                    "capability_request": None,
                }
            except (LLMError, KeyError, ValueError) as exc:
                capability_request_summary, ongoing_action_transition_summary = self._exception_capability_dispatch_trace(
                    exc
                )
                debug_log(
                    "Wake",
                    f"{self._short_cycle_id(cycle_id)} failed error={type(exc).__name__}: {self._clamp(str(exc))}",
                )
                # 失敗永続化
                finished_at = self._now_iso()
                self._persist_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=finished_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind=trigger_kind,
                    input_event_kind=input_event_kind,
                    input_event_role="system",
                    pending_intent_selection=pending_intent_selection,
                    capability_request_summary=capability_request_summary,
                    ongoing_action_transition_summary=ongoing_action_transition_summary,
                )
                self._emit_input_failure_logs(
                    cycle_id=cycle_id,
                    trigger_kind=trigger_kind,
                    input_text=input_text,
                    failure_reason=str(exc),
                    pending_intent_selection=pending_intent_selection,
                )
                return {
                    "cycle_id": cycle_id,
                    "result_kind": "internal_failure",
                    "reply": None,
                    "capability_request": None,
                }

    def _background_wake_loop(self, stop_event: threading.Event) -> None:
        # ループ
        while not stop_event.is_set():
            try:
                state = self.store.read_state()
                delay_seconds = self._background_wake_delay_seconds(state=state, current_time=self._now_iso())
                if delay_seconds > 0:
                    stop_event.wait(timeout=delay_seconds)
                    continue
                self._execute_wake_cycle(
                    state=state,
                    client_context={"source": "background_wake_scheduler"},
                    trigger_kind="background_wake",
                )
            except Exception as exc:  # noqa: BLE001
                debug_log("Wake", f"background loop error={type(exc).__name__}: {self._clamp(str(exc))}")
                stop_event.wait(timeout=BACKGROUND_WAKE_POLL_SECONDS)

    def _background_wake_delay_seconds(self, *, state: dict[str, Any], current_time: str) -> float:
        # 無効時
        wake_policy = state.get("wake_policy", {})
        if wake_policy.get("mode") != "interval":
            return BACKGROUND_WAKE_POLL_SECONDS

        # 初回起床
        with self._runtime_state_lock:
            last_wake_at = self._wake_runtime_state.get("last_wake_at")
        if not isinstance(last_wake_at, str) or not last_wake_at:
            return 0.0

        # 残り
        interval_seconds = int(wake_policy["interval_seconds"])
        current_dt = self._parse_iso(current_time)
        due_at = self._parse_iso(last_wake_at) + timedelta(seconds=interval_seconds)
        remaining_seconds = (due_at - current_dt).total_seconds()
        if remaining_seconds <= 0:
            return 0.0

        # ポーリング上限
        return min(remaining_seconds, BACKGROUND_WAKE_POLL_SECONDS)

    def _background_desktop_watch_loop(self, stop_event: threading.Event) -> None:
        # ループ
        while not stop_event.is_set():
            try:
                state = self.store.read_state()
                delay_seconds = self._background_desktop_watch_delay_seconds(
                    state=state,
                    current_time=self._now_iso(),
                )
                if delay_seconds > 0:
                    stop_event.wait(timeout=delay_seconds)
                    continue
                self._execute_desktop_watch_cycle(state=state)
            except Exception as exc:  # noqa: BLE001
                debug_log("DesktopWatch", f"background loop error={type(exc).__name__}: {self._clamp(str(exc))}")
                stop_event.wait(timeout=BACKGROUND_DESKTOP_WATCH_POLL_SECONDS)

    def _background_desktop_watch_delay_seconds(
        self,
        *,
        state: dict[str, Any],
        current_time: str,
    ) -> float:
        # 設定
        desktop_watch = state.get("desktop_watch", {})
        if not isinstance(desktop_watch, dict) or not desktop_watch.get("enabled"):
            return BACKGROUND_DESKTOP_WATCH_POLL_SECONDS
        if self._desktop_watch_target_client_id() is None:
            return BACKGROUND_DESKTOP_WATCH_POLL_SECONDS

        # 初回監視
        with self._runtime_state_lock:
            last_watch_at = self._desktop_watch_runtime_state.get("last_watch_at")
        if not isinstance(last_watch_at, str) or not last_watch_at:
            return 0.0

        # 残り
        interval_seconds = int(desktop_watch.get("interval_seconds", 1))
        current_dt = self._parse_iso(current_time)
        due_at = self._parse_iso(last_watch_at) + timedelta(seconds=interval_seconds)
        remaining_seconds = (due_at - current_dt).total_seconds()
        if remaining_seconds <= 0:
            return 0.0

        # ポーリング上限
        return min(remaining_seconds, BACKGROUND_DESKTOP_WATCH_POLL_SECONDS)

    def _execute_desktop_watch_cycle(self, *, state: dict[str, Any]) -> None:
        # 直列化実行
        with self._desktop_watch_execution_lock:
            desktop_watch = state.get("desktop_watch", {})
            if not isinstance(desktop_watch, dict) or not desktop_watch.get("enabled"):
                debug_log("DesktopWatch", "cycle skipped disabled")
                return
            target_client_id = self._desktop_watch_target_client_id()
            if target_client_id is None:
                debug_log("DesktopWatch", "cycle skipped no_capture_client")
                return

            # タイムスタンプ
            started_at = self._now_iso()
            debug_log("DesktopWatch", f"cycle start target_client={target_client_id}")
            cycle_id = self._new_cycle_id()
            runtime_summary = self._build_runtime_summary(state)
            pending_intent_selection = self._empty_pending_intent_selection_trace()
            client_context = {
                "source": "desktop_watch",
                "client_id": target_client_id,
            }
            input_text = self._build_desktop_watch_input_text(
                client_context=client_context,
                selected_candidate=None,
            )

            # キャプチャ
            try:
                capture_response = self._request_desktop_watch_capture(
                    memory_set_id=state["selected_memory_set_id"],
                    target_client_id=target_client_id,
                    current_time=started_at,
                )
            except ValueError as exc:
                capability_request_summary, ongoing_action_transition_summary = (
                    self._exception_capability_dispatch_trace(exc)
                )
                debug_log(
                    "DesktopWatch",
                    f"{self._short_cycle_id(cycle_id)} capture failed error={type(exc).__name__}: {self._clamp(str(exc))}",
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
                    trigger_kind="desktop_watch",
                    input_event_kind="desktop_watch",
                    input_event_role="system",
                    pending_intent_selection=pending_intent_selection,
                    capability_request_summary=capability_request_summary,
                    ongoing_action_transition_summary=ongoing_action_transition_summary,
                )
                self._emit_input_failure_logs(
                    cycle_id=cycle_id,
                    trigger_kind="desktop_watch",
                    input_text=input_text,
                    failure_reason=str(exc),
                    pending_intent_selection=pending_intent_selection,
                )
                return
            if capture_response is None:
                debug_log("DesktopWatch", "cycle skipped capture_unavailable")
                return
            if not capture_response["images"]:
                debug_log("DesktopWatch", "cycle skipped no_images")
                return

            # 成功タイムスタンプ
            self._set_last_desktop_watch_at(self._now_iso())

            client_context = self._build_desktop_watch_client_context(capture_response)
            observation_summary = self._desktop_watch_observation_summary(capture_response)
            capability_request_summary = self._capability_request_summary(capture_response.get("request_record"))
            ongoing_action_transition_summary = capture_response.get("ongoing_action_transition_summary")
            input_text = self._build_desktop_watch_input_text(client_context=client_context, selected_candidate=None)

            # スナップショット
            recent_turns = self._load_recent_turns(state)
            debug_log(
                "DesktopWatch",
                (
                    f"{self._short_cycle_id(cycle_id)} pipeline start images={len(capture_response['images'])} "
                    f"recent_turns={len(recent_turns)}"
                ),
            )

            try:
                # 画像観測要約
                client_context, observation_summary = self._interpret_desktop_watch_capture(
                    state=state,
                    started_at=started_at,
                    client_context=client_context,
                    observation_summary=observation_summary,
                    input_text=input_text,
                    capture_response=capture_response,
                )
                input_text = self._build_desktop_watch_input_text(
                    client_context=client_context,
                    selected_candidate=None,
                )

                # 候補選択
                selection_result = self._select_due_pending_intent_candidate(
                    state=state,
                    trigger_kind="desktop_watch",
                    client_context=client_context,
                    recent_turns=recent_turns,
                    current_time=started_at,
                )
                selected_candidate = selection_result["selected_candidate"]
                pending_intent_selection = selection_result["pending_intent_selection"]
                debug_log(
                    "DesktopWatch",
                    (
                        f"{self._short_cycle_id(cycle_id)} selection "
                        f"pool={pending_intent_selection.get('candidate_pool_count', 0)} "
                        f"eligible={pending_intent_selection.get('eligible_candidate_count', 0)} "
                        f"selected={pending_intent_selection.get('selected_candidate_ref') or '-'}"
                    ),
                )
                input_text = self._build_desktop_watch_input_text(
                    client_context=client_context,
                    selected_candidate=selected_candidate,
                )

                # パイプライン
                pipeline = self._run_input_pipeline(
                    state=state,
                    started_at=started_at,
                    input_text=input_text,
                    recent_turns=recent_turns,
                    cycle_id=cycle_id,
                    trigger_kind="desktop_watch",
                    client_context=client_context,
                    selected_candidate=selected_candidate,
                    pending_intent_selection=pending_intent_selection,
                    observation_summary=observation_summary,
                    capability_request_summary=capability_request_summary,
                )

                # 成功
                self._complete_input_success(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    pipeline=pipeline,
                    trigger_kind="desktop_watch",
                    input_event_kind="desktop_watch",
                    input_event_role="system",
                    consolidate_memory=self._should_consolidate_spontaneous_cycle(
                        trigger_kind="desktop_watch",
                        pipeline=pipeline,
                        observation_summary=observation_summary,
                    ),
                    pending_intent_selection=pending_intent_selection,
                    observation_summary=observation_summary,
                    capability_request_summary=capability_request_summary,
                    ongoing_action_transition_summary=ongoing_action_transition_summary,
                )
                self._record_wake_outcome(
                    current_time=started_at,
                    decision=pipeline["decision"],
                    selected_candidate=selected_candidate,
                )
                self._emit_desktop_watch_reply_event(
                    capture_response=capture_response,
                    pipeline=pipeline,
                )
                debug_log(
                    "DesktopWatch",
                    f"{self._short_cycle_id(cycle_id)} done decision={pipeline['decision']['kind']}",
                )
            except PendingIntentSelectionError as exc:
                debug_log(
                    "DesktopWatch",
                    (
                        f"{self._short_cycle_id(cycle_id)} failed stage={exc.failure_stage} "
                        f"error={type(exc).__name__}: {self._clamp(str(exc))}"
                    ),
                )
                # 失敗
                self._persist_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=self._now_iso(),
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind="desktop_watch",
                    input_event_kind="desktop_watch",
                    input_event_role="system",
                    failure_event_kind="pending_intent_selection_failure",
                    failure_event_payload={
                        "failure_stage": exc.failure_stage,
                    },
                    pending_intent_selection=exc.pending_intent_selection,
                    observation_summary=observation_summary,
                    capability_request_summary=capability_request_summary,
                    ongoing_action_transition_summary=ongoing_action_transition_summary,
                )
                self._emit_input_failure_logs(
                    cycle_id=cycle_id,
                    trigger_kind="desktop_watch",
                    input_text=input_text,
                    failure_reason=str(exc),
                    pending_intent_selection=exc.pending_intent_selection,
                )
            except RecallPackSelectionError as exc:
                debug_log(
                    "DesktopWatch",
                    (
                        f"{self._short_cycle_id(cycle_id)} failed stage={exc.failure_stage} "
                        f"error={type(exc).__name__}: {self._clamp(str(exc))}"
                    ),
                )
                # 失敗
                self._persist_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=self._now_iso(),
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind="desktop_watch",
                    input_event_kind="desktop_watch",
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
                    trigger_kind="desktop_watch",
                    input_text=input_text,
                    failure_reason=str(exc),
                    pending_intent_selection=pending_intent_selection,
                )
            except (LLMError, KeyError, ValueError) as exc:
                failed_capability_request_summary, failed_transition_summary = self._exception_capability_dispatch_trace(
                    exc
                )
                if isinstance(failed_transition_summary, dict):
                    ongoing_action_transition_summary = failed_transition_summary
                debug_log(
                    "DesktopWatch",
                    f"{self._short_cycle_id(cycle_id)} failed error={type(exc).__name__}: {self._clamp(str(exc))}",
                )
                # 失敗
                self._persist_cycle_failure(
                    cycle_id=cycle_id,
                    started_at=started_at,
                    finished_at=self._now_iso(),
                    state=state,
                    runtime_summary=runtime_summary,
                    input_text=input_text,
                    client_context=client_context,
                    failure_reason=str(exc),
                    trigger_kind="desktop_watch",
                    input_event_kind="desktop_watch",
                    input_event_role="system",
                    pending_intent_selection=pending_intent_selection,
                    observation_summary=observation_summary,
                    capability_request_summary=failed_capability_request_summary or capability_request_summary,
                    ongoing_action_transition_summary=ongoing_action_transition_summary,
                )
                self._emit_input_failure_logs(
                    cycle_id=cycle_id,
                    trigger_kind="desktop_watch",
                    input_text=input_text,
                    failure_reason=str(exc),
                    pending_intent_selection=pending_intent_selection,
                )

    def _pending_intent_trace_summary(
        self,
        *,
        cycle_id: str,
        decision: dict[str, Any],
    ) -> dict[str, Any] | None:
        # 確認
        if decision.get("kind") != "pending_intent":
            return None
        pending_intent = decision.get("pending_intent")
        if not isinstance(pending_intent, dict):
            return None

        # 結果
        return {
            "source_cycle_id": cycle_id,
            "intent_kind": pending_intent.get("intent_kind"),
            "intent_summary": pending_intent.get("intent_summary"),
            "reason_summary": decision.get("reason_summary"),
            "dedupe_key": pending_intent.get("dedupe_key"),
        }

    def _select_due_pending_intent_candidate(
        self,
        *,
        state: dict[str, Any],
        trigger_kind: str,
        client_context: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        current_time: str,
    ) -> dict[str, Any]:
        # 初期状態
        trace = self._empty_pending_intent_selection_trace()
        memory_set_id = state["selected_memory_set_id"]

        # 候補群
        candidate_pool = self._pending_intent_candidate_pool(
            memory_set_id=memory_set_id,
            current_time=current_time,
        )
        trace["candidate_pool_count"] = len(candidate_pool)
        current_dt = self._parse_iso(current_time)
        eligible_candidates = [
            candidate
            for candidate in candidate_pool
            if not isinstance(candidate.get("not_before"), str)
            or not candidate["not_before"]
            or self._parse_iso(candidate["not_before"]) <= current_dt
        ]
        trace["eligible_candidate_count"] = len(eligible_candidates)
        debug_log(
            "PendingIntent",
            (
                f"selection start trigger={trigger_kind} pool={len(candidate_pool)} "
                f"eligible={len(eligible_candidates)}"
            ),
        )
        if not eligible_candidates:
            debug_log("PendingIntent", f"selection skipped trigger={trigger_kind} reason=no_eligible_candidates")
            return {
                "selected_candidate": None,
                "pending_intent_selection": trace,
            }

        # source pack
        try:
            source_pack = self._build_pending_intent_selection_source_pack(
                state=state,
                trigger_kind=trigger_kind,
                client_context=client_context,
                recent_turns=recent_turns,
                candidates=eligible_candidates,
                current_time=current_time,
            )
        except (KeyError, TypeError, ValueError) as exc:
            trace["result_status"] = "failed"
            trace["failure_reason"] = str(exc)
            debug_log(
                "PendingIntent",
                f"selection failed trigger={trigger_kind} stage=build_source_pack error={self._clamp(str(exc))}",
            )
            raise PendingIntentSelectionError(
                str(exc),
                pending_intent_selection=trace,
                failure_stage="build_source_pack",
            ) from exc

        # 選択
        role_definition = state["model_presets"][state["selected_model_preset_id"]]["roles"]["pending_intent_selection"]
        try:
            payload = self.llm.generate_pending_intent_selection(
                role_definition=role_definition,
                source_pack=source_pack,
            )
        except LLMContractError as exc:
            trace["result_status"] = "failed"
            trace["failure_reason"] = str(exc)
            debug_log(
                "PendingIntent",
                f"selection failed trigger={trigger_kind} stage=contract_validation error={self._clamp(str(exc))}",
            )
            raise PendingIntentSelectionError(
                str(exc),
                pending_intent_selection=trace,
                failure_stage="contract_validation",
            ) from exc
        except LLMError as exc:
            trace["result_status"] = "failed"
            trace["failure_reason"] = str(exc)
            debug_log(
                "PendingIntent",
                f"selection failed trigger={trigger_kind} stage=llm_generation error={self._clamp(str(exc))}",
            )
            raise PendingIntentSelectionError(
                str(exc),
                pending_intent_selection=trace,
                failure_stage="llm_generation",
            ) from exc

        # 反映
        try:
            selection_result = self._apply_pending_intent_selection(
                payload=payload,
                source_pack=source_pack,
                candidates=eligible_candidates,
            )
        except (KeyError, TypeError, ValueError) as exc:
            trace["result_status"] = "failed"
            trace["failure_reason"] = str(exc)
            debug_log(
                "PendingIntent",
                f"selection failed trigger={trigger_kind} stage=apply_selection error={self._clamp(str(exc))}",
            )
            raise PendingIntentSelectionError(
                str(exc),
                pending_intent_selection=trace,
                failure_stage="apply_selection",
            ) from exc

        # 結果
        trace["selected_candidate_ref"] = selection_result["selected_candidate_ref"]
        trace["selection_reason"] = selection_result["selection_reason"]
        trace["result_status"] = "succeeded"
        selected_candidate = selection_result["selected_candidate"]
        if selected_candidate is not None:
            trace["selected_candidate_id"] = selected_candidate.get("candidate_id")
        debug_log(
            "PendingIntent",
            (
                f"selection done trigger={trigger_kind} selected={trace.get('selected_candidate_ref') or '-'} "
                f"candidate_id={trace.get('selected_candidate_id') or '-'}"
            ),
        )
        return {
            "selected_candidate": selected_candidate,
            "pending_intent_selection": trace,
        }

    def _pending_intent_candidate_pool(
        self,
        *,
        memory_set_id: str,
        current_time: str,
    ) -> list[dict[str, Any]]:
        # ロック下読み取り
        with self._runtime_state_lock:
            self._prune_pending_intent_candidates(current_time=current_time)
            return [
                dict(candidate)
                for candidate in self._pending_intent_candidates
                if candidate.get("memory_set_id") == memory_set_id
            ]

    def _build_pending_intent_selection_source_pack(
        self,
        *,
        state: dict[str, Any],
        trigger_kind: str,
        client_context: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        current_time: str,
    ) -> dict[str, Any]:
        return {
            "trigger_kind": trigger_kind,
            "input_context": self._build_pending_intent_selection_input_context(
                state=state,
                trigger_kind=trigger_kind,
                client_context=client_context,
                current_time=current_time,
            ),
            "recent_turns": self._pending_intent_selection_recent_turns(recent_turns),
            "selection_policy": {
                "allow_none": True,
                "max_selected_candidates": 1,
            },
            "candidates": [
                self._pending_intent_selection_candidate_source_item(
                    candidate_ref=f"candidate:{index}",
                    candidate=candidate,
                    current_time=current_time,
                )
                for index, candidate in enumerate(candidates, start=1)
            ],
        }

    def _build_pending_intent_selection_input_context(
        self,
        *,
        state: dict[str, Any],
        trigger_kind: str,
        client_context: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": self._client_context_text(client_context.get("source"), limit=48) or trigger_kind,
        }
        active_app = self._client_context_text(client_context.get("active_app"), limit=80)
        if active_app is not None:
            payload["active_app"] = active_app
        window_title = self._client_context_text(client_context.get("window_title"), limit=120)
        if window_title is not None:
            payload["window_title"] = window_title
        locale = self._client_context_text(client_context.get("locale"), limit=32)
        if locale is not None:
            payload["locale"] = locale
        image_count = client_context.get("image_count")
        if trigger_kind == "desktop_watch" and isinstance(image_count, int) and image_count >= 0:
            payload["image_count"] = image_count
        drive_state_summary = self._summarize_drive_states(
            self._list_current_drive_states(
                state=state,
                current_time=current_time,
            )
        )
        if drive_state_summary:
            payload["drive_state_summary"] = drive_state_summary
        ongoing_action_summary = self._summarize_ongoing_action(
            self._current_ongoing_action(
                state=state,
                current_time=current_time,
            )
        )
        if isinstance(ongoing_action_summary, dict):
            payload["ongoing_action_summary"] = ongoing_action_summary
        return payload

    def _pending_intent_selection_recent_turns(self, recent_turns: list[dict[str, Any]]) -> list[dict[str, str]]:
        compact_turns: list[dict[str, str]] = []
        for turn in recent_turns[-4:]:
            if not isinstance(turn, dict):
                continue
            role = turn.get("role")
            text = turn.get("text")
            if not isinstance(role, str) or not role.strip():
                continue
            if not isinstance(text, str) or not text.strip():
                continue
            compact_turns.append(
                {
                    "role": role.strip(),
                    "text": self._clamp(text.strip(), limit=120),
                }
            )
        return compact_turns

    def _pending_intent_selection_candidate_source_item(
        self,
        *,
        candidate_ref: str,
        candidate: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        intent_kind = candidate.get("intent_kind")
        intent_summary = candidate.get("intent_summary")
        reason_summary = candidate.get("reason_summary")
        created_at = candidate.get("created_at")
        updated_at = candidate.get("updated_at") or created_at
        expires_at = candidate.get("expires_at")
        if not isinstance(intent_kind, str) or not intent_kind.strip():
            raise ValueError("pending_intent candidate.intent_kind is invalid.")
        if not isinstance(intent_summary, str) or not intent_summary.strip():
            raise ValueError("pending_intent candidate.intent_summary is invalid.")
        if not isinstance(reason_summary, str) or not reason_summary.strip():
            raise ValueError("pending_intent candidate.reason_summary is invalid.")
        if not isinstance(created_at, str) or not created_at.strip():
            raise ValueError("pending_intent candidate.created_at is invalid.")
        if not isinstance(updated_at, str) or not updated_at.strip():
            raise ValueError("pending_intent candidate.updated_at is invalid.")
        if not isinstance(expires_at, str) or not expires_at.strip():
            raise ValueError("pending_intent candidate.expires_at is invalid.")
        return {
            "candidate_ref": candidate_ref,
            "intent_kind": intent_kind.strip(),
            "intent_summary": self._clamp(intent_summary.strip(), limit=120),
            "reason_summary": self._clamp(reason_summary.strip(), limit=160),
            "minutes_since_created": self._pending_intent_selection_minutes_since(
                current_time=current_time,
                timestamp=created_at,
            ),
            "minutes_since_updated": self._pending_intent_selection_minutes_since(
                current_time=current_time,
                timestamp=updated_at,
            ),
            "minutes_until_expiry": self._pending_intent_selection_minutes_until(
                current_time=current_time,
                timestamp=expires_at,
            ),
        }

    def _pending_intent_selection_minutes_since(
        self,
        *,
        current_time: str,
        timestamp: str,
    ) -> int:
        delta_seconds = (self._parse_iso(current_time) - self._parse_iso(timestamp)).total_seconds()
        return max(0, int(delta_seconds // 60))

    def _pending_intent_selection_minutes_until(
        self,
        *,
        current_time: str,
        timestamp: str,
    ) -> int:
        delta_seconds = (self._parse_iso(timestamp) - self._parse_iso(current_time)).total_seconds()
        return max(0, int(delta_seconds // 60))

    def _apply_pending_intent_selection(
        self,
        *,
        payload: dict[str, Any],
        source_pack: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # lookup
        candidate_lookup = {
            source_candidate["candidate_ref"]: dict(candidate)
            for source_candidate, candidate in zip(source_pack["candidates"], candidates, strict=True)
        }

        # 結果
        selected_candidate_ref = str(payload["selected_candidate_ref"]).strip()
        selection_reason = str(payload["selection_reason"]).strip()
        if selected_candidate_ref == "none":
            return {
                "selected_candidate_ref": "none",
                "selected_candidate": None,
                "selection_reason": selection_reason,
            }
        return {
            "selected_candidate_ref": selected_candidate_ref,
            "selected_candidate": candidate_lookup[selected_candidate_ref],
            "selection_reason": selection_reason,
        }

    def _apply_pending_intent_candidate(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        decision: dict[str, Any],
        occurred_at: str,
    ) -> dict[str, Any] | None:
        # 確認
        base_summary = self._pending_intent_trace_summary(cycle_id=cycle_id, decision=decision)
        if base_summary is None:
            return None

        # ロック下upsert
        with self._runtime_state_lock:
            self._prune_pending_intent_candidates(current_time=occurred_at)
            existing = self._find_pending_intent_candidate(
                memory_set_id=memory_set_id,
                dedupe_key=base_summary["dedupe_key"],
                current_time=occurred_at,
            )
            not_before = self._pending_intent_not_before(occurred_at)
            expires_at = self._pending_intent_expires_at(occurred_at)
            if existing is None:
                candidate = {
                    "candidate_id": f"pending_intent_candidate:{uuid.uuid4().hex}",
                    "memory_set_id": memory_set_id,
                    "intent_kind": base_summary["intent_kind"],
                    "intent_summary": base_summary["intent_summary"],
                    "reason_summary": base_summary["reason_summary"],
                    "source_cycle_id": cycle_id,
                    "not_before": not_before,
                    "expires_at": expires_at,
                    "dedupe_key": base_summary["dedupe_key"],
                    "created_at": occurred_at,
                    "updated_at": occurred_at,
                }
                self._pending_intent_candidates.append(candidate)
                queue_action = "created"
            else:
                candidate = existing
                candidate.update(
                    {
                        "intent_kind": base_summary["intent_kind"],
                        "intent_summary": base_summary["intent_summary"],
                        "reason_summary": base_summary["reason_summary"],
                        "source_cycle_id": cycle_id,
                        "not_before": not_before,
                        "expires_at": expires_at,
                        "updated_at": occurred_at,
                    }
                )
                queue_action = "updated"

            # 結果
            return {
                **base_summary,
                "candidate_id": candidate["candidate_id"],
                "queue_action": queue_action,
                "not_before": candidate["not_before"],
                "expires_at": candidate["expires_at"],
            }

    def _record_wake_outcome(
        self,
        *,
        current_time: str,
        decision: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
    ) -> None:
        # 返信
        if decision.get("kind") == "reply":
            with self._runtime_state_lock:
                self._wake_runtime_state["last_spontaneous_at"] = current_time
                self._wake_runtime_state["cooldown_until"] = self._wake_cooldown_until(current_time)
                if selected_candidate is not None:
                    dedupe_key = selected_candidate.get("dedupe_key")
                    if isinstance(dedupe_key, str) and dedupe_key:
                        reply_history = self._wake_runtime_state.setdefault("reply_history_by_dedupe", {})
                        reply_history[dedupe_key] = current_time
                    self._remove_pending_intent_candidate(selected_candidate.get("candidate_id"))
            return

        # 将来行動
        if decision.get("kind") == "pending_intent":
            return

    def _set_last_desktop_watch_at(self, current_time: str) -> None:
        # 更新
        with self._runtime_state_lock:
            self._desktop_watch_runtime_state["last_watch_at"] = current_time

    def _request_desktop_watch_capture(
        self,
        *,
        memory_set_id: str,
        target_client_id: str,
        current_time: str,
    ) -> dict[str, Any] | None:
        # リクエスト
        try:
            selected_client_id = self._select_capability_target_client(capability_id="vision.capture")
        except ValueError:
            return None
        if target_client_id != selected_client_id:
            return None
        result = self._dispatch_capability_request(
            memory_set_id=memory_set_id,
            capability_id="vision.capture",
            input_payload={
                "source": "desktop",
                "mode": "still",
            },
            current_time=current_time,
            goal_summary="desktop_watch で現在の画面状況を観測する。",
            wait_for_response=True,
            component="DesktopWatch",
        )
        if not isinstance(result, dict):
            return None
        request_record = result.get("request_record")
        debug_log(
            "DesktopWatch",
            (
                f"capture received request={result.get('request_id')} target_client={target_client_id} "
                f"images={len(result.get('images', [])) if isinstance(result.get('images'), list) else 0} "
                f"error={bool(result.get('error'))}"
            ),
        )
        result["ongoing_action_transition_summary"] = self._finish_capability_ongoing_action(
            request_record=request_record,
            current_time=self._now_iso(),
            terminal_kind="completed" if result.get("error") in {None, ""} else "interrupted",
            reason_code=self._desktop_watch_capture_reason_code(result),
            terminal_reason=self._desktop_watch_capture_terminal_reason(result),
            final_step_summary=self._desktop_watch_capture_terminal_step_summary(result),
            transition_source="capability_result",
            result_error=result.get("error") not in {None, ""},
            detail_summary=self._desktop_watch_capture_detail_summary(result),
        )
        return result

    def _build_desktop_watch_client_context(self, capture_response: dict[str, Any]) -> dict[str, Any]:
        # source取得
        client_context = capture_response.get("client_context", {})
        if not isinstance(client_context, dict):
            client_context = {}

        # 結果
        return {
            "source": "desktop_watch",
            "client_id": capture_response.get("client_id"),
            "active_app": client_context.get("active_app"),
            "window_title": client_context.get("window_title"),
            "locale": client_context.get("locale"),
            "image_count": len(capture_response.get("images", [])),
            "external_service_summary": client_context.get("external_service_summary"),
            "body_state_summary": client_context.get("body_state_summary"),
            "device_state_summary": client_context.get("device_state_summary"),
            "schedule_summary": client_context.get("schedule_summary"),
        }

    def _desktop_watch_observation_summary(self, capture_response: dict[str, Any]) -> dict[str, Any]:
        request_record = capture_response.get("request_record")
        summary = {
            "source": "desktop_watch",
            "capability_id": "vision.capture",
            "image_count": len(capture_response.get("images", [])),
            "image_interpreted": False,
            "error": capture_response.get("error"),
        }
        if isinstance(request_record, dict) and isinstance(request_record.get("capability_id"), str):
            summary["capability_id"] = request_record["capability_id"]
        client_id = capture_response.get("client_id")
        if isinstance(client_id, str) and client_id.strip():
            summary["client_id"] = client_id.strip()
        client_context = capture_response.get("client_context", {})
        if isinstance(client_context, dict):
            for key in ("active_app", "window_title", "locale"):
                value = client_context.get(key)
                if isinstance(value, str) and value.strip():
                    summary[key] = value.strip()
        return summary

    def _interpret_desktop_watch_capture(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        input_text: str,
        capture_response: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        images = self._normalize_visual_observation_images(
            capture_response.get("images", []),
            allow_missing=True,
        )
        return self._interpret_visual_observation(
            state=state,
            started_at=started_at,
            trigger_kind="desktop_watch",
            client_context=client_context,
            observation_summary=observation_summary,
            input_text=input_text,
            images=images,
        )

    def _desktop_watch_capture_terminal_reason(self, capture_response: dict[str, Any]) -> str:
        reason_code = self._desktop_watch_capture_reason_code(capture_response)
        return self._capability_terminal_transition_reason_summary(
            reason_code=reason_code,
            result_error=reason_code == "result_error",
        )

    def _desktop_watch_capture_reason_code(self, capture_response: dict[str, Any]) -> str:
        capture_error = capture_response.get("error")
        if isinstance(capture_error, str) and capture_error.strip():
            return "result_error"
        image_count = len(capture_response.get("images", []))
        if image_count <= 0:
            return "result_empty"
        return "result_received"

    def _desktop_watch_capture_detail_summary(self, capture_response: dict[str, Any]) -> str | None:
        capture_error = capture_response.get("error")
        if isinstance(capture_error, str) and capture_error.strip():
            return self._clamp(capture_error.strip(), limit=160)
        image_count = len(capture_response.get("images", []))
        if image_count <= 0:
            return "vision.capture の結果は空だった。"
        return None

    def _desktop_watch_capture_terminal_step_summary(self, capture_response: dict[str, Any]) -> str:
        capture_error = capture_response.get("error")
        if isinstance(capture_error, str) and capture_error.strip():
            return "vision.capture が error で終了した。"
        image_count = len(capture_response.get("images", []))
        if image_count <= 0:
            return "vision.capture の結果は空だった。"
        return "vision.capture の結果を受け取った。"

    def _build_desktop_watch_input_text(
        self,
        *,
        client_context: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
    ) -> str:
        # プレフィックス
        parts = ["desktop_watch 観測。"]
        parts.extend(
            self._client_context_input_parts(
                client_context=client_context,
                include_source=False,
                include_capture=True,
            )
        )
        if selected_candidate is not None:
            parts.append(self._wake_input_text(selected_candidate))
            parts.append("いま保留中の会話候補を再評価したい。")
        else:
            parts.append("drive_state と world_state を見て、今は前へ出る価値があるかを見たい。")
        return " ".join(parts)

    def _emit_desktop_watch_reply_event(
        self,
        *,
        capture_response: dict[str, Any],
        pipeline: dict[str, Any],
    ) -> None:
        # 確認
        reply_payload = pipeline.get("reply_payload")
        if not isinstance(reply_payload, dict):
            debug_log("DesktopWatch", "reply_event skipped no_reply")
            return

        # クライアント
        target_client_id = capture_response.get("client_id")
        if not isinstance(target_client_id, str) or not target_client_id.strip():
            debug_log("DesktopWatch", "reply_event skipped no_client")
            return

        # コンテキスト
        client_context = capture_response.get("client_context", {})
        if not isinstance(client_context, dict):
            client_context = {}
        window_title = client_context.get("window_title")
        active_app = client_context.get("active_app")
        summary = None
        if isinstance(window_title, str) and window_title.strip():
            summary = window_title.strip()
        elif isinstance(active_app, str) and active_app.strip():
            summary = active_app.strip()

        # イベント
        event = {
            "event_id": self._next_stream_event_id(),
            "type": "desktop_watch",
            "data": {
                "system_text": f"[desktop_watch] {summary}" if isinstance(summary, str) and summary else "[desktop_watch]",
                "message": reply_payload["reply_text"],
                "images": capture_response.get("images", []),
            },
        }
        sent = self._event_stream_registry.send_to_client(target_client_id.strip(), event)
        debug_log(
            "DesktopWatch",
            (
                f"reply_event sent={sent} client={target_client_id.strip()} "
                f"reply_chars={len(reply_payload['reply_text'])}"
            ),
        )

    def _desktop_watch_target_client_id(self) -> str | None:
        # 接続中で vision.capture を持つ client が 1 台だけなら採用する
        return self._event_stream_registry.find_single_client_with_capability("vision.capture")

    def _next_stream_event_id(self) -> int:
        # カウンター
        with self._stream_event_lock:
            event_id = self._next_stream_event_value
            self._next_stream_event_value += 1
        return event_id

    def _set_last_wake_at(self, current_time: str) -> None:
        # 更新
        with self._runtime_state_lock:
            self._wake_runtime_state["last_wake_at"] = current_time

    def _wake_is_due(self, *, state: dict[str, Any], current_time: str) -> dict[str, Any]:
        # 無効時
        wake_policy = state.get("wake_policy", {})
        if wake_policy.get("mode") != "interval":
            return {
                "should_skip": True,
                "reason_summary": "wake_policy が disabled のため、自発判断は止まっている。",
            }

        # 初回起床
        with self._runtime_state_lock:
            last_wake_at = self._wake_runtime_state.get("last_wake_at")
        if not isinstance(last_wake_at, str) or not last_wake_at:
            return {
                "should_skip": False,
                "reason_summary": None,
            }

        # 間隔
        interval_seconds = wake_policy["interval_seconds"]
        current_dt = self._parse_iso(current_time)
        due_at = self._parse_iso(last_wake_at) + timedelta(seconds=int(interval_seconds))
        if current_dt < due_at:
            return {
                "should_skip": True,
                "reason_summary": "interval wake の次回時刻にまだ達していない。",
            }

        # 期限到来
        return {
            "should_skip": False,
            "reason_summary": None,
        }

    def _wake_cooldown_reason(self, *, current_time: str) -> str | None:
        # 検索
        with self._runtime_state_lock:
            cooldown_until = self._wake_runtime_state.get("cooldown_until")
        if not isinstance(cooldown_until, str) or not cooldown_until:
            return None
        if self._parse_iso(current_time) < self._parse_iso(cooldown_until):
            return "直近の自発 reply から cooldown 中のため、今回は再介入しない。"
        return None

    def _was_recently_replied(self, *, dedupe_key: str, current_time: str) -> bool:
        # 検索
        with self._runtime_state_lock:
            reply_history = self._wake_runtime_state.setdefault("reply_history_by_dedupe", {})
            last_reply_at = reply_history.get(dedupe_key)
        if not isinstance(last_reply_at, str) or not last_reply_at:
            return False
        current_dt = self._parse_iso(current_time)
        return current_dt - self._parse_iso(last_reply_at) < timedelta(minutes=WAKE_REPLY_COOLDOWN_MINUTES)

    def _wake_cooldown_until(self, current_time: str) -> str:
        # タイムスタンプ
        return (self._parse_iso(current_time) + timedelta(minutes=WAKE_REPLY_COOLDOWN_MINUTES)).isoformat()

    def _wake_input_text(self, candidate: dict[str, Any]) -> str:
        # intent判定
        intent_kind = candidate.get("intent_kind", "conversation_follow_up")
        if intent_kind == "conversation_follow_up":
            return "約束の続きとして会話を再開したい。いま話しかける価値があるかを見たい。"
        return "定期起床。未完了の保留候補を再評価したい。"

    def _build_wake_input_text(
        self,
        *,
        state: dict[str, Any],
        client_context: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
    ) -> str:
        # プレフィックス
        parts = ["定期起床。"]
        persona = state["personas"][state["selected_persona_id"]]
        parts.append(f"initiative_baseline は {persona['initiative_baseline']}。")
        parts.extend(
            self._client_context_input_parts(
                client_context=client_context,
                include_source=True,
                include_capture=False,
            )
        )
        if selected_candidate is not None:
            parts.append(self._wake_input_text(selected_candidate))
            parts.append("いま保留中の会話候補を再評価したい。")
        else:
            parts.append("drive_state と world_state を見て、今は前へ出る価値があるかを見たい。")
        return " ".join(parts)

    def _client_context_input_parts(
        self,
        *,
        client_context: dict[str, Any],
        include_source: bool,
        include_capture: bool,
    ) -> list[str]:
        # 項目
        source = self._client_context_text(client_context.get("source"), limit=48)
        active_app = self._client_context_text(client_context.get("active_app"), limit=80)
        window_title = self._client_context_text(client_context.get("window_title"), limit=120)
        locale = self._client_context_text(client_context.get("locale"), limit=32)
        parts: list[str] = []

        # source取得
        if include_source and isinstance(source, str):
            if source == "background_wake_scheduler":
                parts.append("入力源は background wake scheduler。")
            else:
                parts.append(f"入力源は {source}。")

        # 前景
        if isinstance(active_app, str):
            parts.append(f"前景アプリは {active_app}。")
        if isinstance(window_title, str):
            parts.append(f"ウィンドウタイトルは {window_title}。")

        # ロケール
        if isinstance(locale, str):
            parts.append(f"UIロケールは {locale}。")

        # キャプチャ
        if include_capture:
            image_count = client_context.get("image_count")
            if isinstance(image_count, int) and image_count > 0:
                parts.append(f"キャプチャ画像を {image_count} 件受け取った。")
            image_summary_text = self._client_context_text(client_context.get("image_summary_text"), limit=160)
            if isinstance(image_summary_text, str):
                parts.append(f"画像観測では、{image_summary_text}")

        # 結果
        return parts

    def _client_context_text(self, value: Any, *, limit: int) -> str | None:
        # 型
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        if not stripped:
            return None
        return self._clamp(stripped, limit=limit)

    def _remove_pending_intent_candidate(self, candidate_id: Any) -> None:
        # 確認
        if not isinstance(candidate_id, str) or not candidate_id:
            return
        with self._runtime_state_lock:
            self._pending_intent_candidates = [
                candidate
                for candidate in self._pending_intent_candidates
                if candidate.get("candidate_id") != candidate_id
            ]

    def _find_pending_intent_candidate(
        self,
        *,
        memory_set_id: str,
        dedupe_key: str,
        current_time: str,
    ) -> dict[str, Any] | None:
        # ロック下走査
        with self._runtime_state_lock:
            current_dt = self._parse_iso(current_time)
            for candidate in self._pending_intent_candidates:
                if candidate.get("memory_set_id") != memory_set_id:
                    continue
                if candidate.get("dedupe_key") != dedupe_key:
                    continue
                expires_at = candidate.get("expires_at")
                if isinstance(expires_at, str) and expires_at and self._parse_iso(expires_at) <= current_dt:
                    continue
                return candidate
            return None

    def _prune_pending_intent_candidates(self, *, current_time: str) -> None:
        # ロック下絞り込み
        with self._runtime_state_lock:
            current_dt = self._parse_iso(current_time)
            self._pending_intent_candidates = [
                candidate
                for candidate in self._pending_intent_candidates
                if not isinstance(candidate.get("expires_at"), str)
                or self._parse_iso(candidate["expires_at"]) > current_dt
            ]

    def _clear_pending_intent_candidates(self) -> None:
        # リセット
        with self._runtime_state_lock:
            self._pending_intent_candidates = []
            self._wake_runtime_state = {
                "last_wake_at": None,
                "last_spontaneous_at": None,
                "cooldown_until": None,
                "reply_history_by_dedupe": {},
            }
            self._desktop_watch_runtime_state = {
                "last_watch_at": None,
            }

    def _pending_intent_not_before(self, occurred_at: str) -> str:
        # オフセット
        return (self._parse_iso(occurred_at) + timedelta(minutes=PENDING_INTENT_NOT_BEFORE_MINUTES)).isoformat()

    def _pending_intent_expires_at(self, occurred_at: str) -> str:
        # オフセット
        return (self._parse_iso(occurred_at) + timedelta(hours=PENDING_INTENT_EXPIRES_HOURS)).isoformat()
