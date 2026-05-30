from __future__ import annotations

from typing import Any

from otomekairo.llm.client import LLMError
from otomekairo.recall.builder import RecallPackSelectionError
from otomekairo.service.common import ServiceError, debug_log


class ServiceInputCycleMixin:
    # 入力API
    def handle_conversation(self, token: str | None, payload: dict) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # 検証
        input_text = payload.get("text")
        client_context = payload.get("client_context", {})
        input_images = self._normalize_visual_observation_images(payload.get("images"), allow_missing=True)
        if not isinstance(input_text, str):
            raise ServiceError(400, "invalid_text", "The text field must be a string.")
        if not isinstance(client_context, dict):
            raise ServiceError(400, "invalid_client_context", "The client_context field must be an object.")

        current_client_context = dict(client_context)
        observation_summary: dict[str, Any] | None = None

        # スナップショット
        cycle_id = self._new_cycle_id()
        started_at = self._now_iso()
        recent_turns = self._load_recent_turns(state)
        runtime_summary = self._build_runtime_summary(state)
        debug_log(
            "Conversation",
            (
                f"{self._short_cycle_id(cycle_id)} start input_chars={len(input_text)} "
                f"recent_turns={len(recent_turns)} context_keys={self._debug_context_keys(client_context)}"
            ),
        )
        self._emit_live_log(
            level="INFO",
            component="Input",
            message=f"{self._short_cycle_id(cycle_id)} user_message input={self._conversation_log_excerpt(input_text)}",
        )

        self._begin_user_response_cycle()
        try:
            # 会話添付画像は capability 実行ではなく、会話入力の補助要約として扱う。
            if input_images:
                current_client_context["image_count"] = len(input_images)
                observation_summary = {
                    "source": "conversation_attachment",
                    "image_input_kind": "conversation_attachment",
                    "image_count": len(input_images),
                    "image_interpreted": False,
                    "error": None,
                }
                current_client_context, observation_summary = self._interpret_visual_observation(
                    state=state,
                    started_at=started_at,
                    trigger_kind="user_message",
                    client_context=current_client_context,
                    observation_summary=observation_summary,
                    input_text=input_text,
                    images=input_images,
                )

            # パイプライン
            pipeline = self._run_input_pipeline(
                state=state,
                started_at=started_at,
                input_text=input_text,
                recent_turns=recent_turns,
                cycle_id=cycle_id,
                trigger_kind="user_message",
                client_context=current_client_context,
                observation_summary=observation_summary,
            )

            # 成功
            response = self._complete_input_success(
                cycle_id=cycle_id,
                started_at=started_at,
                state=state,
                runtime_summary=runtime_summary,
                input_text=input_text,
                client_context=current_client_context,
                pipeline=pipeline,
                observation_summary=observation_summary,
            )
            debug_log(
                "Conversation",
                f"{self._short_cycle_id(cycle_id)} done result={response['result_kind']}",
            )
            return response
        except RecallPackSelectionError as exc:
            debug_log(
                "Conversation",
                (
                    f"{self._short_cycle_id(cycle_id)} failed stage={exc.failure_stage} "
                    f"error={type(exc).__name__}: {self._clamp(str(exc))}"
                ),
            )
            return self._finalize_cycle_failure(
                cycle_id=cycle_id,
                started_at=started_at,
                state=state,
                runtime_summary=runtime_summary,
                input_text=input_text,
                client_context=current_client_context,
                failure_reason=str(exc),
                recall_trace=self._build_failure_recall_trace(
                    recall_hint=exc.recall_hint_summary,
                    recall_pack_selection=exc.recall_pack_selection,
                ),
                failure_event_kind="recall_pack_selection_failure",
                failure_event_payload={
                    "failure_stage": exc.failure_stage,
                },
                observation_summary=observation_summary,
            )
        except (LLMError, KeyError, ValueError) as exc:
            debug_log(
                "Conversation",
                f"{self._short_cycle_id(cycle_id)} failed error={type(exc).__name__}: {self._clamp(str(exc))}",
            )
            capability_request_summary, ongoing_action_transition_summary = self._exception_capability_dispatch_trace(
                exc
            )
            return self._finalize_cycle_failure(
                cycle_id=cycle_id,
                started_at=started_at,
                state=state,
                runtime_summary=runtime_summary,
                input_text=input_text,
                client_context=current_client_context,
                failure_reason=str(exc),
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
                ongoing_action_transition_summary=ongoing_action_transition_summary,
            )
        finally:
            self._end_user_response_cycle()

    def _finalize_cycle_failure(
        self,
        *,
        cycle_id: str,
        started_at: str,
        state: dict[str, Any],
        runtime_summary: dict[str, Any],
        input_text: str,
        client_context: dict[str, Any],
        failure_reason: str,
        trigger_kind: str | None = None,
        input_event_kind: str | None = None,
        input_event_role: str | None = None,
        recall_trace: dict[str, Any] | None = None,
        failure_event_kind: str | None = None,
        failure_event_payload: dict[str, Any] | None = None,
        observation_summary: dict[str, Any] | None = None,
        pending_intent_selection: dict[str, Any] | None = None,
        capability_request_summary: dict[str, Any] | None = None,
        ongoing_action_transition_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        finished_at = self._now_iso()
        persist_kwargs: dict[str, Any] = {
            "cycle_id": cycle_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "state": state,
            "runtime_summary": runtime_summary,
            "input_text": input_text,
            "client_context": client_context,
            "failure_reason": failure_reason,
            "observation_summary": observation_summary,
            "pending_intent_selection": pending_intent_selection,
            "capability_request_summary": capability_request_summary,
            "ongoing_action_transition_summary": ongoing_action_transition_summary,
        }
        if trigger_kind is not None:
            persist_kwargs["trigger_kind"] = trigger_kind
        if input_event_kind is not None:
            persist_kwargs["input_event_kind"] = input_event_kind
        if input_event_role is not None:
            persist_kwargs["input_event_role"] = input_event_role
        if recall_trace is not None:
            persist_kwargs["recall_trace"] = recall_trace
        if failure_event_kind is not None:
            persist_kwargs["failure_event_kind"] = failure_event_kind
        if failure_event_payload is not None:
            persist_kwargs["failure_event_payload"] = failure_event_payload
        self._persist_cycle_failure(**persist_kwargs)
        emit_kwargs: dict[str, Any] = {
            "cycle_id": cycle_id,
            "input_text": input_text,
            "failure_reason": failure_reason,
        }
        if trigger_kind is not None:
            emit_kwargs["trigger_kind"] = trigger_kind
        if pending_intent_selection is not None:
            emit_kwargs["pending_intent_selection"] = pending_intent_selection
        self._emit_input_failure_logs(**emit_kwargs)
        return {
            "cycle_id": cycle_id,
            "result_kind": "internal_failure",
            "reply": None,
            "capability_request": None,
        }
