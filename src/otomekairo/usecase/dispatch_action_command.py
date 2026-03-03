"""Execute structured chat action commands."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from otomekairo.gateway.notification_client import LineNotificationRequest, NotificationClient
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
    notification_client: NotificationClient,
    line_enabled: bool,
    line_channel_access_token: str,
    line_to_user_id: str,
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
        )
    if command_type == "dispatch_notice":
        return _dispatch_notice_command(
            pending_input=pending_input,
            cycle_id=cycle_id,
            action_command=action_command,
            emit_ui_event=emit_ui_event,
            notification_client=notification_client,
            line_enabled=line_enabled,
            line_channel_access_token=line_channel_access_token,
            line_to_user_id=line_to_user_id,
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
        _emit_browser_event(
            pending_input=pending_input,
            event_type="message",
            payload={
                "message_id": message_id,
                "role": "assistant",
                "text": response_text,
                "created_at": finished_at,
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
        action_type="emit_chat_response",
        emitted_event_types=emitted_event_types,
        observed_effects={
            "status_code_after": "idle",
            "was_cancelled": was_cancelled,
            "token_count": emitted_chunk_count,
            "final_message_emitted": final_message_emitted,
            "message_id": message_id,
        },
        task_mutations=[],
        finished_at=finished_at,
        status="stopped" if was_cancelled else "succeeded",
        failure_mode="cancelled" if was_cancelled else None,
        raw_result_ref=None,
        adapter_trace_ref=None,
    )


# Block: Notice command dispatch
def _dispatch_notice_command(
    *,
    pending_input: dict[str, Any],
    cycle_id: str,
    action_command: dict[str, Any],
    emit_ui_event: Callable[[dict[str, Any]], None],
    notification_client: NotificationClient,
    line_enabled: bool,
    line_channel_access_token: str,
    line_to_user_id: str,
) -> ActionDispatchResult:
    emitted_event_types: list[str] = []
    notice_code = str(action_command["parameters"]["notice_code"])
    notice_text = str(action_command["parameters"]["text"])
    line_result_ref: dict[str, Any] | None = None
    line_trace_ref: dict[str, Any] | None = None

    # Block: Optional LINE delivery
    try:
        if line_enabled:
            line_response = notification_client.send_line_text(
                LineNotificationRequest(
                    cycle_id=cycle_id,
                    text=notice_text,
                    channel_access_token=line_channel_access_token,
                    to_user_id=line_to_user_id,
                )
            )
            line_result_ref = line_response.raw_result_ref
            line_trace_ref = line_response.adapter_trace_ref
    except Exception as error:
        # Block: Notification failure event
        _emit_browser_event(
            pending_input=pending_input,
            event_type="error",
            payload={
                "error_code": "line_delivery_failed",
                "message": _error_message_text(error),
                "retriable": False,
            },
            emit_ui_event=emit_ui_event,
            emitted_event_types=emitted_event_types,
        )

        # Block: Idle status after failure
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
                "error_code": "line_delivery_failed",
                "line_delivery": "failed",
            },
            task_mutations=[],
            finished_at=_now_ms(),
            status="failed",
            failure_mode="line_delivery_failed",
            raw_result_ref=line_result_ref,
            adapter_trace_ref={
                "line_error": {
                    "error_kind": type(error).__name__,
                    "error_message": _error_message_text(error),
                }
            },
        )

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
            "line_delivery": "delivered" if line_enabled else "skipped",
        },
        task_mutations=[],
        finished_at=_now_ms(),
        status="succeeded",
        failure_mode=None,
        raw_result_ref=line_result_ref,
        adapter_trace_ref=line_trace_ref,
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
