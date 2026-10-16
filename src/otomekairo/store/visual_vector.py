from __future__ import annotations

import json
import sqlite3
from typing import Any

import sqlite_vec


class StoreVisualVectorMixin:
    def list_visual_observation_vector_sources(
        self, *, memory_set_id: str, cycle_id: str, embedding_dimension: int,
    ) -> list[dict[str, Any]]:
        """検索対象の正本と索引状態を同じ snapshot から取得する。"""
        with self._memory_db() as conn:
            self._ensure_vector_table(
                conn, table_name="visual_observation_vec", embedding_dimension=embedding_dimension,
            )
            rows = conn.execute(
                """
                SELECT r.payload_json, m.text_hash, v.id AS indexed_id
                FROM visual_observation_records AS r
                LEFT JOIN visual_observation_vector_entries AS m
                  ON m.visual_observation_id = r.visual_observation_id
                 AND m.memory_set_id = r.memory_set_id
                LEFT JOIN visual_observation_vec AS v ON v.id = m.vector_entry_id
                WHERE r.memory_set_id = ? AND r.cycle_id != ?
                  AND r.retention_status != 'excluded'
                ORDER BY r.rowid
                """,
                (memory_set_id, cycle_id),
            ).fetchall()
        return [{"record": json.loads(row["payload_json"]), "text_hash": row["text_hash"],
                 "has_vector": row["indexed_id"] is not None} for row in rows]

    def upsert_visual_observation_vectors(
        self, *, entries: list[dict[str, Any]], embedding_dimension: int,
    ) -> None:
        with self._memory_db() as conn:
            self._ensure_vector_table(
                conn, table_name="visual_observation_vec", embedding_dimension=embedding_dimension,
            )
            for entry in entries:
                # 同期中に削除・除外された正本の索引を復活させない。
                row = conn.execute(
                    "SELECT detailed_summary_text FROM visual_observation_records "
                    "WHERE visual_observation_id = ? AND memory_set_id = ? AND retention_status != 'excluded'",
                    (entry["visual_observation_id"], entry["memory_set_id"]),
                ).fetchone()
                if row is None:
                    continue
                if row["detailed_summary_text"] != entry["source_text"]:
                    raise ValueError("visual observation changed during vector synchronization")
                conn.execute(
                    """
                    INSERT INTO visual_observation_vector_entries
                        (visual_observation_id, memory_set_id, text_hash, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(visual_observation_id) DO UPDATE SET
                        text_hash = excluded.text_hash, updated_at = excluded.updated_at
                    """,
                    (entry["visual_observation_id"], entry["memory_set_id"], entry["text_hash"], entry["updated_at"]),
                )
                metadata = conn.execute(
                    "SELECT vector_entry_id FROM visual_observation_vector_entries WHERE visual_observation_id = ?",
                    (entry["visual_observation_id"],),
                ).fetchone()
                vector_id = metadata["vector_entry_id"]
                conn.execute("DELETE FROM visual_observation_vec WHERE id = ?", (vector_id,))
                conn.execute(
                    "INSERT INTO visual_observation_vec(id, embedding) VALUES (?, ?)",
                    (vector_id, sqlite_vec.serialize_float32(entry["embedding"])),
                )

    def search_visual_observation_vectors(
        self, *, memory_set_id: str, cycle_id: str, query_embedding: list[float],
        embedding_dimension: int, limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self._memory_db() as conn:
            self._ensure_vector_table(
                conn, table_name="visual_observation_vec", embedding_dimension=embedding_dimension,
            )
            # ID 条件を近傍検索内へ渡し、他の集合や除外記録で上位枠を消費しない。
            rows = conn.execute(
                """
                SELECT r.payload_json, v.distance
                FROM visual_observation_vec AS v
                JOIN visual_observation_vector_entries AS m ON m.vector_entry_id = v.id
                JOIN visual_observation_records AS r ON r.visual_observation_id = m.visual_observation_id
                WHERE v.embedding MATCH ? AND k = ?
                  AND v.id IN (
                    SELECT meta.vector_entry_id
                    FROM visual_observation_vector_entries AS meta
                    JOIN visual_observation_records AS record
                      ON record.visual_observation_id = meta.visual_observation_id
                     AND record.memory_set_id = meta.memory_set_id
                    WHERE record.memory_set_id = ? AND record.cycle_id != ?
                      AND record.retention_status != 'excluded'
                  )
                ORDER BY v.distance
                """,
                (sqlite_vec.serialize_float32(query_embedding), limit, memory_set_id, cycle_id),
            ).fetchall()
        return [{"record": json.loads(row["payload_json"]), "distance": float(row["distance"])} for row in rows]

    def _delete_visual_observation_vectors(self, conn: sqlite3.Connection, memory_set_id: str) -> None:
        if self._table_exists(conn, "visual_observation_vec"):
            conn.execute(
                "DELETE FROM visual_observation_vec WHERE id IN "
                "(SELECT vector_entry_id FROM visual_observation_vector_entries WHERE memory_set_id = ?)",
                (memory_set_id,),
            )
        conn.execute("DELETE FROM visual_observation_vector_entries WHERE memory_set_id = ?", (memory_set_id,))
