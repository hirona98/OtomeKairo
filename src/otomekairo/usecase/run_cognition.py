"""Execute minimal cognition flow."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from otomekairo.gateway.camera_controller import CameraController
from otomekairo.gateway.cognition_client import CognitionClient, CognitionRequest
from otomekairo.gateway.speech_synthesizer import SpeechSynthesizer
from otomekairo.schema.runtime_types import ActionHistoryRecord, PendingInputRecord, TaskStateMutationRecord
from otomekairo.usecase.dispatch_action_command import ActionDispatchResult, dispatch_chat_action_command
from otomekairo.usecase.validate_action import validate_chat_response_action


# Block: Module logger
logger = logging.getLogger(__name__)


# Block: Cognition execution
@dataclass(frozen=True, slots=True)
class CognitionExecution:
    cognition_input: dict[str, Any]
    cognition_result: dict[str, Any]
    ui_events: list[dict[str, Any]]
    action_results: list[ActionHistoryRecord]
    task_mutations: list[TaskStateMutationRecord]


# Block: Browser chat cognition execution
def run_cognition_for_browser_chat_input(
    *,
    pending_input: PendingInputRecord,
    cycle_id: str,
    resolved_at: int,
    cognition_input: dict[str, Any],
    effective_settings: dict[str, Any],
    cognition_client: CognitionClient,
    camera_controller: CameraController,
    speech_synthesizer: SpeechSynthesizer,
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
            "label": _initial_status_label(pending_input),
            "cycle_id": cycle_id,
        },
    )

    # Block: Structured cognition result
    cognition_result = cognition_client.generate_result(request).cognition_result
    logger.debug(
        "cognition result generated",
        extra={
            "cycle_id": cycle_id,
            "input_id": pending_input.input_id,
            "input_kind": pending_input.payload["input_kind"],
            "intention_summary": cognition_result["intention_summary"],
            "memory_focus_kind": cognition_result["memory_focus"]["focus_kind"],
            "action_proposal_count": len(cognition_result["action_proposals"]),
        },
    )
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
    logger.info(
        "action decision resolved",
        extra={
            "cycle_id": cycle_id,
            "input_id": pending_input.input_id,
            "decision": validated_action.decision,
            "decision_reason": validated_action.decision_reason,
            "selected_action_type": (
                validated_action.proposal["action_type"]
                if validated_action.proposal is not None
                else None
            ),
            "command_type": (
                action_command["command_type"]
                if action_command is not None
                else None
            ),
        },
    )
    dispatch_result = _dispatch_result_for_decision(
        pending_input=pending_input,
        cycle_id=cycle_id,
        resolved_at=resolved_at,
        action_command=action_command,
        decision=validated_action.decision,
        emit_ui_event=emit_event,
        consume_cancel=lambda: consume_cancel(active_message_id),
        camera_controller=camera_controller,
        speech_synthesizer=speech_synthesizer,
        effective_settings=effective_settings,
    )
    cognition_result["reflection_seed"]["token_count"] = int(
        dispatch_result.observed_effects.get("token_count", 0)
    )
    cognition_result["reflection_seed"]["was_cancelled"] = bool(
        dispatch_result.observed_effects.get("was_cancelled", False)
    )
    logger.debug(
        "action dispatch finished",
        extra={
            "cycle_id": cycle_id,
            "input_id": pending_input.input_id,
            "action_type": dispatch_result.action_type,
            "dispatch_status": dispatch_result.status,
            "emitted_event_types": dispatch_result.emitted_event_types,
        },
    )

    # Block: Action history
    emitted_event_types = dispatch_result.emitted_event_types
    command_payload = _action_history_command(
        pending_input=pending_input,
        validated_action=validated_action,
        action_command=action_command,
        emitted_event_types=emitted_event_types,
    )
    observed_effects = {
        "emitted_event_types": emitted_event_types,
        **dispatch_result.observed_effects,
        "validator_decision": validated_action.decision,
        "validator_reason": validated_action.decision_reason,
        "action_candidate_score": validated_action.action_candidate_score,
    }
    if validated_action.proposal is not None:
        observed_effects["selected_action_type"] = str(validated_action.proposal["action_type"])
    action_results = [
        ActionHistoryRecord(
            result_id=_opaque_id("actres"),
            command_id=_action_command_id(
                decision=validated_action.decision,
                action_command=action_command,
            ),
            action_type=dispatch_result.action_type,
            command=command_payload,
            started_at=resolved_at,
            finished_at=dispatch_result.finished_at,
            status=dispatch_result.status,
            failure_mode=dispatch_result.failure_mode,
            observed_effects=observed_effects,
            raw_result_ref=dispatch_result.raw_result_ref,
            adapter_trace_ref={
                "cognition_result": cognition_result,
                "action_command": action_command,
                "action_candidate_score": validated_action.action_candidate_score,
                "dispatch_trace": dispatch_result.adapter_trace_ref,
            },
        )
    ]
    return CognitionExecution(
        cognition_input=cognition_input,
        cognition_result=cognition_result,
        ui_events=ui_events,
        action_results=action_results,
        task_mutations=dispatch_result.task_mutations,
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


# Block: Initial status label
def _initial_status_label(pending_input: PendingInputRecord) -> str:
    input_kind = str(pending_input.payload["input_kind"])
    if input_kind == "chat_message":
        return "入力を処理しています"
    if input_kind == "camera_observation":
        return "カメラ画像を観測しています"
    if input_kind == "network_result":
        return "検索結果をもとに応答を準備しています"
    raise RuntimeError("unsupported input_kind for cognition status")


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


# Block: Action history type helper
def _action_history_type(*, decision: str, command_type: str | None) -> str:
    if decision == "hold":
        return "hold_chat_response"
    if decision == "reject":
        return "reject_chat_response"
    if command_type == "enqueue_browse_task":
        return "enqueue_browse_task"
    if command_type == "dispatch_notice":
        return "dispatch_notice"
    if command_type == "control_camera_look":
        return "control_camera_look"
    return "emit_chat_response"


# Block: Dispatch selector
def _dispatch_result_for_decision(
    *,
    pending_input: PendingInputRecord,
    cycle_id: str,
    resolved_at: int,
    action_command: dict[str, Any] | None,
    decision: str,
    emit_ui_event: Callable[[str, dict[str, Any]], None],
    consume_cancel: Callable[[], bool],
    camera_controller: CameraController,
    speech_synthesizer: SpeechSynthesizer,
    effective_settings: dict[str, Any],
) -> ActionDispatchResult:
    if decision != "execute":
        emit_ui_event(
            "status",
            {
                "status_code": "idle",
                "label": "待機中",
                "cycle_id": cycle_id,
            },
        )
        command_type = (
            str(action_command["command_type"])
            if action_command is not None
            else None
        )
        return ActionDispatchResult(
            action_type=_action_history_type(
                decision=decision,
                command_type=command_type,
            ),
            emitted_event_types=["status"],
            observed_effects={
                "status_code_after": "idle",
            },
            task_mutations=[],
            finished_at=_now_ms(),
            status="succeeded",
            failure_mode=None,
            raw_result_ref=None,
            adapter_trace_ref=None,
        )
    if action_command is None:
        raise RuntimeError("execute decision requires action_command")
    return dispatch_chat_action_command(
        pending_input={
            "input_id": pending_input.input_id,
            "channel": pending_input.channel,
        },
        cycle_id=cycle_id,
        resolved_at=resolved_at,
        action_command=action_command,
        emit_ui_event=lambda ui_event: emit_ui_event(ui_event["event_type"], ui_event["payload"]),
        consume_cancel=lambda _: consume_cancel(),
        camera_controller=camera_controller,
        speech_synthesizer=speech_synthesizer,
        effective_settings=effective_settings,
    )


# Block: Action history command
def _action_history_command(
    *,
    pending_input: PendingInputRecord,
    validated_action: Any,
    action_command: dict[str, Any] | None,
    emitted_event_types: list[str],
) -> dict[str, Any]:
    if validated_action.decision != "execute" or action_command is None:
        command_payload = {
            "target_channel": pending_input.channel,
            "event_types": emitted_event_types,
            "decision": validated_action.decision,
            "decision_reason": validated_action.decision_reason,
            "related_input_id": pending_input.input_id,
        }
        if validated_action.proposal is not None:
            command_payload["proposal_ref"] = str(validated_action.proposal["proposal_id"])
        return command_payload
    return {
        **action_command,
        "event_types": emitted_event_types,
        "decision": validated_action.decision,
        "decision_reason": validated_action.decision_reason,
        "related_input_id": pending_input.input_id,
    }


# Block: Action command id
def _action_command_id(*, decision: str, action_command: dict[str, Any] | None) -> str:
    if decision == "execute":
        if action_command is None:
            raise RuntimeError("execute decision requires action_command")
        return str(action_command["command_id"])
    return _opaque_id("cmd")


# Block: Time helper
def _now_ms() -> int:
    return int(time.time() * 1000)
