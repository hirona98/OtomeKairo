"""Execute minimal cognition flow."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from otomekairo.gateway.cognition_client import CognitionClient, CognitionRequest
from otomekairo.schema.runtime_types import ActionHistoryRecord, PendingInputRecord


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
    response_parts: list[str] = []
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

    # Block: Streaming response loop
    stream_started = False
    if consume_cancel(message_id):
        was_cancelled = True
    else:
        for chunk_text in cognition_client.stream_text(request):
            if consume_cancel(message_id):
                was_cancelled = True
                break
            if not stream_started:
                emit_event(
                    "status",
                    {
                        "status_code": "speaking",
                        "label": "応答を返しています",
                        "cycle_id": cycle_id,
                    },
                )
                stream_started = True
            response_parts.append(chunk_text)
            emit_event(
                "token",
                {
                    "message_id": message_id,
                    "text": chunk_text,
                    "chunk_index": emitted_chunk_count,
                },
            )
            emitted_chunk_count += 1

    # Block: Structured cognition result
    response_text = "".join(response_parts).strip()
    if not was_cancelled and not response_text:
        raise RuntimeError("cognition stream returned empty response")
    cognition_result = _build_cognition_result(
        pending_input=pending_input,
        cycle_id=cycle_id,
        message_id=message_id,
        response_text=response_text,
        emitted_chunk_count=emitted_chunk_count,
        was_cancelled=was_cancelled,
    )

    # Block: Final message
    message_created_at = _now_ms()
    if response_text and not was_cancelled:
        emit_event(
            "message",
            {
                "message_id": message_id,
                "role": "assistant",
                "text": str(cognition_result["speech_draft"]["text"]),
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
    action_results = [
        ActionHistoryRecord(
            result_id=_opaque_id("actres"),
            command_id=_opaque_id("cmd"),
            action_type="emit_chat_response",
            command={
                "target_channel": pending_input.channel,
                "event_types": emitted_event_types,
                "message_id": message_id,
                "role": "assistant",
                "related_input_id": pending_input.input_id,
            },
            started_at=resolved_at,
            finished_at=message_created_at,
            status="stopped" if was_cancelled else "succeeded",
            failure_mode="cancelled" if was_cancelled else None,
            observed_effects={
                "emitted_event_types": emitted_event_types,
                "message_id": message_id,
                "status_code_after": "idle",
                "was_cancelled": was_cancelled,
                "token_count": emitted_chunk_count,
                "final_message_emitted": bool(response_text and not was_cancelled),
            },
            raw_result_ref=None,
            adapter_trace_ref={"cognition_result": cognition_result},
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


# Block: Cognition result builder
def _build_cognition_result(
    *,
    pending_input: PendingInputRecord,
    cycle_id: str,
    message_id: str,
    response_text: str,
    emitted_chunk_count: int,
    was_cancelled: bool,
) -> dict[str, Any]:
    return {
        "intention_summary": "browser_chat に対して人格として応答する",
        "decision_reason": "最新のテキスト入力を受け取り、現在の人格断面に基づいて返答を選ぶ",
        "action_proposals": [
            {
                "action_type": "speak",
                "target_channel": pending_input.channel,
                "message_id": message_id,
                "priority": 1.0,
            }
        ],
        "step_hints": [],
        "speech_draft": {
            "text": response_text,
            "language": "ja",
            "delivery_mode": "stream",
        },
        "memory_focus": {
            "focus_kind": "current_input_only",
            "summary": "直近のチャット入力を主材料として判断した",
        },
        "reflection_seed": {
            "cycle_id": cycle_id,
            "input_kind": str(pending_input.payload["input_kind"]),
            "message_id": message_id,
            "token_count": emitted_chunk_count,
            "was_cancelled": was_cancelled,
        },
    }


# Block: Time helper
def _now_ms() -> int:
    return int(time.time() * 1000)
