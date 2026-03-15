"""SQLite の cognition state query 実装。"""

from __future__ import annotations

import json

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite_store_legacy_runtime import _merge_runtime_settings
from otomekairo.infra.sqlite_store_memory_helpers import (
    _fetch_event_about_time_for_memory_snapshot,
    _fetch_event_entities_for_memory_snapshot,
    _fetch_event_links_for_memory_snapshot,
    _fetch_event_threads_for_memory_snapshot,
    _fetch_state_about_time_for_memory_snapshot,
    _fetch_state_entities_for_memory_snapshot,
    _fetch_state_links_for_memory_snapshot,
)
from otomekairo.infra.sqlite_store_settings_editor import _read_active_retrieval_profile
from otomekairo.infra.sqlite_store_snapshots import (
    _build_memory_snapshot_rows,
    _build_task_snapshot_rows,
    _memory_snapshot_entry,
    _preference_snapshot_entry,
    _read_stable_preference_projection_rows,
)
from otomekairo.infra.sqlite_store_vectors import (
    _merge_ranked_event_rows,
    _merge_ranked_memory_rows,
    _search_vec_similarity_hits,
)
from otomekairo.schema.runtime_types import CognitionStateSnapshot


# Block: stable preference 件数制限
STABLE_PREFERENCE_BUCKET_LIMIT = 8
RETRIEVAL_STABLE_PREFERENCE_BUCKET_LIMIT = 24


