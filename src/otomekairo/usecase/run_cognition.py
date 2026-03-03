"""Execute minimal cognition flow."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from otomekairo.gateway.cognition_client import CognitionClient, CognitionRequest
from otomekairo.schema.runtime_types import ActionHistoryRecord, PendingInputRecord
from otomekairo.usecase.validate_action import validate_chat_response_action


# Block: Cognition execution
@dataclass(frozen=True, slots=True)
class CognitionExecution:
    cognition_input: dict[str, Any]
    cognition_result: dict[str, Any]
    ui_events: list[dict[str, Any]]
    action_results: list[ActionHistoryRecord]


# Block: Chat cognition execution
def run_cognition_for_chat_message(
    *,
    pending_input: PendingInputRecord,
    cycle_id: str,
    resolved_at: int,
    cognition_input: dict[str, Any],
    cognition_client: CognitionClient,
    emit_ui_event: Callable[[dict[str, Any]], None],
    consume_cancel: Callable[[str], bool],
) -> CognitionExecution:
    request = CognitionRequest(
        cycle_id=cycle_id,
        input_kind=str(pending_input.payload["input_kind"]),
        cognition_input=cognition_input,
    )
    message_id = _opaque_id("msg")
    ui_events: list[dict[str, Any]] = []
    emitted_chunk_count = 0
    was_cancelled = False

    # Block: Immediate event emitter
    def emit_event(event_type: str, payload: dict[str, Any]) -> None:
        ui_event = {
            "channel": pending_input.channel,
            "event_type": event_type,
            "payload": payload,
        }
        ui_events.append(ui_event)
        emit_ui_event(ui_event)

    # Block: Initial status
    emit_event(
        "status",
        {
            "status_code": "thinking",
            "label": "入力を処理しています",
            "cycle_id": cycle_id,
        },
    )

    # Block: Structured cognition result
    cognition_result = cognition_client.generate_result(request).cognition_result
    speech_draft = _validated_speech_draft(cognition_result)
    response_text = str(speech_draft["text"]).strip()
    _merge_reflection_seed(
        cognition_result=cognition_result,
        pending_input=pending_input,
        cycle_id=cycle_id,
        message_id=message_id,
    )
    validated_action = validate_chat_response_action(
        pending_channel=pending_input.channel,
        message_id=message_id,
        cognition_input=cognition_input,
        cognition_result=cognition_result,
        response_text=response_text,
    )
    active_message_id = (
        str(validated_action.proposal["message_id"])
        if validated_action.proposal is not None
        else message_id
    )
    action_command = validated_action.action_command
    command_type = (
        str(action_command["command_type"])
        if action_command is not None
        else None
    )
    should_stream_response = command_type == "speak_ui_message"
    should_emit_notice = command_type == "browser_notice"

    # Block: Streaming response loop
    if should_stream_response:
        emit_event(
            "status",
            {
                "status_code": "speaking",
                "label": "応答を返しています",
                "cycle_id": cycle_id,
            },
        )
        for chunk_text in _iter_speech_chunks(response_text):
            if consume_cancel(active_message_id):
                was_cancelled = True
                break
            emit_event(
                "token",
                {
                    "message_id": active_message_id,
                    "text": chunk_text,
                    "chunk_index": emitted_chunk_count,
                },
            )
            emitted_chunk_count += 1
    if should_emit_notice:
        emit_event(
            "notice",
            {
                "notice_code": str(action_command["notice_code"]),
                "text": str(action_command["text"]),
            },
        )
    cognition_result["reflection_seed"]["token_count"] = emitted_chunk_count
    cognition_result["reflection_seed"]["was_cancelled"] = was_cancelled

    # Block: Final message
    message_created_at = _now_ms()
    if response_text and should_stream_response and not was_cancelled:
        emit_event(
            "message",
            {
                "message_id": str(action_command["message_id"]),
                "role": "assistant",
                "text": response_text,
                "created_at": message_created_at,
                "source_cycle_id": cycle_id,
                "related_input_id": pending_input.input_id,
            },
        )

    # Block: Final status
    emit_event(
        "status",
        {
            "status_code": "idle",
            "label": "待機中",
            "cycle_id": cycle_id,
        },
    )

    # Block: Action history
    emitted_event_types = [ui_event["event_type"] for ui_event in ui_events]
    action_type = _action_history_type(
        decision=validated_action.decision,
        command_type=command_type,
    )
    command_payload = {
        "target_channel": pending_input.channel,
        "event_types": emitted_event_types,
        "decision": validated_action.decision,
        "decision_reason": validated_action.decision_reason,
        "related_input_id": pending_input.input_id,
    }
    observed_effects = {
        "emitted_event_types": emitted_event_types,
        "status_code_after": "idle",
        "was_cancelled": was_cancelled,
        "token_count": emitted_chunk_count,
        "final_message_emitted": bool(response_text and should_stream_response and not was_cancelled),
        "validator_decision": validated_action.decision,
        "validator_reason": validated_action.decision_reason,
        "action_candidate_score": validated_action.action_candidate_score,
    }
    if validated_action.proposal is not None:
        command_payload["proposal_ref"] = str(validated_action.proposal["proposal_id"])
        observed_effects["selected_action_type"] = str(validated_action.proposal["action_type"])
    if should_stream_response and action_command is not None:
        command_payload["command_type"] = str(action_command["command_type"])
        command_payload["message_id"] = str(action_command["message_id"])
        command_payload["role"] = "assistant"
        observed_effects["message_id"] = str(action_command["message_id"])
    if should_emit_notice and action_command is not None:
        command_payload["command_type"] = str(action_command["command_type"])
        command_payload["notice_code"] = str(action_command["notice_code"])
        command_payload["text"] = str(action_command["text"])
        observed_effects["notice_code"] = str(action_command["notice_code"])
    action_results = [
        ActionHistoryRecord(
            result_id=_opaque_id("actres"),
            command_id=_opaque_id("cmd"),
            action_type=action_type,
            command=command_payload,
            started_at=resolved_at,
            finished_at=message_created_at,
            status=_action_status(
                decision=validated_action.decision,
                was_cancelled=was_cancelled,
            ),
            failure_mode=_failure_mode(
                decision=validated_action.decision,
                was_cancelled=was_cancelled,
            ),
            observed_effects=observed_effects,
            raw_result_ref=None,
            adapter_trace_ref={
                "cognition_result": cognition_result,
                "action_command": action_command,
                "action_candidate_score": validated_action.action_candidate_score,
            },
        )
    ]
    return CognitionExecution(
        cognition_input=cognition_input,
        cognition_result=cognition_result,
        ui_events=ui_events,
        action_results=action_results,
    )


# Block: Id helper
def _opaque_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


# Block: Speech draft validation
def _validated_speech_draft(cognition_result: dict[str, Any]) -> dict[str, Any]:
    speech_draft = cognition_result.get("speech_draft")
    if not isinstance(speech_draft, dict):
        raise RuntimeError("cognition_result.speech_draft must be an object")
    speech_text = speech_draft.get("text")
    if not isinstance(speech_text, str) or not speech_text.strip():
        raise RuntimeError("cognition_result.speech_draft.text must be a non-empty string")
    return speech_draft


# Block: Reflection seed merge
def _merge_reflection_seed(
    *,
    cognition_result: dict[str, Any],
    pending_input: PendingInputRecord,
    cycle_id: str,
    message_id: str,
) -> None:
    existing_seed = cognition_result.get("reflection_seed")
    merged_seed = dict(existing_seed) if isinstance(existing_seed, dict) else {}
    merged_seed["cycle_id"] = cycle_id
    merged_seed["input_kind"] = str(pending_input.payload["input_kind"])
    merged_seed["message_id"] = message_id
    merged_seed["token_count"] = 0
    merged_seed["was_cancelled"] = False
    cognition_result["reflection_seed"] = merged_seed


# Block: Token chunk iterator
def _iter_speech_chunks(response_text: str) -> Iterable[str]:
    current_chunk = ""
    for character in response_text:
        current_chunk += character
        if character in "。！？\n" or len(current_chunk) >= 24:
            yield current_chunk
            current_chunk = ""
    if current_chunk:
        yield current_chunk


# Block: Action history type helper
def _action_history_type(*, decision: str, command_type: str | None) -> str:
    if decision == "hold":
        return "hold_chat_response"
    if decision == "reject":
        return "reject_chat_response"
    if command_type == "browser_notice":
        return "emit_browser_notice"
    return "emit_chat_response"


# Block: Action status helper
def _action_status(*, decision: str, was_cancelled: bool) -> str:
    if was_cancelled:
        return "stopped"
    return "succeeded"


# Block: Failure mode helper
def _failure_mode(*, decision: str, was_cancelled: bool) -> str | None:
    if was_cancelled:
        return "cancelled"
    return None


# Block: Time helper
def _now_ms() -> int:
    return int(time.time() * 1000)
