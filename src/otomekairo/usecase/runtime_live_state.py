"""Build live runtime state snapshots for current browser_chat cycles."""

from __future__ import annotations

from typing import Any


# Block: Public live state builder
def build_runtime_live_state(
    *,
    effective_settings: dict[str, Any],
    camera_available: bool,
    attention_state: dict[str, Any],
    active_tasks: list[dict[str, Any]],
    waiting_tasks: list[dict[str, Any]],
    previous_body_state: dict[str, Any],
    previous_world_state: dict[str, Any],
    cycle_context: dict[str, Any] | None,
    updated_at: int,
) -> dict[str, dict[str, Any]]:
    camera_enabled = _required_boolean(
        effective_settings,
        "sensors.camera.enabled",
        "effective_settings.sensors.camera.enabled",
    )
    _required_boolean(
        effective_settings,
        "sensors.microphone.enabled",
        "effective_settings.sensors.microphone.enabled",
    )
    body_state = _build_body_state(
        camera_enabled=camera_enabled,
        camera_available=camera_available,
        active_tasks=active_tasks,
        waiting_tasks=waiting_tasks,
        cycle_context=cycle_context,
        updated_at=updated_at,
    )
    world_state = _build_world_state(
        camera_enabled=camera_enabled,
        camera_available=camera_available,
        attention_state=attention_state,
        active_tasks=active_tasks,
        waiting_tasks=waiting_tasks,
        previous_world_state=previous_world_state,
        cycle_context=cycle_context,
        updated_at=updated_at,
    )
    drive_state = _build_drive_state(
        camera_enabled=camera_enabled,
        camera_available=camera_available,
        attention_state=attention_state,
        active_tasks=active_tasks,
        waiting_tasks=waiting_tasks,
        cycle_context=cycle_context,
        updated_at=updated_at,
    )
    return {
        "body_state": {
            **body_state,
            "mobility": _mobility_payload(previous_body_state=previous_body_state),
        },
        "world_state": world_state,
        "drive_state": drive_state,
    }


# Block: Body state builder
def _build_body_state(
    *,
    camera_enabled: bool,
    camera_available: bool,
    active_tasks: list[dict[str, Any]],
    waiting_tasks: list[dict[str, Any]],
    cycle_context: dict[str, Any] | None,
    updated_at: int,
) -> dict[str, Any]:
    interaction_load = _interaction_load(cycle_context=cycle_context)
    task_queue_pressure = _clamp_unit(
        len(active_tasks) * 0.6
        + len(waiting_tasks) * 0.35
        + interaction_load * 0.15
    )
    action_types = _cycle_action_types(cycle_context)
    return {
        "posture": {
            "mode": _posture_mode(
                active_tasks=active_tasks,
                waiting_tasks=waiting_tasks,
                cycle_context=cycle_context,
            )
        },
        "sensor_availability": {
            "camera": camera_enabled and camera_available,
            "microphone": False,
        },
        "output_locks": {
            "speech": False,
            "camera": False,
            "browse": bool(active_tasks),
        },
        "load": {
            "task_queue_pressure": round(task_queue_pressure, 4),
            "interaction_load": round(interaction_load, 4),
            "last_action_count": len(action_types),
        },
        "updated_at": updated_at,
    }


# Block: World state builder
def _build_world_state(
    *,
    camera_enabled: bool,
    camera_available: bool,
    attention_state: dict[str, Any],
    active_tasks: list[dict[str, Any]],
    waiting_tasks: list[dict[str, Any]],
    previous_world_state: dict[str, Any],
    cycle_context: dict[str, Any] | None,
    updated_at: int,
) -> dict[str, Any]:
    channel = _context_channel(
        cycle_context=cycle_context,
        active_tasks=active_tasks,
        waiting_tasks=waiting_tasks,
        previous_world_state=previous_world_state,
    )
    camera_ready = camera_enabled and camera_available
    action_types = _cycle_action_types(cycle_context)
    return {
        "location": {
            "state": "channel_context",
            "channel": channel,
        },
        "situation_summary": _situation_summary(
            active_tasks=active_tasks,
            waiting_tasks=waiting_tasks,
            previous_world_state=previous_world_state,
            cycle_context=cycle_context,
        ),
        "surroundings": {
            "current_channel": channel,
            "latest_observation_kind": _latest_observation_kind(
                cycle_context=cycle_context,
                previous_world_state=previous_world_state,
            ),
            "latest_observation_source": _latest_observation_source(
                cycle_context=cycle_context,
                previous_world_state=previous_world_state,
            ),
            "latest_action_types": action_types,
        },
        "affordances": {
            "speak": True,
            "browse": True,
            "notify": True,
            "look": camera_ready,
        },
        "constraints": {
            "look_unavailable": not camera_ready,
            "live_microphone_input_unavailable": True,
            "has_external_wait": bool(waiting_tasks),
        },
        "attention_targets": {
            "primary_focus": _attention_focus_summary(attention_state),
            "secondary_focuses": _secondary_focus_summaries(attention_state),
        },
        "external_waits": {
            "count": len(waiting_tasks),
            "items": [_external_wait_entry(task) for task in waiting_tasks],
        },
        "updated_at": updated_at,
    }


