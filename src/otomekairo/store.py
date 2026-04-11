from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import sqlite_vec

from otomekairo.state_store import StateStore
from otomekairo.store_schema import MEMORY_DB_FILE_NAME, StoreSchemaMixin


# 保存
class SQLiteMemoryStore(StoreSchemaMixin):
    def __init__(self, root_dir: Path) -> None:
        # パス群
        self.root_dir = root_dir
        self.memory_db_path = root_dir / MEMORY_DB_FILE_NAME

        # 初期化
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
        # トランザクション
        with self._memory_db() as conn:
            # cycle summary追加
            self._insert_cycle_summary(conn, cycle_summary)

            # イベント追加
            for event in events:
                self._insert_event(conn, event)

            # retrieval追加
            self._insert_retrieval_run(conn, retrieval_run)

            # trace追加
            self._insert_cycle_trace(conn, cycle_trace)

    def append_events(self, *, events: list[dict[str, Any]]) -> None:
        # 空
        if not events:
            return

        # トランザクション
        with self._memory_db() as conn:
            for event in events:
                self._insert_event(conn, event)

    def replace_cycle_trace(self, *, cycle_trace: dict[str, Any]) -> None:
        # トランザクション
        with self._memory_db() as conn:
            self._insert_cycle_trace(conn, cycle_trace)

    def persist_turn_consolidation(
        self,
        *,
        episode: dict[str, Any] | None,
        memory_actions: list[dict[str, Any]],
        affect_updates: list[dict[str, Any]],
    ) -> None:
        # トランザクション
        with self._memory_db() as conn:
            # episode追加
            if episode is not None:
                self._insert_episode(conn, episode)

            # 記憶アクション群
            for action in memory_actions:
                self._apply_memory_action(conn, action)

            # affectUpdates保存
            for affect_update in affect_updates:
                self._upsert_affect_state(conn, affect_update)

    def persist_memory_actions(self, *, memory_actions: list[dict[str, Any]]) -> None:
        # 空
        if not memory_actions:
            return

        # トランザクション
        with self._memory_db() as conn:
            for action in memory_actions:
                self._apply_memory_action(conn, action)

    def upsert_reflection_run(self, *, reflection_run: dict[str, Any]) -> None:
        # トランザクション
        with self._memory_db() as conn:
            self._insert_reflection_run(conn, reflection_run)

    def list_cycle_summaries(self, limit: int) -> list[dict[str, Any]]:
        # クエリ
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

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def get_cycle_trace(self, cycle_id: str) -> dict[str, Any] | None:
        # クエリ
        with self._memory_db() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM cycle_traces
                WHERE cycle_id = ?
                """,
                (cycle_id,),
            ).fetchone()

        # 結果
        if row is None:
            return None
        return json.loads(row["payload_json"])

    def get_latest_reflection_run(
        self,
        memory_set_id: str,
        *,
        result_status: str | None = None,
    ) -> dict[str, Any] | None:
        # Query部品群
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

        # クエリ
        with self._memory_db() as conn:
            row = conn.execute(
                query,
                params,
            ).fetchone()

        # 結果
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
        # クエリ
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

        # 結果
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
        # 空
        if not event_ids or limit <= 0:
            return []

        # Query部品群
        requested_ids = [
            event_id
            for event_id in event_ids
            if isinstance(event_id, str)
        ]
        if not requested_ids:
            return []
        placeholders = ", ".join("?" for _ in requested_ids)

        # クエリ
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

        # 順序付け
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

        # 結果
        return ordered

    def count_cycle_summaries_since(
        self,
        *,
        memory_set_id: str,
        since_iso: str | None,
    ) -> int:
        # Query部品群
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

        # クエリ
        with self._memory_db() as conn:
            count = conn.execute(query, params).fetchone()[0]

        # 結果
        return int(count)

    def count_high_salience_episodes_since(
        self,
        *,
        memory_set_id: str,
        since_iso: str | None,
        salience_threshold: float,
    ) -> int:
        # Query部品群
        clauses = ["memory_set_id = ?", "salience >= ?"]
        params: list[Any] = [memory_set_id, salience_threshold]
        if isinstance(since_iso, str) and since_iso:
            clauses.append("formed_at > ?")
            params.append(since_iso)

        query = f"""
            SELECT COUNT(*)
            FROM episodes
            WHERE {" AND ".join(clauses)}
        """

        # クエリ
        with self._memory_db() as conn:
            count = conn.execute(query, params).fetchone()[0]

        # 結果
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
        # クエリ
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

        # 結果
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
        # Query部品群
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]

        # スコープFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(scope_type = ? AND scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        # スコープTypes
        if scope_types:
            placeholders = ", ".join("?" for _ in scope_types)
            clauses.append(f"scope_type IN ({placeholders})")
            params.extend(scope_types)

        # Include記憶Types
        if include_memory_types:
            placeholders = ", ".join("?" for _ in include_memory_types)
            clauses.append(f"memory_type IN ({placeholders})")
            params.extend(include_memory_types)

        # Exclude記憶Types
        if exclude_memory_types:
            placeholders = ", ".join("?" for _ in exclude_memory_types)
            clauses.append(f"memory_type NOT IN ({placeholders})")
            params.extend(exclude_memory_types)

        # status群
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)

        # commitment状態群
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

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # 結果
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
        # Query部品群
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]

        # status群
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)

        # スコープTypes
        if scope_types:
            placeholders = ", ".join("?" for _ in scope_types)
            clauses.append(f"scope_type IN ({placeholders})")
            params.extend(scope_types)

        # Include記憶Types
        if include_memory_types:
            placeholders = ", ".join("?" for _ in include_memory_types)
            clauses.append(f"memory_type IN ({placeholders})")
            params.extend(include_memory_types)

        # Exclude記憶Types
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

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def list_episodes_for_recall(
        self,
        *,
        memory_set_id: str,
        scope_filters: list[tuple[str, str]] | None = None,
        require_open_loops: bool = False,
        limit: int,
    ) -> list[dict[str, Any]]:
        # Query部品群
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]

        # スコープFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(primary_scope_type = ? AND primary_scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        # 未完了ループ絞り込み
        if require_open_loops:
            clauses.append("has_open_loops = 1")

        query = f"""
            SELECT payload_json
            FROM episodes
            WHERE {" AND ".join(clauses)}
            ORDER BY has_open_loops DESC, salience DESC, formed_at DESC, rowid DESC
            LIMIT ?
        """

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def list_episodes_for_reflection(
        self,
        *,
        memory_set_id: str,
        since_iso: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        # Query部品群
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]
        if isinstance(since_iso, str) and since_iso:
            clauses.append("formed_at > ?")
            params.append(since_iso)

        query = f"""
            SELECT payload_json
            FROM episodes
            WHERE {" AND ".join(clauses)}
            ORDER BY formed_at DESC, salience DESC, rowid DESC
            LIMIT ?
        """

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def list_recent_episodes_for_series(
        self,
        *,
        memory_set_id: str,
        primary_scope_type: str,
        primary_scope_key: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM episodes
                WHERE memory_set_id = ?
                  AND primary_scope_type = ?
                  AND primary_scope_key = ?
                ORDER BY formed_at DESC, rowid DESC
                LIMIT ?
                """,
                (
                    memory_set_id,
                    primary_scope_type,
                    primary_scope_key,
                    limit,
                ),
            ).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def list_affect_states_for_context(
        self,
        *,
        memory_set_id: str,
        scope_filters: list[tuple[str, str]] | None = None,
        layers: list[str] | None = None,
        limit: int,
    ) -> list[dict[str, Any]]:
        # Query部品群
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]

        # スコープFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(target_scope_type = ? AND target_scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        # レイヤー群
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

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def upsert_vector_index_entries(
        self,
        *,
        entries: list[dict[str, Any]],
        embedding_dimension: int,
    ) -> None:
        # 空
        if not entries:
            return

        # トランザクション
        with self._memory_db() as conn:
            self._ensure_vector_tables(conn, embedding_dimension)

            # upsert群
            for entry in entries:
                existing_row = conn.execute(
                    """
                    SELECT vector_entry_id
                    FROM vector_index_entries
                    WHERE memory_set_id = ?
                      AND source_kind = ?
                      AND source_id = ?
                      AND embedding_signature = ?
                    """,
                    (
                        entry["memory_set_id"],
                        entry["source_kind"],
                        entry["source_id"],
                        entry["embedding_signature"],
                    ),
                ).fetchone()

                # 識別情報
                vector_entry_id: int
                if existing_row is None:
                    cursor = conn.execute(
                        """
                        INSERT INTO vector_index_entries (
                            memory_set_id,
                            source_kind,
                            source_id,
                            embedding_signature,
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
                            entry["embedding_signature"],
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

                # ベクトルupsert
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
        embedding_signature: str,
        query_embedding: list[float],
        embedding_dimension: int,
        limit: int,
        scope_filters: list[tuple[str, str]] | None = None,
        scope_types: list[str] | None = None,
        exclude_source_types: list[str] | None = None,
        statuses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        # 空
        if not query_embedding or limit <= 0:
            return []

        # Query部品群
        clauses = [
            "meta.memory_set_id = ?",
            "meta.embedding_signature = ?",
        ]
        params: list[Any] = [
            sqlite_vec.serialize_float32(query_embedding),
            limit,
            memory_set_id,
            embedding_signature,
        ]

        # スコープFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(meta.scope_type = ? AND meta.scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        # スコープTypes
        if scope_types:
            placeholders = ", ".join("?" for _ in scope_types)
            clauses.append(f"meta.scope_type IN ({placeholders})")
            params.extend(scope_types)

        # 除外source type群
        if exclude_source_types:
            placeholders = ", ".join("?" for _ in exclude_source_types)
            clauses.append(f"meta.source_type NOT IN ({placeholders})")
            params.extend(exclude_source_types)

        # status群
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

        # クエリ
        with self._memory_db() as conn:
            self._ensure_vector_tables(conn, embedding_dimension)
            rows = conn.execute(query, (*params, limit)).fetchall()

        # 結果
        return [
            {
                "record": json.loads(row["payload_json"]),
                "distance": float(row["distance"]),
            }
            for row in rows
        ]

    def search_episode_vector_entries(
        self,
        *,
        memory_set_id: str,
        embedding_signature: str,
        query_embedding: list[float],
        embedding_dimension: int,
        limit: int,
        scope_filters: list[tuple[str, str]] | None = None,
        require_open_loops: bool = False,
    ) -> list[dict[str, Any]]:
        # 空
        if not query_embedding or limit <= 0:
            return []

        # Query部品群
        clauses = [
            "meta.memory_set_id = ?",
            "meta.embedding_signature = ?",
        ]
        params: list[Any] = [
            sqlite_vec.serialize_float32(query_embedding),
            limit,
            memory_set_id,
            embedding_signature,
        ]

        # スコープFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(meta.scope_type = ? AND meta.scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        # 未完了Loops
        if require_open_loops:
            clauses.append("meta.has_open_loops = 1")

        query = f"""
            SELECT episode.payload_json, episode_vec.distance
            FROM episode_vec
            JOIN vector_index_entries AS meta
              ON meta.vector_entry_id = episode_vec.id
            JOIN episodes AS episode
              ON episode.episode_id = meta.source_id
            WHERE episode_vec.embedding MATCH ?
              AND k = ?
              AND {" AND ".join(clauses)}
            ORDER BY episode_vec.distance ASC, meta.has_open_loops DESC, meta.salience DESC
            LIMIT ?
        """

        # クエリ
        with self._memory_db() as conn:
            self._ensure_vector_tables(conn, embedding_dimension)
            rows = conn.execute(query, (*params, limit)).fetchall()

        # 結果
        return [
            {
                "record": json.loads(row["payload_json"]),
                "distance": float(row["distance"]),
            }
            for row in rows
        ]

    def delete_memory_set_records(self, memory_set_id: str) -> None:
        # トランザクション
        with self._memory_db() as conn:
            # ベクトル削除
            self._delete_vector_index_entries(conn, memory_set_id)

            # 削除順序
            conn.execute("DELETE FROM revisions WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM affect_state WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM memory_units WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM episodes WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM reflection_runs WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM events WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM retrieval_runs WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM cycle_traces WHERE selected_memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM cycle_summaries WHERE selected_memory_set_id = ?", (memory_set_id,))

    def clone_memory_set_records(
        self,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
    ) -> None:
        # トランザクション
        with self._memory_db() as conn:
            cycle_id_map, event_id_map = self._clone_event_records(
                conn,
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
            )
            episode_id_map = self._clone_episode_records(
                conn,
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
                cycle_id_map=cycle_id_map,
                event_id_map=event_id_map,
            )
            memory_unit_id_map = self._clone_memory_unit_records(
                conn,
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
                event_id_map=event_id_map,
            )
            self._clone_revision_records(
                conn,
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
                event_id_map=event_id_map,
                memory_unit_id_map=memory_unit_id_map,
            )
            self._clone_affect_state_records(
                conn,
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
            )
            self._clone_reflection_run_records(
                conn,
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
                episode_id_map=episode_id_map,
                memory_unit_id_map=memory_unit_id_map,
            )
            self._clone_vector_index_entries(
                conn,
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
                episode_id_map=episode_id_map,
                memory_unit_id_map=memory_unit_id_map,
            )

    def _clone_event_records(
        self,
        conn: sqlite3.Connection,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
    ) -> tuple[dict[str, str], dict[str, str]]:
        cycle_id_map: dict[str, str] = {}
        event_id_map: dict[str, str] = {}
        source_events = self._load_payload_rows(conn, "events", source_memory_set_id)
        for record in source_events:
            source_cycle_id = record["cycle_id"]
            cycle_id_map.setdefault(source_cycle_id, f"cycle:{uuid.uuid4().hex}")
            old_event_id = record["event_id"]
            event_id_map[old_event_id] = f"event:{uuid.uuid4().hex}"
            self._insert_event(
                conn,
                {
                    **record,
                    "event_id": event_id_map[old_event_id],
                    "cycle_id": cycle_id_map[source_cycle_id],
                    "memory_set_id": target_memory_set_id,
                },
            )
        return cycle_id_map, event_id_map

    def _clone_episode_records(
        self,
        conn: sqlite3.Connection,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
        cycle_id_map: dict[str, str],
        event_id_map: dict[str, str],
    ) -> dict[str, str]:
        episode_id_map: dict[str, str] = {}
        source_episodes = self._load_payload_rows(conn, "episodes", source_memory_set_id)
        cloned_episodes: list[dict[str, Any]] = []
        for record in source_episodes:
            source_cycle_id = record["cycle_id"]
            cycle_id_map.setdefault(source_cycle_id, f"cycle:{uuid.uuid4().hex}")
            old_episode_id = record["episode_id"]
            episode_id_map[old_episode_id] = f"episode:{uuid.uuid4().hex}"
            cloned_episodes.append(
                {
                    **record,
                    "episode_id": episode_id_map[old_episode_id],
                    "cycle_id": cycle_id_map[source_cycle_id],
                    "memory_set_id": target_memory_set_id,
                }
            )

        for record in cloned_episodes:
            record["linked_event_ids"] = [
                event_id_map.get(event_id, event_id)
                for event_id in record.get("linked_event_ids", [])
            ]
            episode_series_id = record.get("episode_series_id")
            if isinstance(episode_series_id, str) and episode_series_id in episode_id_map:
                record["episode_series_id"] = episode_id_map[episode_series_id]
            self._insert_episode(conn, record)
        return episode_id_map

    def _clone_memory_unit_records(
        self,
        conn: sqlite3.Connection,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
        event_id_map: dict[str, str],
    ) -> dict[str, str]:
        memory_unit_id_map: dict[str, str] = {}
        source_memory_units = self._load_payload_rows(conn, "memory_units", source_memory_set_id)
        for record in source_memory_units:
            old_memory_unit_id = record["memory_unit_id"]
            memory_unit_id_map[old_memory_unit_id] = f"memory_unit:{uuid.uuid4().hex}"
            self._upsert_memory_unit(
                conn,
                {
                    **record,
                    "memory_unit_id": memory_unit_id_map[old_memory_unit_id],
                    "memory_set_id": target_memory_set_id,
                    "evidence_event_ids": [
                        event_id_map.get(event_id, event_id)
                        for event_id in record.get("evidence_event_ids", [])
                    ],
                },
            )
        return memory_unit_id_map

    def _clone_revision_records(
        self,
        conn: sqlite3.Connection,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
        event_id_map: dict[str, str],
        memory_unit_id_map: dict[str, str],
    ) -> None:
        source_revisions = self._load_payload_rows(conn, "revisions", source_memory_set_id)
        for record in source_revisions:
            self._insert_revision(
                conn,
                {
                    **record,
                    "revision_id": f"revision:{uuid.uuid4().hex}",
                    "memory_set_id": target_memory_set_id,
                    "memory_unit_id": memory_unit_id_map.get(record["memory_unit_id"], record["memory_unit_id"]),
                    "related_memory_unit_ids": [
                        memory_unit_id_map.get(memory_unit_id, memory_unit_id)
                        for memory_unit_id in record.get("related_memory_unit_ids", [])
                    ],
                    "before_snapshot": self._clone_memory_unit_snapshot(
                        record.get("before_snapshot"),
                        event_id_map=event_id_map,
                        memory_unit_id_map=memory_unit_id_map,
                    ),
                    "after_snapshot": self._clone_memory_unit_snapshot(
                        record.get("after_snapshot"),
                        event_id_map=event_id_map,
                        memory_unit_id_map=memory_unit_id_map,
                    ),
                    "evidence_event_ids": [
                        event_id_map.get(event_id, event_id)
                        for event_id in record.get("evidence_event_ids", [])
                    ],
                },
            )

    def _clone_affect_state_records(
        self,
        conn: sqlite3.Connection,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
    ) -> None:
        source_affect_states = self._load_payload_rows(conn, "affect_state", source_memory_set_id)
        for record in source_affect_states:
            self._upsert_affect_state(
                conn,
                {
                    **record,
                    "affect_state_id": f"affect_state:{uuid.uuid4().hex}",
                    "memory_set_id": target_memory_set_id,
                },
            )

    def _clone_reflection_run_records(
        self,
        conn: sqlite3.Connection,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
        episode_id_map: dict[str, str],
        memory_unit_id_map: dict[str, str],
    ) -> None:
        source_reflection_runs = self._load_payload_rows(conn, "reflection_runs", source_memory_set_id)
        for record in source_reflection_runs:
            self._insert_reflection_run(
                conn,
                {
                    **record,
                    "reflection_run_id": f"reflection_run:{uuid.uuid4().hex}",
                    "memory_set_id": target_memory_set_id,
                    "source_episode_ids": [
                        episode_id_map.get(episode_id, episode_id)
                        for episode_id in record.get("source_episode_ids", [])
                    ],
                    "affected_memory_unit_ids": [
                        memory_unit_id_map.get(memory_unit_id, memory_unit_id)
                        for memory_unit_id in record.get("affected_memory_unit_ids", [])
                    ],
                },
            )

    def _insert_event(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # 追加
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
        # 追加
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
        # 追加
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
        # trace項目
        cycle_summary = record.get("cycle_summary", {})

        # 追加
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
                cycle_summary.get("selected_memory_set_id", ""),
                self._to_json(record),
            ),
        )

    def _insert_episode(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # 派生項目
        open_loops = record.get("open_loops", [])

        # 追加
        conn.execute(
            """
            INSERT OR REPLACE INTO episodes (
                episode_id,
                cycle_id,
                memory_set_id,
                episode_type,
                episode_series_id,
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["episode_id"],
                record["cycle_id"],
                record["memory_set_id"],
                record["episode_type"],
                record.get("episode_series_id"),
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
        # 追加
        conn.execute(
            """
            INSERT OR REPLACE INTO reflection_runs (
                reflection_run_id,
                memory_set_id,
                started_at,
                finished_at,
                result_status,
                trigger_reasons_json,
                source_episode_ids_json,
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
                self._to_json(record.get("source_episode_ids", [])),
                self._to_json(record.get("affected_memory_unit_ids", [])),
                self._to_json(record),
            ),
        )

    def _ensure_vector_tables(self, conn: sqlite3.Connection, embedding_dimension: int) -> None:
        # 次元確認
        if embedding_dimension <= 0:
            raise ValueError("embedding_dimension must be positive.")

        # memory unitテーブル
        self._ensure_vector_table(
            conn,
            table_name="memory_unit_vec",
            embedding_dimension=embedding_dimension,
        )

        # episodeテーブル
        self._ensure_vector_table(
            conn,
            table_name="episode_vec",
            embedding_dimension=embedding_dimension,
        )

    def _ensure_vector_table(self, conn: sqlite3.Connection, *, table_name: str, embedding_dimension: int) -> None:
        # 既存スキーマ
        schema_row = conn.execute(
            """
            SELECT sql
            FROM sqlite_schema
            WHERE type = 'table'
              AND name = ?
            """,
            (table_name,),
        ).fetchone()

        # 作成
        if schema_row is None:
            conn.execute(
                f"""
                CREATE VIRTUAL TABLE {table_name}
                USING vec0(id INTEGER PRIMARY KEY, embedding FLOAT[{embedding_dimension}])
                """
            )
            return

        # 検証
        schema_sql = schema_row["sql"] or ""
        if f"FLOAT[{embedding_dimension}]".lower() not in schema_sql.lower():
            raise ValueError(f"{table_name} dimension does not match current embedding_dimension.")

    def _delete_vector_index_entries(self, conn: sqlite3.Connection, memory_set_id: str) -> None:
        # クエリ
        rows = conn.execute(
            """
            SELECT vector_entry_id, source_kind
            FROM vector_index_entries
            WHERE memory_set_id = ?
            """,
            (memory_set_id,),
        ).fetchall()

        # 空
        if not rows:
            return

        # ベクトル削除
        for row in rows:
            table_name = self._vector_table_name(row["source_kind"])
            if self._table_exists(conn, table_name):
                conn.execute(
                    f"DELETE FROM {table_name} WHERE id = ?",
                    (row["vector_entry_id"],),
                )

        # メタデータ削除
        conn.execute(
            "DELETE FROM vector_index_entries WHERE memory_set_id = ?",
            (memory_set_id,),
        )

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        # クエリ
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
        # マッピング
        if source_kind == "memory_unit":
            return "memory_unit_vec"
        if source_kind == "episode":
            return "episode_vec"
        raise ValueError(f"Unsupported source_kind: {source_kind}")

    def _apply_memory_action(self, conn: sqlite3.Connection, action: dict[str, Any]) -> None:
        # 操作読み取り
        operation = action["operation"]
        memory_unit = action.get("memory_unit")

        # 何もしない処理
        if operation == "noop":
            return

        # memory unit upsert実行
        if memory_unit is not None:
            self._upsert_memory_unit(conn, memory_unit)

        # 改訂追加
        self._insert_revision(conn, action)

    def _upsert_memory_unit(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # 追加
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
        # payload構築
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

        # 追加
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
        # 既存検索
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

        # 識別解決
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

        # upsert実行
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
        # 直列化
        return json.dumps(payload, ensure_ascii=False)

    def _load_payload_rows(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        memory_set_id: str,
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            f"""
            SELECT payload_json
            FROM {table_name}
            WHERE memory_set_id = ?
            ORDER BY rowid ASC
            """,
            (memory_set_id,),
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def _clone_memory_unit_snapshot(
        self,
        snapshot: dict[str, Any] | None,
        *,
        event_id_map: dict[str, str],
        memory_unit_id_map: dict[str, str],
    ) -> dict[str, Any] | None:
        if snapshot is None:
            return None
        cloned_snapshot = {
            **snapshot,
            "memory_unit_id": memory_unit_id_map.get(snapshot["memory_unit_id"], snapshot["memory_unit_id"]),
            "evidence_event_ids": [
                event_id_map.get(event_id, event_id)
                for event_id in snapshot.get("evidence_event_ids", [])
            ],
        }
        return cloned_snapshot

    def _clone_vector_index_entries(
        self,
        conn: sqlite3.Connection,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
        episode_id_map: dict[str, str],
        memory_unit_id_map: dict[str, str],
    ) -> None:
        rows = conn.execute(
            """
            SELECT *
            FROM vector_index_entries
            WHERE memory_set_id = ?
            ORDER BY vector_entry_id ASC
            """,
            (source_memory_set_id,),
        ).fetchall()

        for row in rows:
            source_kind = row["source_kind"]
            source_id = row["source_id"]
            if source_kind == "episode":
                target_source_id = episode_id_map.get(source_id)
            elif source_kind == "memory_unit":
                target_source_id = memory_unit_id_map.get(source_id)
            else:
                continue
            if target_source_id is None:
                continue

            cursor = conn.execute(
                """
                INSERT INTO vector_index_entries (
                    memory_set_id,
                    source_kind,
                    source_id,
                    embedding_signature,
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
                    target_memory_set_id,
                    source_kind,
                    target_source_id,
                    row["embedding_signature"],
                    row["source_text"],
                    row["scope_type"],
                    row["scope_key"],
                    row["source_type"],
                    row["status"],
                    row["salience"],
                    row["has_open_loops"],
                    row["updated_at"],
                    row["text_hash"],
                ),
            )
            new_vector_entry_id = int(cursor.lastrowid)

            vector_blob = conn.execute(
                f"SELECT embedding FROM {self._vector_table_name(source_kind)} WHERE id = ?",
                (row["vector_entry_id"],),
            ).fetchone()
            if vector_blob is None:
                continue
            conn.execute(
                f"INSERT INTO {self._vector_table_name(source_kind)}(id, embedding) VALUES (?, ?)",
                (new_vector_entry_id, vector_blob["embedding"]),
            )


# ファサード
class FileStore:
    def __init__(self, root_dir: Path) -> None:
        # 依存関係
        self.root_dir = root_dir
        self.state_store = StateStore(root_dir)
        self.memory_store = SQLiteMemoryStore(root_dir)
        self.state_path = self.state_store.state_path
        self.memory_db_path = self.memory_store.memory_db_path

    def read_state(self) -> dict:
        # 委譲
        return self.state_store.read_state()

    def write_state(self, state: dict) -> None:
        # 委譲
        self.state_store.write_state(state)

    def __getattr__(self, name: str) -> Any:
        # 委譲
        return getattr(self.memory_store, name)
