from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import sqlite_vec

from otomekairo.state_store import StateStore
from otomekairo.store_schema import MEMORY_DB_FILE_NAME, StoreSchemaMixin


# Store
class SQLiteMemoryStore(StoreSchemaMixin):
    def __init__(self, root_dir: Path) -> None:
        # Paths
        self.root_dir = root_dir
        self.memory_db_path = root_dir / MEMORY_DB_FILE_NAME

        # Initialization
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._initialize_memory_db()

    def persist_cycle_records(
        self,
        *,
        events: list[dict[str, Any]],
        retrieval_run: dict[str, Any],
        cycle_summary: dict[str, Any],
        cycle_trace: dict[str, Any],
    ) -> None:
        # Transaction
        with self._memory_db() as conn:
            # CycleSummaryInsert
            self._insert_cycle_summary(conn, cycle_summary)

            # EventInsert
            for event in events:
                self._insert_event(conn, event)

            # RetrievalInsert
            self._insert_retrieval_run(conn, retrieval_run)

            # TraceInsert
            self._insert_cycle_trace(conn, cycle_trace)

    def append_events(self, *, events: list[dict[str, Any]]) -> None:
        # Empty
        if not events:
            return

        # Transaction
        with self._memory_db() as conn:
            for event in events:
                self._insert_event(conn, event)

    def replace_cycle_trace(self, *, cycle_trace: dict[str, Any]) -> None:
        # Transaction
        with self._memory_db() as conn:
            self._insert_cycle_trace(conn, cycle_trace)

    def persist_turn_consolidation(
        self,
        *,
        episode_digest: dict[str, Any] | None,
        memory_actions: list[dict[str, Any]],
        affect_updates: list[dict[str, Any]],
    ) -> None:
        # Transaction
        with self._memory_db() as conn:
            # EpisodeDigestInsert
            if episode_digest is not None:
                self._insert_episode_digest(conn, episode_digest)

            # MemoryActions
            for action in memory_actions:
                self._apply_memory_action(conn, action)

            # AffectUpdates
            for affect_update in affect_updates:
                self._upsert_affect_state(conn, affect_update)

    def persist_memory_actions(self, *, memory_actions: list[dict[str, Any]]) -> None:
        # Empty
        if not memory_actions:
            return

        # Transaction
        with self._memory_db() as conn:
            for action in memory_actions:
                self._apply_memory_action(conn, action)

    def upsert_reflection_run(self, *, reflection_run: dict[str, Any]) -> None:
        # Transaction
        with self._memory_db() as conn:
            self._insert_reflection_run(conn, reflection_run)

    def list_cycle_summaries(self, limit: int) -> list[dict[str, Any]]:
        # Query
        with self._memory_db() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM cycle_summaries
                ORDER BY started_at DESC, rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        # Result
        return [json.loads(row["payload_json"]) for row in rows]

    def get_cycle_trace(self, cycle_id: str) -> dict[str, Any] | None:
        # Query
        with self._memory_db() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM cycle_traces
                WHERE cycle_id = ?
                """,
                (cycle_id,),
            ).fetchone()

        # Result
        if row is None:
            return None
        return json.loads(row["payload_json"])

    def get_latest_reflection_run(
        self,
        memory_set_id: str,
        *,
        result_status: str | None = None,
    ) -> dict[str, Any] | None:
        # QueryParts
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]
        if isinstance(result_status, str) and result_status:
            clauses.append("result_status = ?")
            params.append(result_status)

        query = f"""
            SELECT payload_json
            FROM reflection_runs
            WHERE {" AND ".join(clauses)}
            ORDER BY finished_at DESC, rowid DESC
            LIMIT 1
        """

        # Query
        with self._memory_db() as conn:
            row = conn.execute(
                query,
                params,
            ).fetchone()

        # Result
        if row is None:
            return None
        return json.loads(row["payload_json"])

    def load_recent_turns(
        self,
        *,
        memory_set_id: str,
        since_iso: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        # Query
        with self._memory_db() as conn:
            rows = conn.execute(
                """
                SELECT role, text, created_at
                FROM events
                WHERE memory_set_id = ?
                  AND kind IN ('observation', 'reply')
                  AND text IS NOT NULL
                  AND created_at >= ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (memory_set_id, since_iso, limit),
            ).fetchall()

        # Result
        turns = [
            {
                "role": row["role"],
                "text": row["text"],
                "created_at": row["created_at"],
            }
            for row in reversed(rows)
        ]
        return turns

    def load_events_for_evidence(
        self,
        *,
        memory_set_id: str,
        event_ids: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        # Empty
        if not event_ids or limit <= 0:
            return []

        # QueryParts
        requested_ids = [
            event_id
            for event_id in event_ids
            if isinstance(event_id, str)
        ]
        if not requested_ids:
            return []
        placeholders = ", ".join("?" for _ in requested_ids)

        # Query
        with self._memory_db() as conn:
            rows = conn.execute(
                f"""
                SELECT event_id, payload_json
                FROM events
                WHERE memory_set_id = ?
                  AND event_id IN ({placeholders})
                """,
                (memory_set_id, *requested_ids),
            ).fetchall()

        # Ordering
        rows_by_id = {
            row["event_id"]: json.loads(row["payload_json"])
            for row in rows
        }
        ordered: list[dict[str, Any]] = []
        for event_id in requested_ids:
            record = rows_by_id.get(event_id)
            if record is None:
                continue
            ordered.append(record)
            if len(ordered) >= limit:
                break

        # Result
        return ordered

    def count_cycle_summaries_since(
        self,
        *,
        memory_set_id: str,
        since_iso: str | None,
    ) -> int:
        # QueryParts
        clauses = ["selected_memory_set_id = ?", "failed = 0"]
        params: list[Any] = [memory_set_id]
        if isinstance(since_iso, str) and since_iso:
            clauses.append("started_at > ?")
            params.append(since_iso)

        query = f"""
            SELECT COUNT(*)
            FROM cycle_summaries
            WHERE {" AND ".join(clauses)}
        """

        # Query
        with self._memory_db() as conn:
            count = conn.execute(query, params).fetchone()[0]

        # Result
        return int(count)

    def count_high_salience_episode_digests_since(
        self,
        *,
        memory_set_id: str,
        since_iso: str | None,
        salience_threshold: float,
    ) -> int:
        # QueryParts
        clauses = ["memory_set_id = ?", "salience >= ?"]
        params: list[Any] = [memory_set_id, salience_threshold]
        if isinstance(since_iso, str) and since_iso:
            clauses.append("formed_at > ?")
            params.append(since_iso)

        query = f"""
            SELECT COUNT(*)
            FROM episode_digests
            WHERE {" AND ".join(clauses)}
        """

        # Query
        with self._memory_db() as conn:
            count = conn.execute(query, params).fetchone()[0]

        # Result
        return int(count)

    def find_memory_units_for_compare(
        self,
        *,
        memory_set_id: str,
        memory_type: str,
        scope_type: str,
        scope_key: str,
        subject_ref: str,
        predicate: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        # Query
        with self._memory_db() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM memory_units
                WHERE memory_set_id = ?
                  AND memory_type = ?
                  AND scope_type = ?
                  AND scope_key = ?
                  AND subject_ref = ?
                  AND predicate = ?
                  AND status NOT IN ('superseded', 'revoked')
                ORDER BY salience DESC, confidence DESC, formed_at DESC, rowid DESC
                LIMIT ?
                """,
                (
                    memory_set_id,
                    memory_type,
                    scope_type,
                    scope_key,
                    subject_ref,
                    predicate,
                    limit,
                ),
            ).fetchall()

        # Result
        return [json.loads(row["payload_json"]) for row in rows]

    def list_memory_units_for_recall(
        self,
        *,
        memory_set_id: str,
        scope_filters: list[tuple[str, str]] | None = None,
        scope_types: list[str] | None = None,
        include_memory_types: list[str] | None = None,
        exclude_memory_types: list[str] | None = None,
        statuses: list[str] | None = None,
        commitment_states: list[str] | None = None,
        limit: int,
    ) -> list[dict[str, Any]]:
        # QueryParts
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]

        # ScopeFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(scope_type = ? AND scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        # ScopeTypes
        if scope_types:
            placeholders = ", ".join("?" for _ in scope_types)
            clauses.append(f"scope_type IN ({placeholders})")
            params.extend(scope_types)

        # IncludeMemoryTypes
        if include_memory_types:
            placeholders = ", ".join("?" for _ in include_memory_types)
            clauses.append(f"memory_type IN ({placeholders})")
            params.extend(include_memory_types)

        # ExcludeMemoryTypes
        if exclude_memory_types:
            placeholders = ", ".join("?" for _ in exclude_memory_types)
            clauses.append(f"memory_type NOT IN ({placeholders})")
            params.extend(exclude_memory_types)

        # Statuses
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)

        # CommitmentStates
        if commitment_states:
            placeholders = ", ".join("?" for _ in commitment_states)
            clauses.append(f"commitment_state IN ({placeholders})")
            params.extend(commitment_states)

        query = f"""
            SELECT payload_json
            FROM memory_units
            WHERE {" AND ".join(clauses)}
            ORDER BY salience DESC, confidence DESC, COALESCE(last_confirmed_at, formed_at) DESC, rowid DESC
            LIMIT ?
        """

        # Query
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # Result
        return [json.loads(row["payload_json"]) for row in rows]

    def list_memory_units_for_reflection(
        self,
        *,
        memory_set_id: str,
        statuses: list[str] | None = None,
        scope_types: list[str] | None = None,
        include_memory_types: list[str] | None = None,
        exclude_memory_types: list[str] | None = None,
        limit: int,
    ) -> list[dict[str, Any]]:
        # QueryParts
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]

        # Statuses
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)

        # ScopeTypes
        if scope_types:
            placeholders = ", ".join("?" for _ in scope_types)
            clauses.append(f"scope_type IN ({placeholders})")
            params.extend(scope_types)

        # IncludeMemoryTypes
        if include_memory_types:
            placeholders = ", ".join("?" for _ in include_memory_types)
            clauses.append(f"memory_type IN ({placeholders})")
            params.extend(include_memory_types)

        # ExcludeMemoryTypes
        if exclude_memory_types:
            placeholders = ", ".join("?" for _ in exclude_memory_types)
            clauses.append(f"memory_type NOT IN ({placeholders})")
            params.extend(exclude_memory_types)

        query = f"""
            SELECT payload_json
            FROM memory_units
            WHERE {" AND ".join(clauses)}
            ORDER BY COALESCE(last_confirmed_at, formed_at) DESC, salience DESC, rowid DESC
            LIMIT ?
        """

        # Query
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # Result
        return [json.loads(row["payload_json"]) for row in rows]

    def list_episode_digests_for_recall(
        self,
        *,
        memory_set_id: str,
        scope_filters: list[tuple[str, str]] | None = None,
        require_open_loops: bool = False,
        limit: int,
    ) -> list[dict[str, Any]]:
        # QueryParts
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]

        # ScopeFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(primary_scope_type = ? AND primary_scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        # OpenLoopFilter
        if require_open_loops:
            clauses.append("has_open_loops = 1")

        query = f"""
            SELECT payload_json
            FROM episode_digests
            WHERE {" AND ".join(clauses)}
            ORDER BY has_open_loops DESC, salience DESC, formed_at DESC, rowid DESC
            LIMIT ?
        """

        # Query
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # Result
        return [json.loads(row["payload_json"]) for row in rows]

    def list_episode_digests_for_reflection(
        self,
        *,
        memory_set_id: str,
        since_iso: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        # QueryParts
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]
        if isinstance(since_iso, str) and since_iso:
            clauses.append("formed_at > ?")
            params.append(since_iso)

        query = f"""
            SELECT payload_json
            FROM episode_digests
            WHERE {" AND ".join(clauses)}
            ORDER BY formed_at DESC, salience DESC, rowid DESC
            LIMIT ?
        """

        # Query
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # Result
        return [json.loads(row["payload_json"]) for row in rows]

    def list_affect_states_for_context(
        self,
        *,
        memory_set_id: str,
        scope_filters: list[tuple[str, str]] | None = None,
        layers: list[str] | None = None,
        limit: int,
    ) -> list[dict[str, Any]]:
        # QueryParts
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]

        # ScopeFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(target_scope_type = ? AND target_scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        # Layers
        if layers:
            placeholders = ", ".join("?" for _ in layers)
            clauses.append(f"layer IN ({placeholders})")
            params.extend(layers)

        query = f"""
            SELECT payload_json
            FROM affect_state
            WHERE {" AND ".join(clauses)}
            ORDER BY intensity DESC, updated_at DESC, rowid DESC
            LIMIT ?
        """

        # Query
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # Result
        return [json.loads(row["payload_json"]) for row in rows]

    def upsert_vector_index_entries(
        self,
        *,
        entries: list[dict[str, Any]],
        embedding_dimension: int,
    ) -> None:
        # Empty
        if not entries:
            return

        # Transaction
        with self._memory_db() as conn:
            self._ensure_vector_tables(conn, embedding_dimension)

            # Upserts
            for entry in entries:
                existing_row = conn.execute(
                    """
                    SELECT vector_entry_id
                    FROM vector_index_entries
                    WHERE memory_set_id = ?
                      AND source_kind = ?
                      AND source_id = ?
                      AND embedding_preset = ?
                    """,
                    (
                        entry["memory_set_id"],
                        entry["source_kind"],
                        entry["source_id"],
                        entry["embedding_preset"],
                    ),
                ).fetchone()

                # Identity
                vector_entry_id: int
                if existing_row is None:
                    cursor = conn.execute(
                        """
                        INSERT INTO vector_index_entries (
                            memory_set_id,
                            source_kind,
                            source_id,
                            embedding_preset,
                            source_text,
                            scope_type,
                            scope_key,
                            source_type,
                            status,
                            salience,
                            has_open_loops,
                            updated_at,
                            text_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            entry["memory_set_id"],
                            entry["source_kind"],
                            entry["source_id"],
                            entry["embedding_preset"],
                            entry["source_text"],
                            entry["scope_type"],
                            entry["scope_key"],
                            entry["source_type"],
                            entry["status"],
                            entry["salience"],
                            int(bool(entry["has_open_loops"])),
                            entry["updated_at"],
                            entry["text_hash"],
                        ),
                    )
                    vector_entry_id = int(cursor.lastrowid)
                else:
                    vector_entry_id = int(existing_row["vector_entry_id"])
                    conn.execute(
                        """
                        UPDATE vector_index_entries
                        SET source_text = ?,
                            scope_type = ?,
                            scope_key = ?,
                            source_type = ?,
                            status = ?,
                            salience = ?,
                            has_open_loops = ?,
                            updated_at = ?,
                            text_hash = ?
                        WHERE vector_entry_id = ?
                        """,
                        (
                            entry["source_text"],
                            entry["scope_type"],
                            entry["scope_key"],
                            entry["source_type"],
                            entry["status"],
                            entry["salience"],
                            int(bool(entry["has_open_loops"])),
                            entry["updated_at"],
                            entry["text_hash"],
                            vector_entry_id,
                        ),
                    )

                # VectorUpsert
                vector_table_name = self._vector_table_name(entry["source_kind"])
                conn.execute(
                    f"DELETE FROM {vector_table_name} WHERE id = ?",
                    (vector_entry_id,),
                )
                conn.execute(
                    f"INSERT INTO {vector_table_name}(id, embedding) VALUES (?, ?)",
                    (
                        vector_entry_id,
                        sqlite_vec.serialize_float32(entry["embedding"]),
                    ),
                )

    def search_memory_unit_vector_entries(
        self,
        *,
        memory_set_id: str,
        embedding_preset: str,
        query_embedding: list[float],
        embedding_dimension: int,
        limit: int,
        scope_filters: list[tuple[str, str]] | None = None,
        scope_types: list[str] | None = None,
        exclude_source_types: list[str] | None = None,
        statuses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        # Empty
        if not query_embedding or limit <= 0:
            return []

        # QueryParts
        clauses = [
            "meta.memory_set_id = ?",
            "meta.embedding_preset = ?",
        ]
        params: list[Any] = [
            sqlite_vec.serialize_float32(query_embedding),
            limit,
            memory_set_id,
            embedding_preset,
        ]

        # ScopeFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(meta.scope_type = ? AND meta.scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        # ScopeTypes
        if scope_types:
            placeholders = ", ".join("?" for _ in scope_types)
            clauses.append(f"meta.scope_type IN ({placeholders})")
            params.extend(scope_types)

        # ExcludeSourceTypes
        if exclude_source_types:
            placeholders = ", ".join("?" for _ in exclude_source_types)
            clauses.append(f"meta.source_type NOT IN ({placeholders})")
            params.extend(exclude_source_types)

        # Statuses
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"meta.status IN ({placeholders})")
            params.extend(statuses)

        query = f"""
            SELECT unit.payload_json, memory_unit_vec.distance
            FROM memory_unit_vec
            JOIN vector_index_entries AS meta
              ON meta.vector_entry_id = memory_unit_vec.id
            JOIN memory_units AS unit
              ON unit.memory_unit_id = meta.source_id
            WHERE memory_unit_vec.embedding MATCH ?
              AND k = ?
              AND {" AND ".join(clauses)}
            ORDER BY memory_unit_vec.distance ASC, meta.salience DESC
            LIMIT ?
        """

        # Query
        with self._memory_db() as conn:
            self._ensure_vector_tables(conn, embedding_dimension)
            rows = conn.execute(query, (*params, limit)).fetchall()

        # Result
        return [
            {
                "record": json.loads(row["payload_json"]),
                "distance": float(row["distance"]),
            }
            for row in rows
        ]

    def search_episode_digest_vector_entries(
        self,
        *,
        memory_set_id: str,
        embedding_preset: str,
        query_embedding: list[float],
        embedding_dimension: int,
        limit: int,
        scope_filters: list[tuple[str, str]] | None = None,
        require_open_loops: bool = False,
    ) -> list[dict[str, Any]]:
        # Empty
        if not query_embedding or limit <= 0:
            return []

        # QueryParts
        clauses = [
            "meta.memory_set_id = ?",
            "meta.embedding_preset = ?",
        ]
        params: list[Any] = [
            sqlite_vec.serialize_float32(query_embedding),
            limit,
            memory_set_id,
            embedding_preset,
        ]

        # ScopeFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(meta.scope_type = ? AND meta.scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        # OpenLoops
        if require_open_loops:
            clauses.append("meta.has_open_loops = 1")

        query = f"""
            SELECT digest.payload_json, episode_digest_vec.distance
            FROM episode_digest_vec
            JOIN vector_index_entries AS meta
              ON meta.vector_entry_id = episode_digest_vec.id
            JOIN episode_digests AS digest
              ON digest.episode_digest_id = meta.source_id
            WHERE episode_digest_vec.embedding MATCH ?
              AND k = ?
              AND {" AND ".join(clauses)}
            ORDER BY episode_digest_vec.distance ASC, meta.has_open_loops DESC, meta.salience DESC
            LIMIT ?
        """

        # Query
        with self._memory_db() as conn:
            self._ensure_vector_tables(conn, embedding_dimension)
            rows = conn.execute(query, (*params, limit)).fetchall()

        # Result
        return [
            {
                "record": json.loads(row["payload_json"]),
                "distance": float(row["distance"]),
            }
            for row in rows
        ]

    def delete_memory_set_records(self, memory_set_id: str) -> None:
        # Transaction
        with self._memory_db() as conn:
            # VectorDelete
            self._delete_vector_index_entries(conn, memory_set_id)

            # DeleteOrder
            conn.execute("DELETE FROM revisions WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM affect_state WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM memory_units WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM episode_digests WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM reflection_runs WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM events WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM retrieval_runs WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM cycle_traces WHERE selected_memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM cycle_summaries WHERE selected_memory_set_id = ?", (memory_set_id,))

    def _insert_event(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # Insert
        conn.execute(
            """
            INSERT OR REPLACE INTO events (
                event_id,
                cycle_id,
                memory_set_id,
                kind,
                role,
                text,
                created_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["event_id"],
                record["cycle_id"],
                record["memory_set_id"],
                record["kind"],
                record.get("role"),
                record.get("text"),
                record["created_at"],
                self._to_json(record),
            ),
        )

    def _insert_retrieval_run(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # Insert
        conn.execute(
            """
            INSERT OR REPLACE INTO retrieval_runs (
                cycle_id,
                memory_set_id,
                started_at,
                finished_at,
                result_status,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record["cycle_id"],
                record["selected_memory_set_id"],
                record["started_at"],
                record["finished_at"],
                record["result_status"],
                self._to_json(record),
            ),
        )

    def _insert_cycle_summary(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # Insert
        conn.execute(
            """
            INSERT OR REPLACE INTO cycle_summaries (
                cycle_id,
                server_id,
                trigger_kind,
                started_at,
                finished_at,
                selected_persona_id,
                selected_memory_set_id,
                selected_model_preset_id,
                result_kind,
                failed,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["cycle_id"],
                record["server_id"],
                record["trigger_kind"],
                record["started_at"],
                record["finished_at"],
                record["selected_persona_id"],
                record["selected_memory_set_id"],
                record["selected_model_preset_id"],
                record["result_kind"],
                int(bool(record["failed"])),
                self._to_json(record),
            ),
        )

    def _insert_cycle_trace(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # TraceFields
        cycle_summary = record.get("cycle_summary", {})

        # Insert
        conn.execute(
            """
            INSERT OR REPLACE INTO cycle_traces (
                cycle_id,
                started_at,
                selected_memory_set_id,
                payload_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                record["cycle_id"],
                cycle_summary.get("started_at", ""),
                cycle_summary.get("selected_memory_set_id", "memory_set:legacy"),
                self._to_json(record),
            ),
        )

    def _insert_episode_digest(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # DerivedFields
        open_loops = record.get("open_loops", [])

        # Insert
        conn.execute(
            """
            INSERT OR REPLACE INTO episode_digests (
                episode_digest_id,
                cycle_id,
                memory_set_id,
                episode_type,
                primary_scope_type,
                primary_scope_key,
                summary_text,
                outcome_text,
                open_loops_json,
                has_open_loops,
                salience,
                formed_at,
                linked_event_ids_json,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["episode_digest_id"],
                record["cycle_id"],
                record["memory_set_id"],
                record["episode_type"],
                record["primary_scope_type"],
                record["primary_scope_key"],
                record["summary_text"],
                record.get("outcome_text"),
                self._to_json(open_loops),
                int(bool(open_loops)),
                record["salience"],
                record["formed_at"],
                self._to_json(record.get("linked_event_ids", [])),
                self._to_json(record),
            ),
        )

    def _insert_reflection_run(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # Insert
        conn.execute(
            """
            INSERT OR REPLACE INTO reflection_runs (
                reflection_run_id,
                memory_set_id,
                started_at,
                finished_at,
                result_status,
                trigger_reasons_json,
                source_episode_digest_ids_json,
                affected_memory_unit_ids_json,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["reflection_run_id"],
                record["memory_set_id"],
                record["started_at"],
                record["finished_at"],
                record["result_status"],
                self._to_json(record.get("trigger_reasons", [])),
                self._to_json(record.get("source_episode_digest_ids", [])),
                self._to_json(record.get("affected_memory_unit_ids", [])),
                self._to_json(record),
            ),
        )

    def _ensure_vector_tables(self, conn: sqlite3.Connection, embedding_dimension: int) -> None:
        # DimensionCheck
        if embedding_dimension <= 0:
            raise ValueError("embedding_dimension must be positive.")

        # MemoryUnitTable
        self._ensure_vector_table(
            conn,
            table_name="memory_unit_vec",
            embedding_dimension=embedding_dimension,
        )

        # EpisodeDigestTable
        self._ensure_vector_table(
            conn,
            table_name="episode_digest_vec",
            embedding_dimension=embedding_dimension,
        )

    def _ensure_vector_table(self, conn: sqlite3.Connection, *, table_name: str, embedding_dimension: int) -> None:
        # ExistingSchema
        schema_row = conn.execute(
            """
            SELECT sql
            FROM sqlite_schema
            WHERE type = 'table'
              AND name = ?
            """,
            (table_name,),
        ).fetchone()

        # Create
        if schema_row is None:
            conn.execute(
                f"""
                CREATE VIRTUAL TABLE {table_name}
                USING vec0(id INTEGER PRIMARY KEY, embedding FLOAT[{embedding_dimension}])
                """
            )
            return

        # Validate
        schema_sql = schema_row["sql"] or ""
        if f"FLOAT[{embedding_dimension}]".lower() not in schema_sql.lower():
            raise ValueError(f"{table_name} dimension does not match current embedding_dimension.")

    def _delete_vector_index_entries(self, conn: sqlite3.Connection, memory_set_id: str) -> None:
        # Query
        rows = conn.execute(
            """
            SELECT vector_entry_id, source_kind
            FROM vector_index_entries
            WHERE memory_set_id = ?
            """,
            (memory_set_id,),
        ).fetchall()

        # Empty
        if not rows:
            return

        # DeleteVectors
        for row in rows:
            table_name = self._vector_table_name(row["source_kind"])
            if self._table_exists(conn, table_name):
                conn.execute(
                    f"DELETE FROM {table_name} WHERE id = ?",
                    (row["vector_entry_id"],),
                )

        # DeleteMetadata
        conn.execute(
            "DELETE FROM vector_index_entries WHERE memory_set_id = ?",
            (memory_set_id,),
        )

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        # Query
        row = conn.execute(
            """
            SELECT 1
            FROM sqlite_schema
            WHERE name = ?
            """,
            (table_name,),
        ).fetchone()
        return row is not None

    def _vector_table_name(self, source_kind: str) -> str:
        # Mapping
        if source_kind == "memory_unit":
            return "memory_unit_vec"
        if source_kind == "episode_digest":
            return "episode_digest_vec"
        raise ValueError(f"Unsupported source_kind: {source_kind}")

    def _apply_memory_action(self, conn: sqlite3.Connection, action: dict[str, Any]) -> None:
        # OperationRead
        operation = action["operation"]
        memory_unit = action.get("memory_unit")

        # Noop
        if operation == "noop":
            return

        # UpsertMemoryUnit
        if memory_unit is not None:
            self._upsert_memory_unit(conn, memory_unit)

        # RevisionInsert
        self._insert_revision(conn, action)

    def _upsert_memory_unit(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # Insert
        conn.execute(
            """
            INSERT OR REPLACE INTO memory_units (
                memory_unit_id,
                memory_set_id,
                memory_type,
                scope_type,
                scope_key,
                subject_ref,
                predicate,
                object_ref_or_value,
                summary_text,
                status,
                commitment_state,
                confidence,
                salience,
                formed_at,
                last_confirmed_at,
                valid_from,
                valid_to,
                evidence_event_ids_json,
                qualifiers_json,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["memory_unit_id"],
                record["memory_set_id"],
                record["memory_type"],
                record["scope_type"],
                record["scope_key"],
                record["subject_ref"],
                record["predicate"],
                record.get("object_ref_or_value"),
                record["summary_text"],
                record["status"],
                record.get("commitment_state"),
                record["confidence"],
                record["salience"],
                record["formed_at"],
                record.get("last_confirmed_at"),
                record.get("valid_from"),
                record.get("valid_to"),
                self._to_json(record.get("evidence_event_ids", [])),
                self._to_json(record.get("qualifiers", {})),
                self._to_json(record),
            ),
        )

    def _insert_revision(self, conn: sqlite3.Connection, action: dict[str, Any]) -> None:
        # PayloadBuild
        revision = {
            "revision_id": action["revision_id"],
            "memory_set_id": action["memory_set_id"],
            "memory_unit_id": action["memory_unit_id"],
            "occurred_at": action["occurred_at"],
            "operation": action["operation"],
            "related_memory_unit_ids": action.get("related_memory_unit_ids", []),
            "before_snapshot": action.get("before_snapshot"),
            "after_snapshot": action.get("after_snapshot"),
            "reason": action["reason"],
            "evidence_event_ids": action.get("evidence_event_ids", []),
        }

        # Insert
        conn.execute(
            """
            INSERT OR REPLACE INTO revisions (
                revision_id,
                memory_set_id,
                memory_unit_id,
                occurred_at,
                operation,
                related_memory_unit_ids_json,
                before_snapshot_json,
                after_snapshot_json,
                reason,
                evidence_event_ids_json,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision["revision_id"],
                revision["memory_set_id"],
                revision["memory_unit_id"],
                revision["occurred_at"],
                revision["operation"],
                self._to_json(revision["related_memory_unit_ids"]),
                self._to_json(revision["before_snapshot"]) if revision["before_snapshot"] is not None else None,
                self._to_json(revision["after_snapshot"]) if revision["after_snapshot"] is not None else None,
                revision["reason"],
                self._to_json(revision["evidence_event_ids"]),
                self._to_json(revision),
            ),
        )

    def _upsert_affect_state(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # ExistingLookup
        existing_row = conn.execute(
            """
            SELECT affect_state_id, observed_at
            FROM affect_state
            WHERE memory_set_id = ?
              AND layer = ?
              AND target_scope_type = ?
              AND target_scope_key = ?
              AND affect_label = ?
            """,
            (
                record["memory_set_id"],
                record["layer"],
                record["target_scope_type"],
                record["target_scope_key"],
                record["affect_label"],
            ),
        ).fetchone()

        # IdentityResolve
        affect_state_id = record["affect_state_id"]
        observed_at = record["observed_at"]
        if existing_row is not None:
            affect_state_id = existing_row["affect_state_id"]
            observed_at = existing_row["observed_at"]

        payload = {
            **record,
            "affect_state_id": affect_state_id,
            "observed_at": observed_at,
        }

        # Upsert
        conn.execute(
            """
            INSERT OR REPLACE INTO affect_state (
                affect_state_id,
                memory_set_id,
                layer,
                target_scope_type,
                target_scope_key,
                affect_label,
                intensity,
                observed_at,
                updated_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["affect_state_id"],
                payload["memory_set_id"],
                payload["layer"],
                payload["target_scope_type"],
                payload["target_scope_key"],
                payload["affect_label"],
                payload["intensity"],
                payload["observed_at"],
                payload["updated_at"],
                self._to_json(payload),
            ),
        )

    def _to_json(self, payload: Any) -> str:
        # Serialize
        return json.dumps(payload, ensure_ascii=False)


# Facade
class FileStore:
    def __init__(self, root_dir: Path) -> None:
        # Dependencies
        self.root_dir = root_dir
        self.state_store = StateStore(root_dir)
        self.memory_store = SQLiteMemoryStore(root_dir)
        self.state_path = self.state_store.state_path
        self.memory_db_path = self.memory_store.memory_db_path

    def read_state(self) -> dict:
        # Delegate
        return self.state_store.read_state()

    def write_state(self, state: dict) -> None:
        # Delegate
        self.state_store.write_state(state)

    def __getattr__(self, name: str) -> Any:
        # Delegate
        return getattr(self.memory_store, name)
