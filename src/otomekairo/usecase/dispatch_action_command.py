"""Execute structured chat action commands."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from otomekairo.gateway.camera_controller import CameraController, CameraLookRequest
from otomekairo.gateway.speech_synthesizer import (
    SpeechSynthesisRequest,
    SpeechSynthesisResponse,
    SpeechSynthesizer,
)
from otomekairo.schema.runtime_types import TaskStateMutationRecord


# Block: Dispatch result
@dataclass(frozen=True, slots=True)
class ActionDispatchResult:
    action_type: str
    emitted_event_types: list[str]
    observed_effects: dict[str, Any]
    task_mutations: list[TaskStateMutationRecord]
    finished_at: int
    status: str
    failure_mode: str | None
    raw_result_ref: dict[str, Any] | None
    adapter_trace_ref: dict[str, Any] | None


# Block: Public dispatcher
def dispatch_chat_action_command(
    *,
    pending_input: dict[str, Any],
    cycle_id: str,
    resolved_at: int,
    action_command: dict[str, Any],
    emit_ui_event: Callable[[dict[str, Any]], None],
    consume_cancel: Callable[[str], bool],
    camera_controller: CameraController,
    speech_synthesizer: SpeechSynthesizer,
    effective_settings: dict[str, Any],
) -> ActionDispatchResult:
    command_type = str(action_command["command_type"])
    if command_type == "speak_ui_message":
        return _dispatch_speak_command(
            pending_input=pending_input,
            cycle_id=cycle_id,
            resolved_at=resolved_at,
            action_command=action_command,
            emit_ui_event=emit_ui_event,
            consume_cancel=consume_cancel,
            speech_synthesizer=speech_synthesizer,
            effective_settings=effective_settings,
        )
    if command_type == "dispatch_notice":
        return _dispatch_notice_command(
            pending_input=pending_input,
            cycle_id=cycle_id,
            action_command=action_command,
            emit_ui_event=emit_ui_event,
        )
    if command_type == "control_camera_look":
        return _dispatch_camera_look_command(
            pending_input=pending_input,
            cycle_id=cycle_id,
            action_command=action_command,
            emit_ui_event=emit_ui_event,
            camera_controller=camera_controller,
        )
    if command_type == "enqueue_browse_task":
        return _dispatch_browse_task_command(
            pending_input=pending_input,
            cycle_id=cycle_id,
            resolved_at=resolved_at,
            action_command=action_command,
            emit_ui_event=emit_ui_event,
        )
    raise RuntimeError("unsupported action_command.command_type")


# Block: Speak command dispatch
def _dispatch_speak_command(
    *,
    pending_input: dict[str, Any],
    cycle_id: str,
    resolved_at: int,
    action_command: dict[str, Any],
    emit_ui_event: Callable[[dict[str, Any]], None],
    consume_cancel: Callable[[str], bool],
    speech_synthesizer: SpeechSynthesizer,
    effective_settings: dict[str, Any],
) -> ActionDispatchResult:
    message_id = str(action_command["parameters"]["message_id"])
    response_text = str(action_command["parameters"]["text"])
    emitted_event_types: list[str] = []
    emitted_chunk_count = 0
    was_cancelled = False

    # Block: Speaking status
    _emit_browser_event(
        pending_input=pending_input,
        event_type="status",
        payload={
            "status_code": "speaking",
            "label": "応答を返しています",
            "cycle_id": cycle_id,
        },
        emit_ui_event=emit_ui_event,
        emitted_event_types=emitted_event_types,
    )

    # Block: Cloud speech synthesis
    synthesis_result: SpeechSynthesisResponse | None = None
    try:
        synthesis_result = _synthesize_speech_if_enabled(
            cycle_id=cycle_id,
            message_id=message_id,
            response_text=response_text,
            speech_synthesizer=speech_synthesizer,
            effective_settings=effective_settings,
        )
    except Exception as error:
        # Block: TTS failure event
        _emit_browser_event(
            pending_input=pending_input,
            event_type="error",
            payload={
                "error_code": "tts_synthesis_failed",
                "message": f"クラウドTTSに失敗しました: {_error_message_text(error)}",
                "retriable": False,
            },
            emit_ui_event=emit_ui_event,
            emitted_event_types=emitted_event_types,
        )
        # Block: Idle status after TTS failure
        _emit_browser_event(
            pending_input=pending_input,
            event_type="status",
            payload={
                "status_code": "idle",
                "label": "待機中",
                "cycle_id": cycle_id,
            },
            emit_ui_event=emit_ui_event,
            emitted_event_types=emitted_event_types,
        )
        return ActionDispatchResult(
            action_type="emit_chat_response",
            emitted_event_types=emitted_event_types,
            observed_effects={
                "status_code_after": "idle",
                "was_cancelled": False,
                "token_count": 0,
                "final_message_emitted": False,
                "message_id": message_id,
                "tts_enabled": bool(effective_settings.get("speech.tts.enabled")),
                "tts_audio_generated": False,
            },
            task_mutations=[],
            finished_at=_now_ms(),
            status="failed",
            failure_mode="tts_synthesis_failed",
            raw_result_ref=None,
            adapter_trace_ref={
                "tts_error": {
                    "error_kind": type(error).__name__,
                    "error_message": _error_message_text(error),
                }
            },
        )

    # Block: Token streaming
    for chunk_text in _iter_speech_chunks(response_text):
        if consume_cancel(message_id):
            was_cancelled = True
            break
        _emit_browser_event(
            pending_input=pending_input,
            event_type="token",
            payload={
                "message_id": message_id,
                "text": chunk_text,
                "chunk_index": emitted_chunk_count,
            },
            emit_ui_event=emit_ui_event,
            emitted_event_types=emitted_event_types,
        )
        emitted_chunk_count += 1

    # Block: Final message
    finished_at = _now_ms()
    final_message_emitted = False
    if response_text and not was_cancelled:
        final_message_payload = {
            "message_id": message_id,
            "role": "assistant",
            "text": response_text,
            "created_at": finished_at,
            "source_cycle_id": cycle_id,
            "related_input_id": str(pending_input["input_id"]),
        }
        if synthesis_result is not None:
            final_message_payload["audio_url"] = synthesis_result.audio_url
            final_message_payload["audio_mime_type"] = synthesis_result.mime_type
        _emit_browser_event(
            pending_input=pending_input,
            event_type="message",
            payload=final_message_payload,
            emit_ui_event=emit_ui_event,
            emitted_event_types=emitted_event_types,
        )
        final_message_emitted = True

    # Block: Idle status
    _emit_browser_event(
        pending_input=pending_input,
        event_type="status",
        payload={
            "status_code": "idle",
            "label": "待機中",
            "cycle_id": cycle_id,
        },
        emit_ui_event=emit_ui_event,
        emitted_event_types=emitted_event_types,
    )

    return ActionDispatchResult(
        action_type="emit_chat_response",
        emitted_event_types=emitted_event_types,
        observed_effects={
            "status_code_after": "idle",
            "was_cancelled": was_cancelled,
            "token_count": emitted_chunk_count,
            "final_message_emitted": final_message_emitted,
            "message_id": message_id,
            "tts_enabled": bool(effective_settings.get("speech.tts.enabled")),
            "tts_audio_generated": synthesis_result is not None,
        },
        task_mutations=[],
        finished_at=finished_at,
        status="stopped" if was_cancelled else "succeeded",
        failure_mode="cancelled" if was_cancelled else None,
        raw_result_ref=synthesis_result.raw_result_ref if synthesis_result is not None else None,
        adapter_trace_ref=synthesis_result.adapter_trace_ref if synthesis_result is not None else None,
    )


# Block: Notice command dispatch
def _dispatch_notice_command(
    *,
    pending_input: dict[str, Any],
    cycle_id: str,
    action_command: dict[str, Any],
    emit_ui_event: Callable[[dict[str, Any]], None],
) -> ActionDispatchResult:
    emitted_event_types: list[str] = []
    notice_code = str(action_command["parameters"]["notice_code"])
    notice_text = str(action_command["parameters"]["text"])

    # Block: Notice output
    _emit_browser_event(
        pending_input=pending_input,
        event_type="notice",
        payload={
            "notice_code": notice_code,
            "text": notice_text,
        },
        emit_ui_event=emit_ui_event,
        emitted_event_types=emitted_event_types,
    )

    # Block: Idle status
    _emit_browser_event(
        pending_input=pending_input,
        event_type="status",
        payload={
            "status_code": "idle",
            "label": "待機中",
            "cycle_id": cycle_id,
        },
        emit_ui_event=emit_ui_event,
        emitted_event_types=emitted_event_types,
    )

    return ActionDispatchResult(
        action_type="dispatch_notice",
        emitted_event_types=emitted_event_types,
        observed_effects={
            "status_code_after": "idle",
            "notice_code": notice_code,
        },
        task_mutations=[],
        finished_at=_now_ms(),
        status="succeeded",
        failure_mode=None,
        raw_result_ref=None,
        adapter_trace_ref=None,
    )


# Block: Camera look dispatch
def _dispatch_camera_look_command(
    *,
    pending_input: dict[str, Any],
    cycle_id: str,
    action_command: dict[str, Any],
    emit_ui_event: Callable[[dict[str, Any]], None],
    camera_controller: CameraController,
) -> ActionDispatchResult:
    emitted_event_types: list[str] = []
    message_id = str(action_command["parameters"]["message_id"])
    response_text = str(action_command["parameters"]["text"])

    # Block: Camera move status
    _emit_browser_event(
        pending_input=pending_input,
        event_type="status",
        payload={
            "status_code": "camera_moving",
            "label": "カメラを動かしています",
            "cycle_id": cycle_id,
        },
        emit_ui_event=emit_ui_event,
        emitted_event_types=emitted_event_types,
    )

    # Block: Camera move execution
    try:
        look_response = camera_controller.move_view(
            CameraLookRequest(
                cycle_id=cycle_id,
                direction=_optional_action_text(action_command["parameters"], "direction"),
                preset_id=_optional_action_text(action_command["parameters"], "preset_id"),
                preset_name=_optional_action_text(action_command["parameters"], "preset_name"),
            )
        )
    except Exception as error:
        # Block: Camera move failure event
        _emit_browser_event(
            pending_input=pending_input,
            event_type="error",
            payload={
                "error_code": "camera_move_failed",
                "message": _error_message_text(error),
                "retriable": False,
            },
            emit_ui_event=emit_ui_event,
            emitted_event_types=emitted_event_types,
        )

        # Block: Idle status after camera failure
        _emit_browser_event(
            pending_input=pending_input,
            event_type="status",
            payload={
                "status_code": "idle",
                "label": "待機中",
                "cycle_id": cycle_id,
            },
            emit_ui_event=emit_ui_event,
            emitted_event_types=emitted_event_types,
        )

        return ActionDispatchResult(
            action_type="control_camera_look",
            emitted_event_types=emitted_event_types,
            observed_effects={
                "status_code_after": "idle",
                "camera_move": "failed",
                "message_id": message_id,
            },
            task_mutations=[],
            finished_at=_now_ms(),
            status="failed",
            failure_mode="camera_move_failed",
            raw_result_ref=None,
            adapter_trace_ref={
                "camera_error": {
                    "error_kind": type(error).__name__,
                    "error_message": _error_message_text(error),
                }
            },
        )

    # Block: Camera move message
    final_message_emitted = False
    if response_text:
        _emit_browser_event(
            pending_input=pending_input,
            event_type="message",
            payload={
                "message_id": message_id,
                "role": "assistant",
                "text": response_text,
                "created_at": _now_ms(),
                "source_cycle_id": cycle_id,
                "related_input_id": str(pending_input["input_id"]),
            },
            emit_ui_event=emit_ui_event,
            emitted_event_types=emitted_event_types,
        )
        final_message_emitted = True

    # Block: Idle status
    _emit_browser_event(
        pending_input=pending_input,
        event_type="status",
        payload={
            "status_code": "idle",
            "label": "待機中",
            "cycle_id": cycle_id,
        },
        emit_ui_event=emit_ui_event,
        emitted_event_types=emitted_event_types,
    )

    return ActionDispatchResult(
        action_type="control_camera_look",
        emitted_event_types=emitted_event_types,
        observed_effects={
            "status_code_after": "idle",
            "camera_move": "succeeded",
            "movement_label": look_response.movement_label,
            "message_id": message_id,
            "final_message_emitted": final_message_emitted,
        },
        task_mutations=[],
        finished_at=_now_ms(),
        status="succeeded",
        failure_mode=None,
        raw_result_ref=look_response.raw_result_ref,
        adapter_trace_ref=look_response.adapter_trace_ref,
    )


# Block: Browse task dispatch
def _dispatch_browse_task_command(
    *,
    pending_input: dict[str, Any],
    cycle_id: str,
    resolved_at: int,
    action_command: dict[str, Any],
    emit_ui_event: Callable[[dict[str, Any]], None],
) -> ActionDispatchResult:
    emitted_event_types: list[str] = []
    query = str(action_command["parameters"]["query"])
    task_id = str(action_command["parameters"]["task_id"])

    # Block: Waiting status
    _emit_browser_event(
        pending_input=pending_input,
        event_type="status",
        payload={
            "status_code": "waiting_external",
            "label": "外部検索を待っています",
            "cycle_id": cycle_id,
        },
        emit_ui_event=emit_ui_event,
        emitted_event_types=emitted_event_types,
    )

    # Block: Queue notice
    _emit_browser_event(
        pending_input=pending_input,
        event_type="notice",
        payload={
            "notice_code": "browse_queued",
            "text": f"検索タスクを追加しました: {query}",
        },
        emit_ui_event=emit_ui_event,
        emitted_event_types=emitted_event_types,
    )

    # Block: Idle status
    _emit_browser_event(
        pending_input=pending_input,
        event_type="status",
        payload={
            "status_code": "idle",
            "label": "待機中",
            "cycle_id": cycle_id,
        },
        emit_ui_event=emit_ui_event,
        emitted_event_types=emitted_event_types,
    )

    return ActionDispatchResult(
        action_type="enqueue_browse_task",
        emitted_event_types=emitted_event_types,
        observed_effects={
            "status_code_after": "idle",
            "queued_task_id": task_id,
            "queued_task_kind": "browse",
            "queued_task_status": "waiting_external",
            "query": query,
        },
        task_mutations=[
            TaskStateMutationRecord(
                task_id=task_id,
                task_kind="browse",
                task_status="waiting_external",
                goal_hint=query,
                completion_hint={
                    "mode": "external_search_result",
                    "query": query,
                    "target_channel": str(action_command["parameters"]["target_channel"]),
                },
                resume_condition={
                    "kind": "external_result_arrived",
                    "query": query,
                    "target_channel": str(action_command["parameters"]["target_channel"]),
                },
                interruptible=True,
                priority=80,
                title=f"検索: {query}",
                step_hints=[],
                created_at=resolved_at,
            )
        ],
        finished_at=_now_ms(),
        status="succeeded",
        failure_mode=None,
        raw_result_ref=None,
        adapter_trace_ref=None,
    )


# Block: UI event helper
def _emit_browser_event(
    *,
    pending_input: dict[str, Any],
    event_type: str,
    payload: dict[str, Any],
    emit_ui_event: Callable[[dict[str, Any]], None],
    emitted_event_types: list[str],
) -> None:
    ui_event = {
        "channel": str(pending_input["channel"]),
        "event_type": event_type,
        "payload": payload,
    }
    emitted_event_types.append(event_type)
    emit_ui_event(ui_event)


# Block: Cloud speech synthesis execution
def _synthesize_speech_if_enabled(
    *,
    cycle_id: str,
    message_id: str,
    response_text: str,
    speech_synthesizer: SpeechSynthesizer,
    effective_settings: dict[str, Any],
) -> SpeechSynthesisResponse | None:
    tts_enabled = effective_settings.get("speech.tts.enabled")
    if not isinstance(tts_enabled, bool):
        raise RuntimeError("speech.tts.enabled must be boolean")
    if not tts_enabled:
        return None
    synthesis_request = _build_speech_synthesis_request(
        cycle_id=cycle_id,
        message_id=message_id,
        response_text=response_text,
        effective_settings=effective_settings,
    )
    return speech_synthesizer.synthesize(synthesis_request)


# Block: Cloud speech request build
def _build_speech_synthesis_request(
    *,
    cycle_id: str,
    message_id: str,
    response_text: str,
    effective_settings: dict[str, Any],
) -> SpeechSynthesisRequest:
    return SpeechSynthesisRequest(
        cycle_id=cycle_id,
        message_id=message_id,
        text=response_text,
        api_key=_required_non_empty_setting(effective_settings, "speech.tts.api_key"),
        endpoint_url=_required_non_empty_setting(effective_settings, "speech.tts.endpoint_url"),
        model_uuid=_required_non_empty_setting(effective_settings, "speech.tts.model_uuid"),
        speaker_uuid=_required_non_empty_setting(effective_settings, "speech.tts.speaker_uuid"),
        style_id=_required_setting_int(effective_settings, "speech.tts.style_id"),
        use_ssml=False,
        language=_required_non_empty_setting(effective_settings, "speech.tts.language"),
        speaking_rate=_required_setting_number(effective_settings, "speech.tts.speaking_rate"),
        emotional_intensity=_required_setting_number(effective_settings, "speech.tts.emotional_intensity"),
        tempo_dynamics=_required_setting_number(effective_settings, "speech.tts.tempo_dynamics"),
        pitch=_required_setting_number(effective_settings, "speech.tts.pitch"),
        volume=_required_setting_number(effective_settings, "speech.tts.volume"),
        output_format=_required_non_empty_setting(effective_settings, "speech.tts.output_format"),
    )


# Block: Settings read helpers
def _required_non_empty_setting(effective_settings: dict[str, Any], key: str) -> str:
    value = effective_settings.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{key} must be non-empty string")
    return value.strip()


def _required_setting_int(effective_settings: dict[str, Any], key: str) -> int:
    value = effective_settings.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"{key} must be integer")
    return value


def _required_setting_number(effective_settings: dict[str, Any], key: str) -> float:
    value = effective_settings.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{key} must be number")
    return float(value)


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


# Block: Id helper
def opaque_action_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


# Block: Time helper
def _now_ms() -> int:
    return int(time.time() * 1000)


# Block: Error formatting
def _error_message_text(error: Exception) -> str:
    message = str(error).strip()
    if not message:
        return type(error).__name__
    return message[:240]


# Block: Optional action text helper
def _optional_action_text(parameters: dict[str, Any], key: str) -> str | None:
    value = parameters.get(key)
    if not isinstance(value, str):
        return None
    stripped_value = value.strip()
    if not stripped_value:
        return None
    return stripped_value
