"""Execute minimal cognition flow."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

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
) -> CognitionExecution:
    cognition_response = cognition_client.complete(
        CognitionRequest(
            cycle_id=cycle_id,
            input_kind=str(pending_input.payload["input_kind"]),
            cognition_input=cognition_input,
        )
    )
    message_id = _opaque_id("msg")
    ui_events = [
        {
            "channel": pending_input.channel,
            "event_type": "status",
            "payload": {
                "status_code": "thinking",
                "label": "入力を処理しています",
                "cycle_id": cycle_id,
            },
        },
        {
            "channel": pending_input.channel,
            "event_type": "message",
            "payload": {
                "message_id": message_id,
                "role": cognition_response.response_role,
                "text": cognition_response.response_text,
                "created_at": resolved_at,
                "source_cycle_id": cycle_id,
                "related_input_id": pending_input.input_id,
            },
        },
        {
            "channel": pending_input.channel,
            "event_type": "status",
            "payload": {
                "status_code": "idle",
                "label": "待機中",
                "cycle_id": cycle_id,
            },
        },
    ]
    action_results = [
        ActionHistoryRecord(
            result_id=_opaque_id("actres"),
            command_id=_opaque_id("cmd"),
            action_type="emit_chat_response",
            command={
                "target_channel": pending_input.channel,
                "event_types": ["status", "message", "status"],
                "message_id": message_id,
                "role": cognition_response.response_role,
                "related_input_id": pending_input.input_id,
            },
            started_at=resolved_at,
            finished_at=resolved_at + 1,
            status="succeeded",
            failure_mode=None,
            observed_effects={
                "emitted_event_types": ["status", "message", "status"],
                "message_id": message_id,
                "status_code_after": "idle",
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
