"""Runtime view and status helper functions for the SQLite state store."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from otomekairo.schema.runtime_types import (
    ActionHistoryRecord,
    PendingInputMutationRecord,
    PendingInputRecord,
    TaskStateRecord,
)
from otomekairo.usecase.observation_normalization import (
    normalize_observation_kind,
    normalize_observation_source,
)


# Block: Live state shape check
def _body_state_has_current_shape(row: sqlite3.Row) -> bool:
    posture = json.loads(row["posture_json"])
    mobility = json.loads(row["mobility_json"])
    sensor_availability = json.loads(row["sensor_availability_json"])
    output_locks = json.loads(row["output_locks_json"])
    load = json.loads(row["load_json"])
    return (
        isinstance(posture, dict)
        and isinstance(posture.get("mode"), str)
        and isinstance(mobility, dict)
        and isinstance(mobility.get("move_available"), bool)
        and isinstance(mobility.get("camera_look_available"), bool)
        and isinstance(sensor_availability, dict)
        and isinstance(sensor_availability.get("camera"), bool)
        and isinstance(sensor_availability.get("microphone"), bool)
        and isinstance(output_locks, dict)
        and isinstance(output_locks.get("chat"), bool)
        and isinstance(output_locks.get("notice"), bool)
        and isinstance(load, dict)
        and not isinstance(load.get("task_queue_pressure"), bool)
        and isinstance(load.get("task_queue_pressure"), (int, float))
        and not isinstance(load.get("interaction_load"), bool)
        and isinstance(load.get("interaction_load"), (int, float))
    )


# Block: World state shape check
def _world_state_has_current_shape(row: sqlite3.Row) -> bool:
    location = json.loads(row["location_json"])
    surroundings = json.loads(row["surroundings_json"])
    affordances = json.loads(row["affordances_json"])
    constraints = json.loads(row["constraints_json"])
    attention_targets = json.loads(row["attention_targets_json"])
    external_waits = json.loads(row["external_waits_json"])
    situation_summary = row["situation_summary"]
    return (
        isinstance(location, dict)
        and isinstance(location.get("channel"), str)
        and isinstance(situation_summary, str)
        and bool(situation_summary)
        and isinstance(surroundings, dict)
        and isinstance(surroundings.get("current_channel"), str)
        and isinstance(surroundings.get("latest_observation_kind"), str)
        and isinstance(surroundings.get("latest_observation_source"), str)
        and isinstance(surroundings.get("latest_action_types"), list)
        and isinstance(affordances, dict)
        and isinstance(affordances.get("speak"), bool)
        and isinstance(affordances.get("browse"), bool)
        and isinstance(affordances.get("notify"), bool)
        and isinstance(affordances.get("look"), bool)
        and isinstance(constraints, dict)
        and isinstance(constraints.get("look_unavailable"), bool)
        and isinstance(constraints.get("live_microphone_input_unavailable"), bool)
        and isinstance(constraints.get("has_external_wait"), bool)
        and isinstance(attention_targets, dict)
        and isinstance(attention_targets.get("primary_focus"), dict)
        and isinstance(attention_targets.get("secondary_focuses"), list)
        and isinstance(external_waits, dict)
        and not isinstance(external_waits.get("count"), bool)
        and isinstance(external_waits.get("count"), int)
        and isinstance(external_waits.get("items"), list)
    )


# Block: Drive state shape check
def _drive_state_has_current_shape(row: sqlite3.Row) -> bool:
    drive_levels = json.loads(row["drive_levels_json"])
    priority_effects = json.loads(row["priority_effects_json"])
    return (
        isinstance(drive_levels, dict)
        and not isinstance(drive_levels.get("task_progress"), bool)
        and isinstance(drive_levels.get("task_progress"), (int, float))
        and not isinstance(drive_levels.get("exploration"), bool)
        and isinstance(drive_levels.get("exploration"), (int, float))
        and not isinstance(drive_levels.get("maintenance"), bool)
        and isinstance(drive_levels.get("maintenance"), (int, float))
        and not isinstance(drive_levels.get("social"), bool)
        and isinstance(drive_levels.get("social"), (int, float))
        and isinstance(priority_effects, dict)
        and not isinstance(priority_effects.get("task_progress_bias"), bool)
        and isinstance(priority_effects.get("task_progress_bias"), (int, float))
        and not isinstance(priority_effects.get("exploration_bias"), bool)
        and isinstance(priority_effects.get("exploration_bias"), (int, float))
        and not isinstance(priority_effects.get("maintenance_bias"), bool)
        and isinstance(priority_effects.get("maintenance_bias"), (int, float))
        and not isinstance(priority_effects.get("social_bias"), bool)
        and isinstance(priority_effects.get("social_bias"), (int, float))
    )


# Block: Live state decode
def _decode_body_state_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "posture": json.loads(row["posture_json"]),
        "mobility": json.loads(row["mobility_json"]),
        "sensor_availability": json.loads(row["sensor_availability_json"]),
        "output_locks": json.loads(row["output_locks_json"]),
        "load": json.loads(row["load_json"]),
        "updated_at": int(row["updated_at"]),
    }


# Block: World state decode
def _decode_world_state_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "location": json.loads(row["location_json"]),
        "situation_summary": str(row["situation_summary"]),
        "surroundings": json.loads(row["surroundings_json"]),
        "affordances": json.loads(row["affordances_json"]),
        "constraints": json.loads(row["constraints_json"]),
        "attention_targets": json.loads(row["attention_targets_json"]),
        "external_waits": json.loads(row["external_waits_json"]),
        "updated_at": int(row["updated_at"]),
    }


# Block: Pending-input cycle context
def _pending_input_cycle_context(
    *,
    pending_input: PendingInputRecord,
    resolution_status: str,
    action_results: list[ActionHistoryRecord],
    pending_input_mutations: list[PendingInputMutationRecord],
) -> dict[str, Any]:
    return {
        "channel": pending_input.channel,
        "observation_source": normalize_observation_source(
            source=pending_input.source,
            payload=pending_input.payload,
        ),
        "observation_kind": normalize_observation_kind(payload=pending_input.payload),
        "action_types": [action_result.action_type for action_result in action_results],
        "situation_summary": _pending_input_situation_summary(
            pending_input=pending_input,
            resolution_status=resolution_status,
            action_results=action_results,
            pending_input_mutations=pending_input_mutations,
        ),
    }


# Block: Task cycle context
def _task_cycle_context(
    *,
    task: TaskStateRecord,
    final_status: str,
    action_results: list[ActionHistoryRecord],
    pending_input_mutations: list[PendingInputMutationRecord],
) -> dict[str, Any]:
    return {
        "channel": _task_record_channel(task),
        "observation_source": _task_cycle_observation_source(pending_input_mutations),
        "observation_kind": _task_cycle_observation_kind(pending_input_mutations),
        "action_types": [action_result.action_type for action_result in action_results],
        "situation_summary": _task_cycle_situation_summary(
            task=task,
            final_status=final_status,
        ),
    }


# Block: Pending-input situation summary
def _pending_input_situation_summary(
    *,
    pending_input: PendingInputRecord,
    resolution_status: str,
    action_results: list[ActionHistoryRecord],
    pending_input_mutations: list[PendingInputMutationRecord],
) -> str:
    input_kind = str(pending_input.payload["input_kind"])
    action_types = {action_result.action_type for action_result in action_results}
    has_followup_camera_observation = any(
        pending_input_mutation.payload.get("input_kind") == "camera_observation"
        for pending_input_mutation in pending_input_mutations
    )
    if input_kind == "chat_message":
        if "enqueue_browse_task" in action_types:
            query = _queued_browse_query(action_results)
            if query is not None:
                return f"検索タスクを登録した: {query}"
            return "検索タスクを登録した"
        if "control_camera_look" in action_types and has_followup_camera_observation:
            return "カメラ視点を調整し、追跡観測を登録した"
        if "emit_chat_response" in action_types:
            return "チャット応答を返した"
        if "dispatch_notice" in action_types:
            return "通知を返した"
        if "control_camera_look" in action_types:
            return "カメラ視点を調整した"
        return "チャット入力を処理した" if resolution_status == "consumed" else "チャット入力を棄却した"
    if input_kind == "microphone_message":
        if "enqueue_browse_task" in action_types:
            query = _queued_browse_query(action_results)
            if query is not None:
                return f"音声入力をもとに検索タスクを登録した: {query}"
            return "音声入力をもとに検索タスクを登録した"
        if "control_camera_look" in action_types and has_followup_camera_observation:
            return "音声入力をもとにカメラ視点を調整し、追跡観測を登録した"
        if "emit_chat_response" in action_types:
            return "音声入力に応答した"
        if "dispatch_notice" in action_types:
            return "音声入力に対して通知した"
        if "control_camera_look" in action_types:
            return "音声入力をもとにカメラ視点を調整した"
        return "音声入力を処理した" if resolution_status == "consumed" else "音声入力を棄却した"
    if input_kind == "camera_observation":
        trigger_reason = pending_input.payload.get("trigger_reason")
        is_followup_observation = trigger_reason == "post_action_followup"
        if "enqueue_browse_task" in action_types:
            query = _queued_browse_query(action_results)
            if query is not None:
                return f"カメラ観測をもとに検索した: {query}"
            return "カメラ観測をもとに検索した"
        if "control_camera_look" in action_types:
            if has_followup_camera_observation:
                return "カメラ観測をもとに視点を調整し、追跡観測を登録した"
            return "カメラ観測をもとに視点を調整した"
        if "emit_chat_response" in action_types:
            if is_followup_observation:
                return "追跡観測を処理して応答した"
            return "カメラ観測を処理して応答した"
        if resolution_status == "consumed":
            if is_followup_observation:
                return "追跡観測を処理した"
            return "カメラ観測を処理した"
        if is_followup_observation:
            return "追跡観測を棄却した"
        return "カメラ観測を棄却した"
    if input_kind == "network_result":
        if "emit_chat_response" in action_types:
            return "検索結果を要約して応答した"
        return "検索結果を取り込んだ" if resolution_status == "consumed" else "検索結果入力を棄却した"
    if input_kind == "idle_tick":
        if "control_camera_look" in action_types and has_followup_camera_observation:
            return "idle_tick を処理し、視点調整と追跡観測を開始した"
        if "enqueue_browse_task" in action_types:
            query = _queued_browse_query(action_results)
            if query is not None:
                return f"idle_tick を処理して検索した: {query}"
            return "idle_tick を処理して検索した"
        if "emit_chat_response" in action_types:
            return "idle_tick を処理して応答した"
        if "dispatch_notice" in action_types:
            return "idle_tick を処理して通知した"
        return "idle_tick を処理した" if resolution_status == "consumed" else "idle_tick を棄却した"
    if input_kind == "cancel":
        return "停止要求を処理した" if resolution_status == "consumed" else "停止要求を棄却した"
    if resolution_status == "consumed":
        return f"{input_kind} を処理した"
    return f"{input_kind} を棄却した"


# Block: Task situation summary
def _task_cycle_situation_summary(
    *,
    task: TaskStateRecord,
    final_status: str,
) -> str:
    if task.task_kind == "browse":
        query = _task_record_query(task) or task.goal_hint
        if final_status == "completed":
            return f"外部検索を完了した: {query}"
        return f"外部検索に失敗した: {query}"
    if final_status == "completed":
        return f"タスクを完了した: {task.goal_hint}"
    return f"タスクを中断した: {task.goal_hint}"


# Block: Task observation kind
def _task_cycle_observation_kind(
    pending_input_mutations: list[PendingInputMutationRecord],
) -> str | None:
    for pending_input_mutation in pending_input_mutations:
        input_kind = pending_input_mutation.payload.get("input_kind")
        if input_kind == "network_result":
            return "search_result"
    return None


# Block: Task observation source
def _task_cycle_observation_source(
    pending_input_mutations: list[PendingInputMutationRecord],
) -> str:
    for pending_input_mutation in pending_input_mutations:
        if pending_input_mutation.payload.get("input_kind") == "network_result":
            return "network_result"
    return "runtime_task"


# Block: Task record channel
def _task_record_channel(task: TaskStateRecord) -> str:
    target_channel = task.completion_hint.get("target_channel")
    if not isinstance(target_channel, str) or not target_channel:
        raise RuntimeError("task.completion_hint.target_channel must be non-empty string")
    return target_channel


# Block: Task record query
def _task_record_query(task: TaskStateRecord) -> str | None:
    query = task.completion_hint.get("query")
    if query is None:
        return None
    if not isinstance(query, str) or not query:
        raise RuntimeError("task.completion_hint.query must be non-empty string")
    return query


# Block: Queued browse query
def _queued_browse_query(action_results: list[ActionHistoryRecord]) -> str | None:
    for action_result in action_results:
        if action_result.action_type != "enqueue_browse_task":
            continue
        observed_effects = action_result.observed_effects
        if not isinstance(observed_effects, dict):
            raise RuntimeError("enqueue_browse_task observed_effects must be an object")
        query = observed_effects.get("query")
        if isinstance(query, str) and query:
            return query
    return None


# Block: Public body summary
def _public_body_state_summary(
    *,
    posture_json: dict[str, Any],
    sensor_availability_json: dict[str, Any],
    load_json: dict[str, Any],
) -> dict[str, Any]:
    posture_mode = posture_json.get("mode")
    if not isinstance(posture_mode, str) or not posture_mode:
        raise RuntimeError("body_state.posture_json.mode is required")
    camera_available = sensor_availability_json.get("camera")
    microphone_available = sensor_availability_json.get("microphone")
    if not isinstance(camera_available, bool):
        raise RuntimeError("body_state.sensor_availability_json.camera is required")
    if not isinstance(microphone_available, bool):
        raise RuntimeError("body_state.sensor_availability_json.microphone is required")
    task_queue_pressure = load_json.get("task_queue_pressure")
    interaction_load = load_json.get("interaction_load")
    if isinstance(task_queue_pressure, bool) or not isinstance(task_queue_pressure, (int, float)):
        raise RuntimeError("body_state.load_json.task_queue_pressure is required")
    if isinstance(interaction_load, bool) or not isinstance(interaction_load, (int, float)):
        raise RuntimeError("body_state.load_json.interaction_load is required")
    return {
        "posture_mode": posture_mode,
        "sensor_availability": {
            "camera": camera_available,
            "microphone": microphone_available,
        },
        "load": {
            "task_queue_pressure": float(task_queue_pressure),
            "interaction_load": float(interaction_load),
        },
    }


# Block: Public world summary
def _public_world_state_summary(
    *,
    situation_summary: str,
    external_waits_json: dict[str, Any],
) -> dict[str, Any]:
    wait_count = external_waits_json.get("count")
    if isinstance(wait_count, bool) or not isinstance(wait_count, int):
        raise RuntimeError("world_state.external_waits_json.count is required")
    if not situation_summary:
        raise RuntimeError("world_state.situation_summary is required")
    return {
        "situation_summary": situation_summary,
        "external_wait_count": wait_count,
    }


# Block: Public drive summary
def _public_drive_state_summary(
    *,
    priority_effects_json: dict[str, Any],
) -> dict[str, Any]:
    return {
        "priority_effects": {
            "task_progress_bias": _required_numeric_field(
                priority_effects_json,
                "task_progress_bias",
                "drive_state.priority_effects_json.task_progress_bias",
            ),
            "exploration_bias": _required_numeric_field(
                priority_effects_json,
                "exploration_bias",
                "drive_state.priority_effects_json.exploration_bias",
            ),
            "maintenance_bias": _required_numeric_field(
                priority_effects_json,
                "maintenance_bias",
                "drive_state.priority_effects_json.maintenance_bias",
            ),
            "social_bias": _required_numeric_field(
                priority_effects_json,
                "social_bias",
                "drive_state.priority_effects_json.social_bias",
            ),
        }
    }


# Block: Required numeric field
def _required_numeric_field(payload: dict[str, Any], key: str, field_name: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{field_name} must be numeric")
    return float(value)


# Block: Public emotion summary
def _public_emotion_summary(current_emotion_json: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(current_emotion_json, dict):
        raise RuntimeError("self_state.current_emotion_json must be an object")
    if "primary_label" not in current_emotion_json:
        raise RuntimeError("self_state.current_emotion_json.primary_label is required")
    return {
        "v": float(current_emotion_json["valence"]),
        "a": float(current_emotion_json["arousal"]),
        "d": float(current_emotion_json["dominance"]),
        "labels": [str(current_emotion_json["primary_label"])],
    }


# Block: Event log entry
def _event_log_entry(row: sqlite3.Row) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_id": str(row["event_id"]),
        "created_at": int(row["created_at"]),
        "source": str(row["source"]),
        "kind": str(row["kind"]),
        "searchable": bool(row["searchable"]),
    }
    updated_at = row["updated_at"]
    if updated_at is not None:
        payload["updated_at"] = int(updated_at)
    if isinstance(row["observation_summary"], str) and row["observation_summary"]:
        payload["observation_summary"] = str(row["observation_summary"])
    if isinstance(row["action_summary"], str) and row["action_summary"]:
        payload["action_summary"] = str(row["action_summary"])
    if isinstance(row["result_summary"], str) and row["result_summary"]:
        payload["result_summary"] = str(row["result_summary"])
    if isinstance(row["payload_ref_json"], str) and row["payload_ref_json"]:
        payload["payload_ref"] = json.loads(row["payload_ref_json"])
    if isinstance(row["input_journal_refs_json"], str) and row["input_journal_refs_json"]:
        payload["input_journal_refs"] = json.loads(row["input_journal_refs_json"])
    return payload


# Block: Commit-log sync error text
def _commit_log_sync_error_text(error: Exception) -> str:
    compact_message = " ".join(str(error).split())
    if not compact_message:
        return type(error).__name__
    return compact_message[:240]


# Block: Public primary focus
def _public_primary_focus(primary_focus_json: dict[str, Any]) -> str:
    if not isinstance(primary_focus_json, dict):
        raise RuntimeError("attention_state.primary_focus_json must be an object")
    summary = primary_focus_json.get("summary")
    if not isinstance(summary, str) or not summary:
        raise RuntimeError("attention_state.primary_focus_json.summary is required")
    return summary


# Block: Receipt summary
def _pending_input_receipt_summary(pending_input: PendingInputRecord) -> str:
    input_kind = str(pending_input.payload["input_kind"])
    if input_kind == "chat_message":
        text = pending_input.payload.get("text")
        if isinstance(text, str) and text:
            return f"chat_message:{text[:60]}"
        attachments = pending_input.payload.get("attachments")
        if isinstance(attachments, list) and attachments:
            return f"chat_message:camera_images:{len(attachments)}"
        return "chat_message"
    if input_kind == "microphone_message":
        text = pending_input.payload.get("text")
        if isinstance(text, str) and text:
            return f"microphone_message:{text[:60]}"
        return "microphone_message"
    if input_kind == "camera_observation":
        attachments = pending_input.payload.get("attachments")
        if pending_input.source == "post_action_followup":
            if isinstance(attachments, list) and attachments:
                return f"camera_observation:post_action_followup:camera_images:{len(attachments)}"
            return "camera_observation:post_action_followup"
        if isinstance(attachments, list) and attachments:
            return f"camera_observation:camera_images:{len(attachments)}"
        return "camera_observation"
    if input_kind == "network_result":
        query = str(pending_input.payload["query"])
        summary_text = str(pending_input.payload["summary_text"])
        return f"network_result:{query}:{summary_text[:40]}"
    if input_kind == "idle_tick":
        idle_duration_ms = int(pending_input.payload["idle_duration_ms"])
        return f"idle_tick:{idle_duration_ms}"
    if input_kind == "cancel":
        return "cancel request"
    return f"input:{input_kind}"


# Block: Pending-input message payload
def _pending_input_user_message_payload(
    *,
    input_id: str,
    created_at: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "message_id": input_id,
        "role": "user",
        "text": _pending_input_user_message_text(payload=payload),
        "created_at": created_at,
    }


# Block: Pending-input message text
def _pending_input_user_message_text(*, payload: dict[str, Any]) -> str:
    input_kind = str(payload.get("input_kind"))
    if input_kind == "chat_message":
        text = payload.get("text")
        attachments = payload.get("attachments")
        normalized_text = text.strip() if isinstance(text, str) else ""
        attachment_count = len(attachments) if isinstance(attachments, list) else 0
        return _chat_message_echo_text(
            text=normalized_text,
            attachment_count=attachment_count,
        )
    if input_kind == "microphone_message":
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("microphone_message.text is required for user message payload")
        return text.strip()
    raise RuntimeError("user message payload is only supported for chat_message and microphone_message")


# Block: Chat message echo text
def _chat_message_echo_text(*, text: str, attachment_count: int) -> str:
    if attachment_count < 0:
        raise RuntimeError("attachment_count must not be negative")
    normalized_text = text.strip()
    if normalized_text and attachment_count > 0:
        return f"{normalized_text}\n[画像 {attachment_count} 枚]"
    if normalized_text:
        return normalized_text
    return f"[画像 {attachment_count} 枚]"


# Block: History user message
def _history_user_message(
    *,
    input_id: str,
    created_at: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _pending_input_user_message_payload(
        input_id=input_id,
        created_at=created_at,
        payload=payload,
    )


# Block: History assistant message
def _history_assistant_message(
    *,
    finished_at: int,
    command_json: dict[str, Any],
    observed_effects_json: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(observed_effects_json, dict):
        return None
    if bool(observed_effects_json.get("final_message_emitted")) is not True:
        return None
    parameters = command_json.get("parameters")
    if not isinstance(parameters, dict):
        raise RuntimeError("action_history.command_json.parameters must be object")
    text = parameters.get("text")
    message_id = parameters.get("message_id")
    if not isinstance(text, str) or not text.strip():
        return None
    if not isinstance(message_id, str) or not message_id:
        raise RuntimeError("action_history.command_json.parameters.message_id must be non-empty string")
    return {
        "message_id": message_id,
        "role": "assistant",
        "text": text,
        "created_at": finished_at,
    }


# Block: Required JSON text decode
def _decode_required_json_text(*, raw_value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(raw_value, str) or not raw_value:
        raise RuntimeError(f"{field_name} must be non-empty string")
    try:
        decoded_value = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{field_name} must be valid JSON") from error
    if not isinstance(decoded_value, dict):
        raise RuntimeError(f"{field_name} must decode to object")
    return decoded_value


# Block: Optional JSON text decode
def _decode_optional_json_text(*, raw_value: Any, field_name: str) -> dict[str, Any] | None:
    if raw_value is None:
        return None
    return _decode_required_json_text(raw_value=raw_value, field_name=field_name)


# Block: Runtime response summary
def _runtime_response_summary(ui_events: list[dict[str, Any]]) -> str | None:
    for ui_event in ui_events:
        payload = ui_event["payload"]
        event_type = ui_event["event_type"]
        if event_type == "message":
            return str(payload["text"])
        if event_type == "notice":
            return str(payload["text"])
        if event_type == "error":
            return str(payload["message"])
    return None


# Block: Action command summary
def _action_command_summary(action_result: ActionHistoryRecord) -> str:
    target_channel = action_result.command.get("target_channel")
    if isinstance(target_channel, str) and target_channel:
        return f"{action_result.action_type} -> {target_channel}"
    target = action_result.command.get("target")
    if isinstance(target, dict):
        target_channel = target.get("channel")
        if isinstance(target_channel, str) and target_channel:
            return f"{action_result.action_type} -> {target_channel}"
        target_queue = target.get("queue")
        if isinstance(target_queue, str) and target_queue:
            return f"{action_result.action_type} -> {target_queue}"
    return action_result.action_type


# Block: Action result summary
def _action_result_summary(action_result: ActionHistoryRecord) -> str:
    if action_result.failure_mode:
        return f"{action_result.action_type} {action_result.status}: {action_result.failure_mode}"
    return f"{action_result.action_type} {action_result.status}"
