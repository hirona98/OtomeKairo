"""SQLite の cognition 記憶スナップショット query 実装。"""

from __future__ import annotations

from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
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
from otomekairo.infra.sqlite_store_snapshots import _read_stable_preference_projection_rows
from otomekairo.infra.sqlite_store_vectors import (
    _merge_ranked_event_rows,
    _merge_ranked_memory_rows,
    _search_vec_similarity_hits,
)


# Block: stable preference 件数制限
STABLE_PREFERENCE_BUCKET_LIMIT = 8
RETRIEVAL_STABLE_PREFERENCE_BUCKET_LIMIT = 24


# Block: cognition 記憶スナップショット読み出し
def load_cognition_memory_snapshot(
    *,
    backend: SqliteBackend,
    effective_settings: dict[str, object],
    embedding_model: str,
    observation_hint_text: str | None,
) -> dict[str, Any]:
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
    return {
        "retrieval_profile": retrieval_profile,
        "recent_event_rows": recent_event_rows,
        "memory_rows": memory_rows,
        "affect_rows": affect_rows,
        "stable_preference_rows": stable_preference_rows,
        "retrieval_preference_rows": retrieval_preference_rows,
        "stable_long_mood_row": stable_long_mood_row,
        "event_link_rows": event_link_rows,
        "event_thread_rows": event_thread_rows,
        "event_about_time_rows": event_about_time_rows,
        "event_entity_rows": event_entity_rows,
        "state_link_rows": state_link_rows,
        "state_about_time_rows": state_about_time_rows,
        "state_entity_rows": state_entity_rows,
    }