# Block: Drive state builder
def _build_drive_state(
    *,
    camera_enabled: bool,
    camera_available: bool,
    attention_state: dict[str, Any],
    active_tasks: list[dict[str, Any]],
    waiting_tasks: list[dict[str, Any]],
    cycle_context: dict[str, Any] | None,
    updated_at: int,
) -> dict[str, Any]:
    focus_kind = _focus_kind(attention_state)
    action_types = set(_cycle_action_types(cycle_context))
    observation_kind = _cycle_observation_kind(cycle_context)
    has_task_signal = bool(active_tasks or waiting_tasks) or bool(
        action_types & {"enqueue_browse_task", "complete_browse_task"}
    )
    has_exploration_signal = observation_kind in {"scene_change", "search_result"} or bool(
        action_types & {"enqueue_browse_task", "complete_browse_task", "control_camera_look"}
    )
    has_social_signal = observation_kind in {"dialogue_turn", "instruction"} or bool(
        action_types & {"emit_chat_response", "dispatch_notice", "stop_active_message"}
    )
    idle_bias = not has_task_signal and not has_exploration_signal and not has_social_signal
    camera_ready = camera_enabled and camera_available
    task_progress_bias = _clamp_unit(
        (0.55 if active_tasks else 0.0)
        + (0.35 if waiting_tasks else 0.0)
        + (0.10 if focus_kind == "task" else 0.0)
        + (0.10 if "complete_browse_task" in action_types else 0.0)
    )
    exploration_bias = _clamp_unit(
        (0.55 if has_exploration_signal else 0.0)
        + (0.15 if camera_ready else 0.0)
        + (0.10 if focus_kind in {"observation", "relationship"} else 0.0)
    )
    maintenance_bias = _clamp_unit(
        (0.45 if idle_bias else 0.05)
        + (0.20 if not camera_ready else 0.0)
        + (0.10 if focus_kind == "idle" else 0.0)
    )
    social_bias = _clamp_unit(
        (0.65 if has_social_signal else 0.0)
        + (0.15 if focus_kind == "relationship" else 0.0)
    )
    priority_effects = {
        "task_progress_bias": round(task_progress_bias, 4),
        "exploration_bias": round(exploration_bias, 4),
        "maintenance_bias": round(maintenance_bias, 4),
        "social_bias": round(social_bias, 4),
    }
    return {
        "drive_levels": {
            "task_progress": priority_effects["task_progress_bias"],
            "exploration": priority_effects["exploration_bias"],
            "maintenance": priority_effects["maintenance_bias"],
            "social": priority_effects["social_bias"],
        },
        "priority_effects": priority_effects,
        "updated_at": updated_at,
    }


# Block: Posture helpers
def _posture_mode(
    *,
    active_tasks: list[dict[str, Any]],
    waiting_tasks: list[dict[str, Any]],
    cycle_context: dict[str, Any] | None,
) -> str:
    if active_tasks:
        return "focused_task"
    if waiting_tasks:
        return "awaiting_external"
    observation_kind = _cycle_observation_kind(cycle_context)
    if observation_kind == "scene_change":
        return "observing"
    action_types = set(_cycle_action_types(cycle_context))
    if action_types & {"emit_chat_response", "dispatch_notice"}:
        return "responding"
    return "idle"


# Block: World helpers
def _context_channel(
    *,
    cycle_context: dict[str, Any] | None,
    active_tasks: list[dict[str, Any]],
    waiting_tasks: list[dict[str, Any]],
    previous_world_state: dict[str, Any],
) -> str:
    if isinstance(cycle_context, dict):
        channel = cycle_context.get("channel")
        if isinstance(channel, str) and channel:
            return channel
    for task_entry in active_tasks + waiting_tasks:
        channel = _task_channel(task_entry)
        if channel is not None:
            return channel
    previous_location = previous_world_state.get("location")
    if isinstance(previous_location, dict):
        channel = previous_location.get("channel")
        if isinstance(channel, str) and channel:
            return channel
    return "browser_chat"


