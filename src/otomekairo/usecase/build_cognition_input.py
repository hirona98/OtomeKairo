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
) -> dict[str, Any]:
    if pending_input.payload["input_kind"] != "chat_message":
        raise ValueError("cognition_input is only supported for chat_message")
    selection_profile = _build_selection_profile(state_snapshot)
    return {
        "cycle_meta": {
            "cycle_id": cycle_id,
            "trigger_reason": "external_input",
            "input_id": pending_input.input_id,
            "input_kind": pending_input.payload["input_kind"],
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
        "task_snapshot": {
            "active_tasks": [],
            "waiting_external_tasks": [],
        },
        "attention_snapshot": state_snapshot.attention_state,
        "memory_bundle": {
            "working_memory_items": [],
            "episodic_items": [],
            "semantic_items": [],
            "affective_items": [],
            "relationship_items": [],
            "reflection_items": [],
            "recent_event_window": [],
        },
        "policy_snapshot": {
            "system_policy": {
                "respect_invariants": True,
                "allow_direct_state_write": False,
            },
            "runtime_policy": {
                "camera_enabled": bool(state_snapshot.effective_settings["sensors.camera.enabled"]),
                "microphone_enabled": bool(state_snapshot.effective_settings["sensors.microphone.enabled"]),
                "tts_enabled": bool(state_snapshot.effective_settings["output.tts.enabled"]),
            },
        },
        "skill_candidates": [],
        "current_observation": {
            "source": pending_input.source,
            "channel": pending_input.channel,
            "input_kind": pending_input.payload["input_kind"],
            "text": str(pending_input.payload["text"]),
            "captured_at": pending_input.created_at,
            "captured_at_utc_text": _utc_text(pending_input.created_at),
            "captured_at_local_text": _local_text(pending_input.created_at),
            "relative_time_text": _relative_time_text(resolved_at, pending_input.created_at),
        },
        "context_budget": {
            "max_tokens": int(state_snapshot.effective_settings["runtime.context_budget_tokens"]),
            "default_model": str(state_snapshot.effective_settings["llm.default_model"]),
            "temperature": float(state_snapshot.effective_settings["llm.temperature"]),
            "max_output_tokens": int(state_snapshot.effective_settings["llm.max_output_tokens"]),
        },
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
            "task_progress_bias": _normalized_signed_number(priority_effects.get("task_progress_bias", 0.0)),
            "exploration_bias": _normalized_signed_number(priority_effects.get("exploration_bias", 0.0)),
            "maintenance_bias": _normalized_signed_number(priority_effects.get("maintenance_bias", 0.0)),
            "social_bias": _normalized_signed_number(priority_effects.get("social_bias", 0.0)),
        },
    }


# Block: Relationship priorities
def _build_relationship_priorities(relationship_overview: dict[str, Any]) -> list[dict[str, Any]]:
    relationships = relationship_overview.get("relationships", [])
    priorities: list[dict[str, Any]] = []
    for relationship in relationships[:3]:
        target_ref = relationship.get("target_ref")
        if not isinstance(target_ref, str) or not target_ref:
            continue
        priorities.append(
            {
                "target_ref": target_ref,
                "priority_weight": _normalized_number(relationship.get("attention_weight", 0.0)),
                "reason_tag": _relationship_reason_tag(relationship),
            }
        )
    return priorities


# Block: Relationship reason
def _relationship_reason_tag(relationship: dict[str, Any]) -> str:
    if relationship.get("waiting_response") is True:
        return "pending_relation"
    if _normalized_number(relationship.get("care_commitment", 0.0)) >= 0.70:
        return "care_target"
    if _normalized_number(relationship.get("recent_tension", 0.0)) >= 0.60:
        return "recent_tension"
    if _normalized_number(relationship.get("recent_positive_contact", 0.0)) >= 0.60:
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
def _normalized_number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if not isinstance(value, (int, float)):
        return 0.0
    numeric_value = float(value)
    if numeric_value < 0.0:
        return 0.0
    if numeric_value > 1.0:
        return 1.0
    return numeric_value


def _normalized_signed_number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if not isinstance(value, (int, float)):
        return 0.0
    numeric_value = float(value)
    if numeric_value < -1.0:
        return -1.0
    if numeric_value > 1.0:
        return 1.0
    return numeric_value
