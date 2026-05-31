from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any


class StoreVisualMixin:
    def upsert_visual_observation_records(self, *, records: list[dict[str, Any]]) -> None:
        # 空
        if not records:
            return

        # トランザクション
        with self._memory_db() as conn:
            for record in records:
                self._insert_visual_observation_record(conn, record)

    def upsert_daily_visual_digest(
        self,
        *,
        digest: dict[str, Any],
        updated_records: list[dict[str, Any]],
    ) -> None:
        # トランザクション
        with self._memory_db() as conn:
            for record in updated_records:
                self._insert_visual_observation_record(conn, record)
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_visual_digests (
                    digest_id,
                    memory_set_id,
                    local_date,
                    started_at,
                    finished_at,
                    result_status,
                    record_count,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest["digest_id"],
                    digest["memory_set_id"],
                    digest["local_date"],
                    digest["started_at"],
                    digest["finished_at"],
                    digest["result_status"],
                    int(digest["record_count"]),
                    self._to_json(digest),
                ),
            )

    def get_daily_visual_digest(self, *, memory_set_id: str, local_date: str) -> dict[str, Any] | None:
        # クエリ
        with self._memory_db() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM daily_visual_digests
                WHERE memory_set_id = ? AND local_date = ?
                """,
                (memory_set_id, local_date),
            ).fetchone()

        # 結果
        if row is None:
            return None
        return json.loads(row["payload_json"])

    def list_daily_visual_digests(
        self,
        *,
        memory_set_id: str,
        query_text: str | None = None,
        local_date: str | None = None,
        before_local_date: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        # 入力検証
        if limit <= 0:
            return []

        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]
        if isinstance(local_date, str) and local_date.strip():
            clauses.append("local_date = ?")
            params.append(local_date.strip())
        if isinstance(before_local_date, str) and before_local_date.strip():
            clauses.append("local_date < ?")
            params.append(before_local_date.strip())
        normalized_query = query_text.strip() if isinstance(query_text, str) else ""
        if normalized_query:
            query_terms = self._visual_observation_query_terms(normalized_query)
            if query_terms:
                clauses.append("(" + " OR ".join("payload_json LIKE ?" for _ in query_terms) + ")")
                params.extend(f"%{term.strip()}%" for term in query_terms)
        params.append(limit)

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM daily_visual_digests
                WHERE {' AND '.join(clauses)}
                ORDER BY local_date DESC, finished_at DESC, rowid DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def list_visual_observation_local_dates(
        self,
        *,
        memory_set_id: str,
        before_local_date: str,
        limit: int = 14,
    ) -> list[str]:
        # 入力検証
        if limit <= 0:
            return []

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT substr(observed_at, 1, 10) AS local_date
                FROM visual_observation_records
                WHERE memory_set_id = ?
                  AND substr(observed_at, 1, 10) < ?
                ORDER BY local_date ASC
                LIMIT ?
                """,
                (memory_set_id, before_local_date, limit),
            ).fetchall()

        # 結果
        return [str(row["local_date"]) for row in rows]

    def list_visual_observation_records_for_date(
        self,
        *,
        memory_set_id: str,
        local_date: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        # 入力検証
        if limit is not None and limit <= 0:
            return []
        start_date = date.fromisoformat(local_date)
        end_date = (start_date + timedelta(days=1)).isoformat()

        # クエリ
        limit_clause = "LIMIT ?" if limit is not None else ""
        params: list[Any] = [memory_set_id, f"{local_date}T00:00:00", f"{end_date}T00:00:00"]
        if limit is not None:
            params.append(limit)
        with self._memory_db() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM visual_observation_records
                WHERE memory_set_id = ?
                  AND observed_at >= ?
                  AND observed_at < ?
                ORDER BY observed_at ASC, rowid ASC
                {limit_clause}
                """,
                params,
            ).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def list_visual_observation_records(
        self,
        *,
        memory_set_id: str,
        query_text: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        # 入力検証
        if limit <= 0:
            return []

        clauses = ["memory_set_id = ?", "retention_status != ?"]
        params: list[Any] = [memory_set_id]
        params.append("excluded")
        normalized_query = query_text.strip() if isinstance(query_text, str) else ""
        if normalized_query:
            # 日本語入力は空白で分かち書きされないため、空白語と短い n-gram の OR 検索にする。
            query_terms = self._visual_observation_query_terms(normalized_query)
            if query_terms:
                clauses.append("(" + " OR ".join("detailed_summary_text LIKE ?" for _ in query_terms) + ")")
                params.extend(f"%{term.strip()}%" for term in query_terms)
            else:
                clauses.append("detailed_summary_text LIKE ?")
                params.append(f"%{normalized_query}%")
        params.append(limit)

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM visual_observation_records
                WHERE {' AND '.join(clauses)}
                ORDER BY
                    CASE retention_status
                        WHEN 'active' THEN 0
                        WHEN 'compressed' THEN 1
                        ELSE 2
                    END ASC,
                    observed_at DESC,
                    rowid DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def _visual_observation_query_terms(self, query_text: str) -> list[str]:
        # 空白語
        terms: list[str] = []
        seen: set[str] = set()
        normalized = query_text.replace("　", " ").strip()
        for term in normalized.split():
            cleaned = term.strip(" \t\r\n。、，．！？!?「」『』（）()[]{}")
            if len(cleaned) < 2 or cleaned in seen:
                continue
            terms.append(cleaned)
            seen.add(cleaned)

        # n-gram
        compact = "".join(char for char in normalized if not char.isspace())
        compact = compact.strip("。、，．！？!?「」『』（）()[]{}")
        if compact and len(terms) <= 1:
            grams: list[str] = []
            for size in range(min(6, len(compact)), 1, -1):
                for index in range(0, len(compact) - size + 1):
                    gram = compact[index : index + size]
                    if gram in seen:
                        continue
                    grams.append(gram)
                    seen.add(gram)
            terms.extend(grams[:16])

        # 結果
        return terms[:20]

    def _insert_visual_observation_record(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # 追加
        conn.execute(
            """
            INSERT OR REPLACE INTO visual_observation_records (
                visual_observation_id,
                memory_set_id,
                cycle_id,
                observed_at,
                source_kind,
                source_label,
                vision_source_id,
                image_input_kind,
                confidence_hint,
                retention_status,
                detailed_summary_text,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["visual_observation_id"],
                record["memory_set_id"],
                record["cycle_id"],
                record["observed_at"],
                record["source_kind"],
                record.get("source_label"),
                record.get("vision_source_id"),
                record["image_input_kind"],
                record.get("confidence_hint"),
                record["retention_status"],
                record["detailed_summary_text"],
                self._to_json(record),
            ),
        )