def _situation_summary(
    *,
    active_tasks: list[dict[str, Any]],
    waiting_tasks: list[dict[str, Any]],
    previous_world_state: dict[str, Any],
    cycle_context: dict[str, Any] | None,
) -> str:
    active_summary = _active_task_summary(active_tasks)
    if active_summary is not None:
        return active_summary
    waiting_summary = _waiting_task_summary(waiting_tasks)
    if waiting_summary is not None:
        return waiting_summary
    if isinstance(cycle_context, dict):
        situation_summary = cycle_context.get("situation_summary")
        if isinstance(situation_summary, str) and situation_summary:
            return situation_summary
    previous_summary = previous_world_state.get("situation_summary")
    if isinstance(previous_summary, str) and previous_summary:
        return previous_summary
    return "待機中"


def _latest_observation_kind(
    *,
    cycle_context: dict[str, Any] | None,
    previous_world_state: dict[str, Any],
) -> str:
    observation_kind = _cycle_observation_kind(cycle_context)
    if observation_kind is not None:
        return observation_kind
    previous_surroundings = previous_world_state.get("surroundings")
    if isinstance(previous_surroundings, dict):
        previous_kind = previous_surroundings.get("latest_observation_kind")
        if isinstance(previous_kind, str) and previous_kind:
            return previous_kind
    return "idle"


def _latest_observation_source(
    *,
    cycle_context: dict[str, Any] | None,
    previous_world_state: dict[str, Any],
) -> str:
    if isinstance(cycle_context, dict):
        observation_source = cycle_context.get("observation_source")
        if isinstance(observation_source, str) and observation_source:
            return observation_source
    previous_surroundings = previous_world_state.get("surroundings")
    if isinstance(previous_surroundings, dict):
        previous_source = previous_surroundings.get("latest_observation_source")
        if isinstance(previous_source, str) and previous_source:
            return previous_source
    return "runtime"


# Block: Attention helpers
def _attention_focus_summary(attention_state: dict[str, Any]) -> dict[str, Any]:
    primary_focus = _required_object(
        attention_state,
        "primary_focus",
        "attention_state.primary_focus",
    )
    return {
        "focus_ref": _required_string(
            primary_focus,
            "focus_ref",
            "attention_state.primary_focus.focus_ref",
        ),
        "focus_kind": _required_string(
            primary_focus,
            "focus_kind",
            "attention_state.primary_focus.focus_kind",
        ),
        "summary": _required_string(
            primary_focus,
            "summary",
            "attention_state.primary_focus.summary",
        ),
    }


def _secondary_focus_summaries(attention_state: dict[str, Any]) -> list[dict[str, Any]]:
    secondary_focuses = attention_state.get("secondary_focuses")
    if not isinstance(secondary_focuses, list):
        raise RuntimeError("attention_state.secondary_focuses must be a list")
    summaries: list[dict[str, Any]] = []
    for index, focus_entry in enumerate(secondary_focuses):
        if not isinstance(focus_entry, dict):
            raise RuntimeError(
                f"attention_state.secondary_focuses[{index}] must be an object"
            )
        summaries.append(
            {
                "focus_ref": _required_string(
                    focus_entry,
                    "focus_ref",
                    f"attention_state.secondary_focuses[{index}].focus_ref",
                ),
                "focus_kind": _required_string(
                    focus_entry,
                    "focus_kind",
                    f"attention_state.secondary_focuses[{index}].focus_kind",
                ),
                "summary": _required_string(
                    focus_entry,
                    "summary",
                    f"attention_state.secondary_focuses[{index}].summary",
                ),
            }
        )
    return summaries


# Block: Task helpers
def _active_task_summary(active_tasks: list[dict[str, Any]]) -> str | None:
    if not active_tasks:
        return None
    task_entry = active_tasks[0]
    task_kind = _required_string(task_entry, "task_kind", "active_tasks[0].task_kind")
    if task_kind == "browse":
        query = _task_query(task_entry)
        if query is not None:
            return f"外部検索を実行中: {query}"
    goal_hint = _required_string(task_entry, "goal_hint", "active_tasks[0].goal_hint")
    return f"タスクを進行中: {goal_hint}"


