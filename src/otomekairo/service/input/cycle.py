from __future__ import annotations

from typing import Any

from otomekairo.llm.client import LLMError
from otomekairo.interaction import InteractionContext, normalize_interaction_context
from otomekairo.recall.builder import RecallPackSelectionError
from otomekairo.service.common import ServiceError, debug_log


class ServiceInputCycleMixin:
    # 入力API
    def handle_conversation(
        self,
        token: str | None,
        payload: dict,
        *,
        defer_audio_delivery: bool = False,
    ) -> dict[str, Any]:
        # 一つの個の判断状態を会話到着順に更新する。
        self._cycle_coordinator.enter_foreground()
        try:
            response = self._handle_conversation_cycle(token, payload)
        finally:
            self._cycle_coordinator.leave_foreground()

        # 音声入力経路はassistant_message eventとの順序を保つため呼び出し側で配送する。
        if not defer_audio_delivery:
            self._attach_response_audio_delivery(
                response,
                source_kind="conversation",
            )
        return response

    def _handle_conversation_cycle(self, token: str | None, payload: dict) -> dict[str, Any]:
        # 認可
        state = self._require_token(token)

        # 検証
        input_text = payload.get("text")
        message_id = payload.get("message_id")
        client_context = payload.get("client_context", {})
        interaction_context = normalize_interaction_context(
            payload.get("interaction_context"),
            required=True,
            require_speaker=True,
        )
        input_images = self._normalize_visual_observation_images(payload.get("images"), allow_missing=True)
        if not isinstance(input_text, str):
            raise ServiceError(400, "invalid_text", "The text field must be a string.")
        if (
            not isinstance(message_id, str)
            or not message_id.startswith("chat_message:")
            or not message_id.removeprefix("chat_message:")
        ):
            raise ServiceError(
                400,
                "invalid_message_id",
                "message_id must use chat_message:<key> form.",
            )
        if not isinstance(client_context, dict):
            raise ServiceError(400, "invalid_client_context", "The client_context field must be an object.")
        autonomous_run_action = self._normalize_conversation_autonomous_run_action(
            payload.get("autonomous_run_action")
        )

        current_client_context = dict(client_context)
        observation_summary: dict[str, Any] | None = None

        # スナップショット
        cycle_id = self._new_cycle_id()
        started_at = self._now_iso()
        source_kind = client_context.get("source_kind", "user_message")
        if source_kind not in {
            "user_message",
            "local_microphone",
            "console_microphone",
            "web_microphone",
        }:
            raise ServiceError(
                400,
                "invalid_conversation_source_kind",
                "client_context.source_kind is unsupported.",
            )
        speaker = next(
            participant
            for participant in interaction_context.participants
            if participant.person_ref == interaction_context.speaker_ref
        )
        event_data: dict[str, Any] = {
            "message_id": message_id,
            "cycle_id": cycle_id,
            "created_at": started_at,
            "source_kind": source_kind,
            "source_client_id": client_context.get("client_id"),
            "message": input_text,
            "interaction_ref": interaction_context.interaction_ref,
            "speaker_ref": interaction_context.speaker_ref,
            "participant_refs": list(interaction_context.participant_refs),
            "display_name": speaker.display_name,
        }
        utterance_seq = client_context.get("utterance_seq")
        if isinstance(utterance_seq, int):
            event_data["utterance_seq"] = utterance_seq
        self._event_stream_registry.send_to_subscribers(
            "conversation_input",
            {
                "event_id": self._next_stream_event_id(),
                "type": "conversation_input",
                "data": event_data,
            },
        )
        recent_turns = self._load_recent_turns(state, interaction_context)
        runtime_summary = self._build_runtime_summary(state)
        cancel_autonomous_runs = autonomous_run_action == "cancel_all"
        self._begin_user_response_cycle()
        try:
            if cancel_autonomous_runs:
                self._cancel_autonomous_runs_for_user_request(
                    state=state,
                    current_time=started_at,
                )
            else:
                self._pause_autonomous_runs_for_user_interaction(
                    state=state,
                    current_time=started_at,
                )
            debug_log(
                "Conversation",
                (
                    f"{self._short_cycle_id(cycle_id)} start input_chars={len(input_text)} "
                    f"recent_turns={len(recent_turns)} context_keys={self._debug_context_keys(client_context)}"
                ),
                level="DEBUG",
            )
            self._emit_live_log(
                level="INFO",
                component="Input",
                message=f"{self._short_cycle_id(cycle_id)} user_message input={self._conversation_log_excerpt(input_text)}",
            )
        except Exception:  # noqa: BLE001
            self._end_user_response_cycle()
            raise
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
                interaction_context=interaction_context,
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
                interaction_context=interaction_context,
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
                level="ERROR",
            )
            return self._finalize_cycle_failure(
                cycle_id=cycle_id,
                started_at=started_at,
                state=state,
                runtime_summary=runtime_summary,
                input_text=input_text,
                client_context=current_client_context,
                interaction_context=interaction_context,
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
                level="ERROR",
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
                interaction_context=interaction_context,
                failure_reason=str(exc),
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
                ongoing_action_transition_summary=ongoing_action_transition_summary,
            )
        finally:
            self._end_user_response_cycle()
            if not cancel_autonomous_runs:
                self._resume_autonomous_runs_after_user_interaction(
                    state=state,
                    current_time=self._now_iso(),
                )

    def _normalize_conversation_autonomous_run_action(self, value: Any) -> str | None:
        # 自然文の意味推定ではなく、API 境界の明示コマンドだけを解釈する。
        if value is None:
            return None
        if not isinstance(value, dict) or set(value) != {"kind"}:
            raise ServiceError(
                400,
                "invalid_autonomous_run_action",
                "autonomous_run_action must contain only the kind field.",
            )
        kind = value.get("kind")
        if kind != "cancel_all":
            raise ServiceError(
                400,
                "invalid_autonomous_run_action",
                "autonomous_run_action.kind must be cancel_all.",
            )
        return kind

    def _finalize_cycle_failure(
        self,
        *,
        cycle_id: str,
        started_at: str,
        state: dict[str, Any],
        runtime_summary: dict[str, Any],
        input_text: str,
        client_context: dict[str, Any],
        interaction_context: InteractionContext | None,
        failure_reason: str,
        trigger_kind: str = "user_message",
        input_event_kind: str = "conversation_input",
        input_event_role: str = "person",
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
            "interaction_context": interaction_context,
            "failure_reason": failure_reason,
            "observation_summary": observation_summary,
            "pending_intent_selection": pending_intent_selection,
            "capability_request_summary": capability_request_summary,
            "ongoing_action_transition_summary": ongoing_action_transition_summary,
            "trigger_kind": trigger_kind,
            "input_event_kind": input_event_kind,
            "input_event_role": input_event_role,
        }
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
            "trigger_kind": trigger_kind,
        }
        if pending_intent_selection is not None:
            emit_kwargs["pending_intent_selection"] = pending_intent_selection
        self._emit_input_failure_logs(**emit_kwargs)
        return {
            "cycle_id": cycle_id,
            "interaction_ref": interaction_context.interaction_ref if interaction_context is not None else None,
            "recipient_person_refs": (
                list(interaction_context.participant_refs)
                if interaction_context is not None
                else []
            ),
            "result_kind": "internal_failure",
            "speech": None,
            "capability_request": None,
            "autonomous_run": None,
        }
