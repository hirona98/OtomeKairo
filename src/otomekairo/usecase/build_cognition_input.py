"""Build minimal cognition input from runtime state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from otomekairo.schema.runtime_types import CognitionStateSnapshot, PendingInputRecord


# Block: Public builder
def build_cognition_input(
    *,
    pending_input: PendingInputRecord,
    cycle_id: str,
    resolved_at: int,
    state_snapshot: CognitionStateSnapshot,
    camera_available: bool,
) -> dict[str, Any]:
    input_kind = str(pending_input.payload["input_kind"])
    if input_kind not in {"chat_message", "network_result"}:
        raise ValueError("cognition_input is only supported for browser_chat text and network_result")
    selection_profile = _build_selection_profile(state_snapshot)
    current_observation = _build_current_observation(
        pending_input=pending_input,
        resolved_at=resolved_at,
    )
    return {
        "cycle_meta": {
            "cycle_id": cycle_id,
            "trigger_reason": (
                "external_result"
                if input_kind == "network_result"
                else "external_input"
            ),
            "input_id": pending_input.input_id,
            "input_kind": input_kind,
        },
        "time_context": _build_time_context(resolved_at),
        "persona_snapshot": {
            "personality": state_snapshot.self_state["personality"],
            "current_emotion": state_snapshot.self_state["current_emotion"],
            "long_term_goals": state_snapshot.self_state["long_term_goals"],
            "relationship_overview": state_snapshot.self_state["relationship_overview"],
            "invariants": state_snapshot.self_state["invariants"],
        },
        "selection_profile": selection_profile,
        "body_snapshot": state_snapshot.body_state,
        "world_snapshot": state_snapshot.world_state,
        "drive_snapshot": state_snapshot.drive_state,
        "task_snapshot": _build_task_snapshot(
            task_snapshot=state_snapshot.task_snapshot,
            resolved_at=resolved_at,
        ),
        "attention_snapshot": state_snapshot.attention_state,
        "memory_bundle": _build_memory_bundle(
            memory_snapshot=state_snapshot.memory_snapshot,
            current_observation=current_observation,
            resolved_at=resolved_at,
        ),
        "policy_snapshot": {
            "system_policy": {
                "respect_invariants": True,
                "allow_direct_state_write": False,
            },
            "runtime_policy": {
                "camera_enabled": bool(state_snapshot.effective_settings["sensors.camera.enabled"]),
                "camera_available": bool(camera_available),
                "microphone_enabled": bool(state_snapshot.effective_settings["sensors.microphone.enabled"]),
                "tts_enabled": bool(state_snapshot.effective_settings["output.tts.enabled"]),
                "line_enabled": bool(state_snapshot.effective_settings["integrations.line.enabled"]),
            },
        },
        "skill_candidates": [],
        "current_observation": current_observation,
        "context_budget": {
            "max_tokens": int(state_snapshot.effective_settings["runtime.context_budget_tokens"]),
            "default_model": str(state_snapshot.effective_settings["llm.default_model"]),
            "temperature": float(state_snapshot.effective_settings["llm.temperature"]),
            "max_output_tokens": int(state_snapshot.effective_settings["llm.max_output_tokens"]),
        },
    }


# Block: Current observation builder
def _build_current_observation(
    *,
    pending_input: PendingInputRecord,
    resolved_at: int,
) -> dict[str, Any]:
    input_kind = str(pending_input.payload["input_kind"])
    base_observation = {
        "source": pending_input.source,
        "channel": pending_input.channel,
        "input_kind": input_kind,
        "captured_at": pending_input.created_at,
        "captured_at_utc_text": _utc_text(pending_input.created_at),
        "captured_at_local_text": _local_text(pending_input.created_at),
        "relative_time_text": _relative_time_text(resolved_at, pending_input.created_at),
    }
    if input_kind == "chat_message":
        text = pending_input.payload.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("chat_message.text must be non-empty string")
        return {
            **base_observation,
            "observation_text": text,
            "text": text,
        }
    if input_kind == "network_result":
        summary_text = pending_input.payload.get("summary_text")
        query = pending_input.payload.get("query")
        source_task_id = pending_input.payload.get("source_task_id")
        if not isinstance(summary_text, str) or not summary_text:
            raise ValueError("network_result.summary_text must be non-empty string")
        if not isinstance(query, str) or not query:
            raise ValueError("network_result.query must be non-empty string")
        if not isinstance(source_task_id, str) or not source_task_id:
            raise ValueError("network_result.source_task_id must be non-empty string")
        return {
            **base_observation,
            "observation_text": summary_text,
            "query": query,
            "summary_text": summary_text,
            "source_task_id": source_task_id,
        }
    raise ValueError("unsupported current_observation input_kind")


# Block: Task snapshot builder
def _build_task_snapshot(
    *,
    task_snapshot: dict[str, Any],
    resolved_at: int,
) -> dict[str, Any]:
    return {
        "active_tasks": [
            _task_snapshot_entry_for_cognition(task_entry, resolved_at=resolved_at)
            for task_entry in task_snapshot["active_tasks"]
        ],
        "waiting_external_tasks": [
            _task_snapshot_entry_for_cognition(task_entry, resolved_at=resolved_at)
            for task_entry in task_snapshot["waiting_external_tasks"]
        ],
    }


def _task_snapshot_entry_for_cognition(
    task_entry: dict[str, Any],
    *,
    resolved_at: int,
) -> dict[str, Any]:
    updated_at = int(task_entry["updated_at"])
    created_at = int(task_entry["created_at"])
    return {
        **task_entry,
        "created_at_utc_text": _utc_text(created_at),
        "created_at_local_text": _local_text(created_at),
        "updated_at_utc_text": _utc_text(updated_at),
        "updated_at_local_text": _local_text(updated_at),
        "relative_time_text": _relative_time_text(resolved_at, updated_at),
    }


# Block: Memory bundle builder
def _build_memory_bundle(
    *,
    memory_snapshot: dict[str, Any],
    current_observation: dict[str, Any],
    resolved_at: int,
) -> dict[str, Any]:
    selected_working_memory = _select_memory_entries(
        memory_entries=memory_snapshot["working_memory_items"],
        current_observation=current_observation,
        limit=3,
    )
    selected_semantic_memory = _select_memory_entries(
        memory_entries=memory_snapshot["semantic_items"],
        current_observation=current_observation,
        limit=3,
    )
    selected_recent_events = _select_recent_events(
        event_entries=memory_snapshot["recent_event_window"],
        current_observation=current_observation,
        limit=5,
    )
    return {
        "working_memory_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in selected_working_memory
        ],
        "episodic_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in memory_snapshot["episodic_items"]
        ],
        "semantic_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in selected_semantic_memory
        ],
        "affective_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in memory_snapshot["affective_items"]
        ],
        "relationship_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in memory_snapshot["relationship_items"]
        ],
        "reflection_items": [
            _memory_entry_for_cognition(memory_entry, resolved_at=resolved_at)
            for memory_entry in memory_snapshot["reflection_items"]
        ],
        "recent_event_window": [
            _recent_event_for_cognition(event_entry, resolved_at=resolved_at)
            for event_entry in selected_recent_events
        ],
    }


def _select_memory_entries(
    *,
    memory_entries: list[dict[str, Any]],
    current_observation: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("memory selection limit must be positive")
    scored_entries: list[tuple[float, int, dict[str, Any]]] = []
    for memory_entry in memory_entries:
        score = _memory_relevance_score(
            memory_entry=memory_entry,
            current_observation=current_observation,
        )
        if score <= 0.0:
            continue
        scored_entries.append(
            (
                score,
                int(memory_entry["updated_at"]),
                memory_entry,
            )
        )
    scored_entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [
        memory_entry
        for _, _, memory_entry in scored_entries[:limit]
    ]


def _select_recent_events(
    *,
    event_entries: list[dict[str, Any]],
    current_observation: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("recent event selection limit must be positive")
    scored_entries: list[tuple[float, int, dict[str, Any]]] = []
    for event_entry in event_entries:
        score = _event_relevance_score(
            event_entry=event_entry,
            current_observation=current_observation,
        )
        if score <= 0.0:
            continue
        scored_entries.append(
            (
                score,
                int(event_entry["created_at"]),
                event_entry,
            )
        )
    scored_entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [
        event_entry
        for _, _, event_entry in scored_entries[:limit]
    ]


def _memory_relevance_score(
    *,
    memory_entry: dict[str, Any],
    current_observation: dict[str, Any],
) -> float:
    body_text = memory_entry["body_text"]
    if not isinstance(body_text, str) or not body_text:
        raise ValueError("memory entry body_text must be non-empty string")
    payload = memory_entry["payload"]
    if not isinstance(payload, dict):
        raise ValueError("memory entry payload must be object")
    score = 0.0
    for text_hint in _observation_text_hints(current_observation):
        if text_hint in body_text:
            score += 1.0
    query_hint = _observation_query_hint(current_observation)
    if query_hint is not None:
        payload_query = payload.get("query")
        if payload_query == query_hint:
            score += 1.5
    source_task_id = current_observation.get("source_task_id")
    if isinstance(source_task_id, str) and source_task_id:
        payload_source_task_id = payload.get("source_task_id")
        if payload_source_task_id == source_task_id:
            score += 2.0
    score += min(1.0, float(memory_entry["importance"])) * 0.2
    score += min(1.0, float(memory_entry["memory_strength"])) * 0.2
    return score


def _event_relevance_score(
    *,
    event_entry: dict[str, Any],
    current_observation: dict[str, Any],
) -> float:
    summary_text = event_entry["summary_text"]
    if not isinstance(summary_text, str) or not summary_text:
        raise ValueError("recent event summary_text must be non-empty string")
    score = 0.0
    for text_hint in _observation_text_hints(current_observation):
        if text_hint in summary_text:
            score += 1.0
    if current_observation["input_kind"] == "network_result":
        if event_entry["source"] == "network_result":
            score += 1.0
    return score


def _observation_text_hints(current_observation: dict[str, Any]) -> list[str]:
    observation_text = current_observation["observation_text"]
    if not isinstance(observation_text, str) or not observation_text:
        raise ValueError("current_observation.observation_text must be non-empty string")
    hints: list[str] = [observation_text]
    for token in _text_hint_tokens(observation_text):
        if token not in hints:
            hints.append(token)
    query_hint = _observation_query_hint(current_observation)
    if query_hint is not None and query_hint not in hints:
        hints.append(query_hint)
    return hints


def _observation_query_hint(current_observation: dict[str, Any]) -> str | None:
    if current_observation["input_kind"] == "network_result":
        query = current_observation.get("query")
        if not isinstance(query, str) or not query:
            raise ValueError("network_result query must be non-empty string")
        return query
    text = current_observation.get("text")
    if not isinstance(text, str) or not text:
        raise ValueError("chat_message text must be non-empty string")
    return None


def _text_hint_tokens(text: str) -> list[str]:
    normalized_text = text
    for separator in ("　", "\n", "\t", ",", "、", ".", "。", "!", "！", "?", "？", ":", "：", ";", "；", "(", ")", "（", "）", "[", "]", "「", "」", "『", "』", "/", "／"):
        normalized_text = normalized_text.replace(separator, " ")
    tokens: list[str] = []
    for raw_token in normalized_text.split(" "):
        token = raw_token.strip()
        if len(token) < 2:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def _memory_entry_for_cognition(
    memory_entry: dict[str, Any],
    *,
    resolved_at: int,
) -> dict[str, Any]:
    updated_at = int(memory_entry["updated_at"])
    created_at = int(memory_entry["created_at"])
    last_confirmed_at = int(memory_entry["last_confirmed_at"])
    return {
        **memory_entry,
        "created_at_utc_text": _utc_text(created_at),
        "created_at_local_text": _local_text(created_at),
        "updated_at_utc_text": _utc_text(updated_at),
        "updated_at_local_text": _local_text(updated_at),
        "last_confirmed_at_utc_text": _utc_text(last_confirmed_at),
        "last_confirmed_at_local_text": _local_text(last_confirmed_at),
        "relative_time_text": _relative_time_text(resolved_at, updated_at),
    }


def _recent_event_for_cognition(
    event_entry: dict[str, Any],
    *,
    resolved_at: int,
) -> dict[str, Any]:
    created_at = int(event_entry["created_at"])
    return {
        **event_entry,
        "created_at_utc_text": _utc_text(created_at),
        "created_at_local_text": _local_text(created_at),
        "relative_time_text": _relative_time_text(resolved_at, created_at),
    }


# Block: Selection profile
def _build_selection_profile(state_snapshot: CognitionStateSnapshot) -> dict[str, Any]:
    personality = state_snapshot.self_state["personality"]
    current_emotion = state_snapshot.self_state["current_emotion"]
    relationship_overview = state_snapshot.self_state["relationship_overview"]
    priority_effects = state_snapshot.drive_state["priority_effects"]
    return {
        "trait_values": dict(personality["trait_values"]),
        "interaction_style": dict(personality["preferred_interaction_style"]),
        "relationship_priorities": _build_relationship_priorities(relationship_overview),
        "learned_preferences": list(personality["learned_preferences"]),
        "learned_aversions": list(personality["learned_aversions"]),
        "habit_biases": dict(personality["habit_biases"]),
        "emotion_bias": dict(current_emotion["active_biases"]),
        "drive_bias": {
            "task_progress_bias": _normalized_signed_number(
                priority_effects["task_progress_bias"],
                field_name="drive_state.priority_effects.task_progress_bias",
            ),
            "exploration_bias": _normalized_signed_number(
                priority_effects["exploration_bias"],
                field_name="drive_state.priority_effects.exploration_bias",
            ),
            "maintenance_bias": _normalized_signed_number(
                priority_effects["maintenance_bias"],
                field_name="drive_state.priority_effects.maintenance_bias",
            ),
            "social_bias": _normalized_signed_number(
                priority_effects["social_bias"],
                field_name="drive_state.priority_effects.social_bias",
            ),
        },
    }


# Block: Relationship priorities
def _build_relationship_priorities(relationship_overview: dict[str, Any]) -> list[dict[str, Any]]:
    relationships = relationship_overview["relationships"]
    if not isinstance(relationships, list):
        raise ValueError("self_state.relationship_overview.relationships must be list")
    priorities: list[dict[str, Any]] = []
    for relationship in relationships[:3]:
        if not isinstance(relationship, dict):
            raise ValueError("self_state.relationship_overview.relationships item must be object")
        target_ref = relationship["target_ref"]
        if not isinstance(target_ref, str) or not target_ref:
            raise ValueError("relationship.target_ref must be non-empty string")
        priorities.append(
            {
                "target_ref": target_ref,
                "priority_weight": _normalized_number(
                    relationship["attention_weight"],
                    field_name="relationship.attention_weight",
                ),
                "reason_tag": _relationship_reason_tag(relationship),
            }
        )
    return priorities


# Block: Relationship reason
def _relationship_reason_tag(relationship: dict[str, Any]) -> str:
    waiting_response = relationship["waiting_response"]
    if not isinstance(waiting_response, bool):
        raise ValueError("relationship.waiting_response must be boolean")
    if waiting_response is True:
        return "pending_relation"
    if _normalized_number(
        relationship["care_commitment"],
        field_name="relationship.care_commitment",
    ) >= 0.70:
        return "care_target"
    if _normalized_number(
        relationship["recent_tension"],
        field_name="relationship.recent_tension",
    ) >= 0.60:
        return "recent_tension"
    if _normalized_number(
        relationship["recent_positive_contact"],
        field_name="relationship.recent_positive_contact",
    ) >= 0.60:
        return "recent_positive_contact"
    return "care_target"


# Block: Time helpers
def _build_time_context(resolved_at: int) -> dict[str, Any]:
    local_now = datetime.fromtimestamp(resolved_at / 1000, tz=timezone.utc).astimezone()
    timezone_name = local_now.tzname() or "UTC"
    return {
        "current_time_unix_ms": resolved_at,
        "current_time_utc_text": _utc_text(resolved_at),
        "current_time_local_text": _local_text(resolved_at),
        "timezone_name": timezone_name,
        "relative_reference_text": "0秒前",
    }


def _utc_text(unix_ms: int) -> str:
    return datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _local_text(unix_ms: int) -> str:
    local_dt = datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc).astimezone()
    timezone_name = local_dt.tzname() or "UTC"
    return local_dt.strftime(f"%Y-%m-%d %H:%M:%S {timezone_name}")


def _relative_time_text(now_ms: int, past_ms: int) -> str:
    delta_seconds = max(0, (now_ms - past_ms) // 1000)
    if delta_seconds < 60:
        return f"{delta_seconds}秒前"
    delta_minutes = delta_seconds // 60
    if delta_minutes < 60:
        return f"{delta_minutes}分前"
    delta_hours = delta_minutes // 60
    if delta_hours < 24:
        return f"{delta_hours}時間前"
    delta_days = delta_hours // 24
    return f"{delta_days}日前"


# Block: Numeric helper
def _normalized_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    numeric_value = float(value)
    if numeric_value < 0.0:
        return 0.0
    if numeric_value > 1.0:
        return 1.0
    return numeric_value


def _normalized_signed_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric")
    numeric_value = float(value)
    if numeric_value < -1.0:
        return -1.0
    if numeric_value > 1.0:
        return 1.0
    return numeric_value