def _waiting_task_summary(waiting_tasks: list[dict[str, Any]]) -> str | None:
    if not waiting_tasks:
        return None
    task_entry = waiting_tasks[0]
    task_kind = _required_string(task_entry, "task_kind", "waiting_tasks[0].task_kind")
    if task_kind == "browse":
        query = _task_query(task_entry)
        if query is not None:
            return f"外部結果待ち: {query}"
    goal_hint = _required_string(task_entry, "goal_hint", "waiting_tasks[0].goal_hint")
    return f"外部待ち: {goal_hint}"


def _external_wait_entry(task_entry: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "task_id": _required_string(task_entry, "task_id", "waiting_task.task_id"),
        "task_kind": _required_string(task_entry, "task_kind", "waiting_task.task_kind"),
        "goal_hint": _required_string(task_entry, "goal_hint", "waiting_task.goal_hint"),
        "priority": _required_integer(task_entry, "priority", "waiting_task.priority"),
    }
    title = task_entry.get("title")
    if isinstance(title, str) and title:
        payload["title"] = title
    target_channel = _task_channel(task_entry)
    if target_channel is not None:
        payload["target_channel"] = target_channel
    query = _task_query(task_entry)
    if query is not None:
        payload["query"] = query
    return payload


def _task_channel(task_entry: dict[str, Any]) -> str | None:
    completion_hint = _required_object(
        task_entry,
        "completion_hint",
        "task_entry.completion_hint",
    )
    channel = completion_hint.get("target_channel")
    if channel is None:
        return None
    if not isinstance(channel, str) or not channel:
        raise RuntimeError("task_entry.completion_hint.target_channel must be non-empty string")
    return channel


def _task_query(task_entry: dict[str, Any]) -> str | None:
    completion_hint = _required_object(
        task_entry,
        "completion_hint",
        "task_entry.completion_hint",
    )
    query = completion_hint.get("query")
    if query is None:
        return None
    if not isinstance(query, str) or not query:
        raise RuntimeError("task_entry.completion_hint.query must be non-empty string")
    return query


# Block: Drive helpers
def _focus_kind(attention_state: dict[str, Any]) -> str:
    primary_focus = _required_object(
        attention_state,
        "primary_focus",
        "attention_state.primary_focus",
    )
    return _required_string(
        primary_focus,
        "focus_kind",
        "attention_state.primary_focus.focus_kind",
    )


def _cycle_action_types(cycle_context: dict[str, Any] | None) -> list[str]:
    if cycle_context is None:
        return []
    action_types = cycle_context.get("action_types")
    if not isinstance(action_types, list):
        raise RuntimeError("cycle_context.action_types must be a list")
    normalized: list[str] = []
    for index, action_type in enumerate(action_types):
        if not isinstance(action_type, str) or not action_type:
            raise RuntimeError(f"cycle_context.action_types[{index}] must be non-empty string")
        normalized.append(action_type)
    return normalized


def _cycle_observation_kind(cycle_context: dict[str, Any] | None) -> str | None:
    if cycle_context is None:
        return None
    observation_kind = cycle_context.get("observation_kind")
    if observation_kind is None:
        return None
    if not isinstance(observation_kind, str) or not observation_kind:
        raise RuntimeError("cycle_context.observation_kind must be non-empty string")
    return observation_kind


def _interaction_load(*, cycle_context: dict[str, Any] | None) -> float:
    observation_kind = _cycle_observation_kind(cycle_context)
    if observation_kind in {"dialogue_turn", "instruction"}:
        return 0.8
    if observation_kind in {"scene_change", "search_result"}:
        return 0.5
    action_types = set(_cycle_action_types(cycle_context))
    if action_types & {"emit_chat_response", "dispatch_notice"}:
        return 0.6
    if action_types:
        return 0.35
    return 0.0


# Block: Body helpers
def _mobility_payload(*, previous_body_state: dict[str, Any]) -> dict[str, Any]:
    previous_mobility = previous_body_state.get("mobility")
    if isinstance(previous_mobility, dict):
        mode = previous_mobility.get("mode")
        if isinstance(mode, str) and mode:
            return {"mode": mode}
    return {"mode": "fixed"}


# Block: Primitive helpers
def _required_object(payload: dict[str, Any], key: str, field_name: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise RuntimeError(f"{field_name} must be an object")
    return value


def _required_string(payload: dict[str, Any], key: str, field_name: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{field_name} must be non-empty string")
    return value


def _required_integer(payload: dict[str, Any], key: str, field_name: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"{field_name} must be integer")
    return value


def _required_boolean(payload: dict[str, Any], key: str, field_name: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise RuntimeError(f"{field_name} must be boolean")
    return value


def _clamp_unit(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
