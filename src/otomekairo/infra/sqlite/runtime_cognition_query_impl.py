"""SQLite の cognition state query 実装。"""

from __future__ import annotations

import json

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite.runtime_cognition_base_query_impl import load_cognition_base_state
from otomekairo.infra.sqlite.runtime_memory_snapshot_query_impl import load_cognition_memory_snapshot
from otomekairo.infra.sqlite_store_snapshots import (
    _build_memory_snapshot_rows,
    _build_task_snapshot_rows,
    _memory_snapshot_entry,
    _preference_snapshot_entry,
)
from otomekairo.schema.runtime_types import CognitionStateSnapshot


# Block: 認知状態読み出し
def read_cognition_state(
    backend: SqliteBackend,
    default_settings: dict[str, object],
    *,
    observation_hint_text: str | None = None,
) -> CognitionStateSnapshot:
    base_state = load_cognition_base_state(
        backend=backend,
        default_settings=default_settings,
    )
    memory_snapshot_state = load_cognition_memory_snapshot(
        backend=backend,
        effective_settings=dict(base_state["effective_settings"]),
        embedding_model=str(base_state["embedding_model"]),
        observation_hint_text=observation_hint_text,
    )
    return CognitionStateSnapshot(
        self_state={
            "personality": json.loads(base_state["self_row"]["personality_json"]),
            "current_emotion": json.loads(base_state["self_row"]["current_emotion_json"]),
            "long_term_goals": json.loads(base_state["self_row"]["long_term_goals_json"]),
            "relationship_overview": json.loads(base_state["self_row"]["relationship_overview_json"]),
            "invariants": json.loads(base_state["self_row"]["invariants_json"]),
            "personality_updated_at": int(base_state["self_row"]["personality_updated_at"]),
            "updated_at": int(base_state["self_row"]["updated_at"]),
        },
        attention_state={
            "primary_focus": json.loads(base_state["attention_row"]["primary_focus_json"]),
            "secondary_focuses": json.loads(base_state["attention_row"]["secondary_focuses_json"]),
            "suppressed_items": json.loads(base_state["attention_row"]["suppressed_items_json"]),
            "revisit_queue": json.loads(base_state["attention_row"]["revisit_queue_json"]),
            "updated_at": int(base_state["attention_row"]["updated_at"]),
        },
        body_state={
            "posture": json.loads(base_state["body_row"]["posture_json"]),
            "mobility": json.loads(base_state["body_row"]["mobility_json"]),
            "sensor_availability": json.loads(base_state["body_row"]["sensor_availability_json"]),
            "output_locks": json.loads(base_state["body_row"]["output_locks_json"]),
            "load": json.loads(base_state["body_row"]["load_json"]),
            "updated_at": int(base_state["body_row"]["updated_at"]),
        },
        world_state={
            "location": json.loads(base_state["world_row"]["location_json"]),
            "situation_summary": str(base_state["world_row"]["situation_summary"]),
            "surroundings": json.loads(base_state["world_row"]["surroundings_json"]),
            "affordances": json.loads(base_state["world_row"]["affordances_json"]),
            "constraints": json.loads(base_state["world_row"]["constraints_json"]),
            "attention_targets": json.loads(base_state["world_row"]["attention_targets_json"]),
            "external_waits": json.loads(base_state["world_row"]["external_waits_json"]),
            "updated_at": int(base_state["world_row"]["updated_at"]),
        },
        drive_state={
            "drive_levels": json.loads(base_state["drive_row"]["drive_levels_json"]),
            "priority_effects": json.loads(base_state["drive_row"]["priority_effects_json"]),
            "updated_at": int(base_state["drive_row"]["updated_at"]),
        },
        task_snapshot=_build_task_snapshot_rows(
            active_task_rows=base_state["active_task_rows"],
            waiting_task_rows=base_state["waiting_task_rows"],
        ),
        memory_snapshot=_build_memory_snapshot_rows(
            recent_event_rows=memory_snapshot_state["recent_event_rows"],
            memory_rows=memory_snapshot_state["memory_rows"],
            affect_rows=memory_snapshot_state["affect_rows"],
            stable_preference_rows=memory_snapshot_state["retrieval_preference_rows"],
            event_link_rows=memory_snapshot_state["event_link_rows"],
            event_thread_rows=memory_snapshot_state["event_thread_rows"],
            event_about_time_rows=memory_snapshot_state["event_about_time_rows"],
            event_entity_rows=memory_snapshot_state["event_entity_rows"],
            state_link_rows=memory_snapshot_state["state_link_rows"],
            state_about_time_rows=memory_snapshot_state["state_about_time_rows"],
            state_entity_rows=memory_snapshot_state["state_entity_rows"],
        ),
        stable_preference_items=[
            _preference_snapshot_entry(row)
            for row in memory_snapshot_state["stable_preference_rows"]
        ],
        stable_long_mood_item=(
            _memory_snapshot_entry(memory_snapshot_state["stable_long_mood_row"])
            if memory_snapshot_state["stable_long_mood_row"] is not None
            else None
        ),
        retrieval_profile=memory_snapshot_state["retrieval_profile"],
        effective_settings=base_state["effective_settings"],
    )