# Block: 認知状態読み出し
def read_cognition_state(
    backend: SqliteBackend,
    default_settings: dict[str, object],
    *,
    observation_hint_text: str | None = None,
) -> CognitionStateSnapshot:
    with backend._connect() as connection:
        self_row = connection.execute(
            """
            SELECT
                personality_json,
                current_emotion_json,
                long_term_goals_json,
                relationship_overview_json,
                invariants_json,
                personality_updated_at,
                updated_at
            FROM self_state
            WHERE row_id = 1
            """
        ).fetchone()
        attention_row = connection.execute(
            """
            SELECT
                primary_focus_json,
                secondary_focuses_json,
                suppressed_items_json,
                revisit_queue_json,
                updated_at
            FROM attention_state
            WHERE row_id = 1
            """
        ).fetchone()
        body_row = connection.execute(
            """
            SELECT
                posture_json,
                mobility_json,
                sensor_availability_json,
                output_locks_json,
                load_json,
                updated_at
            FROM body_state
            WHERE row_id = 1
            """
        ).fetchone()
        world_row = connection.execute(
            """
            SELECT
                location_json,
                situation_summary,
                surroundings_json,
                affordances_json,
                constraints_json,
                attention_targets_json,
                external_waits_json,
                updated_at
            FROM world_state
            WHERE row_id = 1
            """
        ).fetchone()
        drive_row = connection.execute(
            """
            SELECT
                drive_levels_json,
                priority_effects_json,
                updated_at
            FROM drive_state
            WHERE row_id = 1
            """
        ).fetchone()
        runtime_settings_row = connection.execute(
            """
            SELECT values_json
            FROM runtime_settings
            WHERE row_id = 1
            """
        ).fetchone()
        active_task_rows = connection.execute(
            """
            SELECT
                task_id,
                task_kind,
                task_status,
                goal_hint,
                completion_hint_json,
                resume_condition_json,
                interruptible,
                priority,
                created_at,
                updated_at,
                title,
                step_hints_json
            FROM task_state
            WHERE task_status = 'active'
            ORDER BY priority DESC, updated_at DESC
            LIMIT 3
            """
        ).fetchall()
        waiting_task_rows = connection.execute(
            """
            SELECT
                task_id,
                task_kind,
                task_status,
                goal_hint,
                completion_hint_json,
                resume_condition_json,
                interruptible,
                priority,
                created_at,
                updated_at,
                title,
                step_hints_json
            FROM task_state
            WHERE task_status = 'waiting_external'
            ORDER BY priority DESC, updated_at DESC
            LIMIT 3
            """
        ).fetchall()
    if (
        self_row is None
        or attention_row is None
        or body_row is None
        or world_row is None
        or drive_row is None
        or runtime_settings_row is None
    ):
        raise RuntimeError("singleton state rows are missing")
    effective_settings = _merge_runtime_settings(
        default_settings,
        json.loads(runtime_settings_row["values_json"]),
    )
    embedding_model = effective_settings.get("llm.embedding_model")
    if not isinstance(embedding_model, str) or not embedding_model:
        raise RuntimeError("llm.embedding_model must be non-empty string")
    with backend._connect() as connection:
        retrieval_profile = _read_active_retrieval_profile(connection=connection)
        similarity_limit = int(retrieval_profile["semantic_top_k"])
        similar_episodes_limit = effective_settings.get("memory.similar_episodes_limit")
        if isinstance(similar_episodes_limit, int) and not isinstance(similar_episodes_limit, bool):
            similarity_limit = min(similarity_limit, similar_episodes_limit)
        recent_event_fetch_limit = max(8, int(retrieval_profile["recent_window_limit"]))
        memory_fetch_limit = max(16, similarity_limit * 2)
        recent_event_rows = connection.execute(
            """
            SELECT
                events.event_id,
                events.source,
                events.kind,
                events.observation_summary,
                events.action_summary,
                events.result_summary,
                events.created_at
            FROM events
            WHERE events.searchable = 1
            ORDER BY events.created_at DESC
            LIMIT ?
            """,
            (recent_event_fetch_limit,),
        ).fetchall()
        memory_rows = connection.execute(
            """
            SELECT
                memory_state_id,
                memory_kind,
                body_text,
                payload_json,
                confidence,
                importance,
                memory_strength,
                created_at,
                updated_at,
                last_confirmed_at
            FROM memory_states
            WHERE searchable = 1
              AND memory_kind IN ('summary', 'fact', 'relation', 'long_mood_state', 'reflection_note')
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (memory_fetch_limit,),
        ).fetchall()
        affect_rows = connection.execute(
            """
            SELECT
                event_affects.event_affect_id,
                event_affects.event_id,
                event_affects.moment_affect_text,
                event_affects.moment_affect_labels_json,
                event_affects.vad_json,
                event_affects.confidence,
                event_affects.created_at,
                events.source,
                events.kind,
                events.observation_summary,
                events.action_summary,
                events.result_summary
            FROM event_affects
            INNER JOIN events
                    ON events.event_id = event_affects.event_id
            WHERE events.searchable = 1
            ORDER BY event_affects.created_at DESC
            LIMIT 6
            """
        ).fetchall()
        stable_preference_rows = _read_stable_preference_projection_rows(
            connection=connection,
            bucket_limit=STABLE_PREFERENCE_BUCKET_LIMIT,
        )
        retrieval_preference_rows = _read_stable_preference_projection_rows(
            connection=connection,
            bucket_limit=RETRIEVAL_STABLE_PREFERENCE_BUCKET_LIMIT,
        )
        stable_long_mood_row = connection.execute(
            """
            SELECT
                memory_state_id,
                memory_kind,
                body_text,
                payload_json,
                confidence,
                importance,
                memory_strength,
                created_at,
                updated_at,
                last_confirmed_at
            FROM memory_states
            WHERE memory_kind = 'long_mood_state'
            ORDER BY updated_at DESC, created_at DESC, memory_state_id DESC
            LIMIT 1
            """
        ).fetchone()
        if observation_hint_text is not None and observation_hint_text.strip():
            similarity_hits = _search_vec_similarity_hits(
                connection=connection,
                query_text=observation_hint_text.strip(),
                embedding_model=embedding_model,
                limit=similarity_limit,
            )
            recent_event_rows = _merge_ranked_event_rows(
                connection=connection,
                ranked_hits=similarity_hits,
                fallback_rows=recent_event_rows,
            )
            memory_rows = _merge_ranked_memory_rows(
                connection=connection,
                ranked_hits=similarity_hits,
                fallback_rows=memory_rows,
            )
        recent_event_ids = [str(row["event_id"]) for row in recent_event_rows]
        memory_state_ids = [str(row["memory_state_id"]) for row in memory_rows]
        event_link_rows = _fetch_event_links_for_memory_snapshot(
            connection=connection,
            event_ids=recent_event_ids,
        )
        event_thread_rows = _fetch_event_threads_for_memory_snapshot(
            connection=connection,
            event_ids=recent_event_ids,
        )
        event_about_time_rows = _fetch_event_about_time_for_memory_snapshot(
            connection=connection,
            event_ids=recent_event_ids,
        )
        event_entity_rows = _fetch_event_entities_for_memory_snapshot(
            connection=connection,
            event_ids=recent_event_ids,
        )
        state_link_rows = _fetch_state_links_for_memory_snapshot(
            connection=connection,
            memory_state_ids=memory_state_ids,
        )
        state_about_time_rows = _fetch_state_about_time_for_memory_snapshot(
            connection=connection,
            memory_state_ids=memory_state_ids,
        )
        state_entity_rows = _fetch_state_entities_for_memory_snapshot(
            connection=connection,
            memory_state_ids=memory_state_ids,
        )
    return CognitionStateSnapshot(
        self_state={
            "personality": json.loads(self_row["personality_json"]),
            "current_emotion": json.loads(self_row["current_emotion_json"]),
            "long_term_goals": json.loads(self_row["long_term_goals_json"]),
            "relationship_overview": json.loads(self_row["relationship_overview_json"]),
            "invariants": json.loads(self_row["invariants_json"]),
            "personality_updated_at": int(self_row["personality_updated_at"]),
            "updated_at": int(self_row["updated_at"]),
        },
        attention_state={
            "primary_focus": json.loads(attention_row["primary_focus_json"]),
            "secondary_focuses": json.loads(attention_row["secondary_focuses_json"]),
            "suppressed_items": json.loads(attention_row["suppressed_items_json"]),
            "revisit_queue": json.loads(attention_row["revisit_queue_json"]),
            "updated_at": int(attention_row["updated_at"]),
        },
        body_state={
            "posture": json.loads(body_row["posture_json"]),
            "mobility": json.loads(body_row["mobility_json"]),
            "sensor_availability": json.loads(body_row["sensor_availability_json"]),
            "output_locks": json.loads(body_row["output_locks_json"]),
            "load": json.loads(body_row["load_json"]),
            "updated_at": int(body_row["updated_at"]),
        },
        world_state={
            "location": json.loads(world_row["location_json"]),
            "situation_summary": str(world_row["situation_summary"]),
            "surroundings": json.loads(world_row["surroundings_json"]),
            "affordances": json.loads(world_row["affordances_json"]),
            "constraints": json.loads(world_row["constraints_json"]),
            "attention_targets": json.loads(world_row["attention_targets_json"]),
            "external_waits": json.loads(world_row["external_waits_json"]),
            "updated_at": int(world_row["updated_at"]),
        },
        drive_state={
            "drive_levels": json.loads(drive_row["drive_levels_json"]),
            "priority_effects": json.loads(drive_row["priority_effects_json"]),
            "updated_at": int(drive_row["updated_at"]),
        },
        task_snapshot=_build_task_snapshot_rows(
            active_task_rows=active_task_rows,
            waiting_task_rows=waiting_task_rows,
        ),
        memory_snapshot=_build_memory_snapshot_rows(
            recent_event_rows=recent_event_rows,
            memory_rows=memory_rows,
            affect_rows=affect_rows,
            stable_preference_rows=retrieval_preference_rows,
            event_link_rows=event_link_rows,
            event_thread_rows=event_thread_rows,
            event_about_time_rows=event_about_time_rows,
            event_entity_rows=event_entity_rows,
            state_link_rows=state_link_rows,
            state_about_time_rows=state_about_time_rows,
            state_entity_rows=state_entity_rows,
        ),
        stable_preference_items=[
            _preference_snapshot_entry(row)
            for row in stable_preference_rows
        ],
        stable_long_mood_item=(
            _memory_snapshot_entry(stable_long_mood_row)
            if stable_long_mood_row is not None
            else None
        ),
        retrieval_profile=retrieval_profile,
        effective_settings=effective_settings,
    )
