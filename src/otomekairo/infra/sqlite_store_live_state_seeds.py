"""Default live-state seed builders for the SQLite state store."""

from __future__ import annotations

from typing import Any


# Block: Attention seed
def _attention_primary_focus_seed() -> dict[str, Any]:
    return {
        "focus_ref": "attention:idle",
        "focus_kind": "idle",
        "summary": "待機中",
        "score_hint": 0.0,
        "reason_codes": ["idle"],
    }


# Block: Body posture seed
def _body_state_posture_seed() -> dict[str, Any]:
    return {"mode": "idle"}


# Block: Body mobility seed
def _body_state_mobility_seed() -> dict[str, Any]:
    return {"mode": "fixed"}


# Block: Body sensor seed
def _body_state_sensor_availability_seed() -> dict[str, Any]:
    return {
        "camera": False,
        "microphone": False,
    }


# Block: Body output locks seed
def _body_state_output_locks_seed() -> dict[str, Any]:
    return {
        "speech": False,
        "camera": False,
        "browse": False,
    }


# Block: Body load seed
def _body_state_load_seed() -> dict[str, Any]:
    return {
        "task_queue_pressure": 0.0,
        "interaction_load": 0.0,
        "last_action_count": 0,
    }


# Block: World location seed
def _world_state_location_seed() -> dict[str, Any]:
    return {
        "state": "unknown",
        "channel": "browser_chat",
    }


# Block: World situation seed
def _world_state_situation_summary_seed() -> str:
    return "待機中"


# Block: World surroundings seed
def _world_state_surroundings_seed() -> dict[str, Any]:
    return {
        "current_channel": "browser_chat",
        "latest_observation_kind": "idle",
        "latest_observation_source": "runtime",
        "latest_action_types": [],
    }


# Block: World affordances seed
def _world_state_affordances_seed() -> dict[str, Any]:
    return {
        "speak": True,
        "browse": True,
        "notify": True,
        "look": False,
    }


# Block: World constraints seed
def _world_state_constraints_seed() -> dict[str, Any]:
    return {
        "look_unavailable": True,
        "live_microphone_input_unavailable": True,
        "has_external_wait": False,
    }


# Block: World attention targets seed
def _world_state_attention_targets_seed() -> dict[str, Any]:
    return {
        "primary_focus": _attention_primary_focus_seed(),
        "secondary_focuses": [],
    }


# Block: World external waits seed
def _world_state_external_waits_seed() -> dict[str, Any]:
    return {
        "count": 0,
        "items": [],
    }


# Block: Self personality seed
def _self_state_personality_seed() -> dict[str, Any]:
    return {
        "trait_values": {
            "sociability": 0.0,
            "caution": 0.0,
            "curiosity": 0.0,
            "persistence": 0.0,
            "warmth": 0.0,
            "assertiveness": 0.0,
            "novelty_preference": 0.0,
        },
        "preferred_interaction_style": {
            "speech_tone": "neutral",
            "distance_style": "balanced",
            "confirmation_style": "balanced",
            "response_pace": "balanced",
        },
        "habit_biases": {
            "preferred_action_types": [],
            "preferred_observation_kinds": [],
            "avoided_action_styles": [],
        },
    }


# Block: Self current emotion seed
def _self_state_current_emotion_seed() -> dict[str, Any]:
    return {
        "primary_label": "calm",
        "valence": 0.0,
        "arousal": 0.0,
        "dominance": 0.0,
        "stability": 1.0,
        "active_biases": {
            "caution_bias": 0.0,
            "approach_bias": 0.0,
            "avoidance_bias": 0.0,
            "speech_intensity_bias": 0.0,
        },
    }


# Block: Self long-term goals seed
def _self_state_long_term_goals_seed() -> dict[str, Any]:
    return {"goals": []}


# Block: Self relationship overview seed
def _self_state_relationship_overview_seed() -> dict[str, Any]:
    return {"relationships": []}


# Block: Self invariants seed
def _self_state_invariants_seed() -> dict[str, Any]:
    return {
        "forbidden_action_types": [],
        "forbidden_action_styles": [],
        "required_confirmation_for": [],
        "protected_targets": [],
    }


# Block: Drive priority effects seed
def _drive_state_priority_effects_seed() -> dict[str, Any]:
    return {
        "task_progress_bias": 0.0,
        "exploration_bias": 0.0,
        "maintenance_bias": 0.0,
        "social_bias": 0.0,
    }


# Block: Drive levels seed
def _drive_state_drive_levels_seed() -> dict[str, Any]:
    return {
        "task_progress": 0.0,
        "exploration": 0.0,
        "maintenance": 0.0,
        "social": 0.0,
    }
