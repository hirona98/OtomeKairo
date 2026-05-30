from __future__ import annotations

import json
import sqlite3
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

        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]
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
                ORDER BY observed_at DESC, rowid DESC
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
