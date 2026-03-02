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

    # Block: Final message
    response_text = "".join(response_parts).strip()
    if not was_cancelled and not response_text:
        raise RuntimeError("cognition stream returned empty response")
    message_created_at = _now_ms()
    if response_text:
        emit_event(
            "message",
            {
                "message_id": message_id,
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
            },
            raw_result_ref=None,
            adapter_trace_ref=None,
        )
    ]
    return CognitionExecution(
        cognition_input=cognition_input,
        ui_events=ui_events,
        action_results=action_results,
    )


# Block: Id helper
def _opaque_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


# Block: Time helper
def _now_ms() -> int:
    return int(time.time() * 1000)
