from __future__ import annotations

import json
import sqlite3
from typing import Any

import sqlite_vec


class StoreVectorMixin:
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
                    """,
                    (
                        entry["memory_set_id"],
                        entry["source_kind"],
                        entry["source_id"],
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
                            source_text,
                            scope_type,
                            scope_key,
                            source_type,
                            status,
                            salience,
                            has_open_loops,
                            updated_at,
                            text_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            entry["memory_set_id"],
                            entry["source_kind"],
                            entry["source_id"],
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
        ]
        params: list[Any] = [
            sqlite_vec.serialize_float32(query_embedding),
            limit,
            memory_set_id,
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
        ]
        params: list[Any] = [
            sqlite_vec.serialize_float32(query_embedding),
            limit,
            memory_set_id,
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

    def reset_memory_set_vector_index(self, memory_set_id: str) -> None:
        # トランザクション
        with self._memory_db() as conn:
            self._delete_vector_index_entries(conn, memory_set_id)

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
            try:
                conn.execute(
                    f"""
                    CREATE VIRTUAL TABLE {table_name}
                    USING vec0(id INTEGER PRIMARY KEY, embedding FLOAT[{embedding_dimension}])
                    """
                )
            except sqlite3.OperationalError as exc:
                if str(exc).endswith("already exists") is False:
                    raise
            schema_row = conn.execute(
                """
                SELECT sql
                FROM sqlite_schema
                WHERE type = 'table'
                  AND name = ?
                """,
                (table_name,),
            ).fetchone()
            if schema_row is None:
                raise RuntimeError(f"{table_name} was not available after creation attempt.")

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
