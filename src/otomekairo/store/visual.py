from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any


class StoreVisualMixin:
    def ensure_visual_observation_search_index(self, *, memory_set_id: str | None = None) -> None:
        # 派生検索 index が欠けている視覚記録だけ補完する。
        clauses = ["i.visual_observation_id IS NULL"]
        params: list[Any] = []
        if isinstance(memory_set_id, str) and memory_set_id.strip():
            clauses.append("r.memory_set_id = ?")
            params.append(memory_set_id.strip())

        with self._memory_db() as conn:
            rows = conn.execute(
                f"""
                SELECT r.payload_json
                FROM visual_observation_records AS r
                LEFT JOIN visual_observation_search_index AS i
                  ON i.visual_observation_id = r.visual_observation_id
                WHERE {' AND '.join(clauses)}
                """,
                params,
            ).fetchall()
            for row in rows:
                self._upsert_visual_observation_search_index(conn, json.loads(row["payload_json"]))

    def rebuild_visual_observation_search_index(self, *, memory_set_id: str | None = None) -> None:
        # 派生検索 index は正本ではないため、視覚記録から再構築する。
        clauses: list[str] = []
        params: list[Any] = []
        if isinstance(memory_set_id, str) and memory_set_id.strip():
            clauses.append("memory_set_id = ?")
            params.append(memory_set_id.strip())

        with self._memory_db() as conn:
            if clauses:
                conn.execute(
                    "DELETE FROM visual_observation_search_index WHERE memory_set_id = ?",
                    (memory_set_id.strip(),),
                )
            else:
                conn.execute("DELETE FROM visual_observation_search_index")

            query = "SELECT payload_json FROM visual_observation_records"
            if clauses:
                query += f" WHERE {' AND '.join(clauses)}"
            rows = conn.execute(query, params).fetchall()
            for row in rows:
                self._upsert_visual_observation_search_index(conn, json.loads(row["payload_json"]))

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

        normalized_query = query_text.strip() if isinstance(query_text, str) else ""
        if normalized_query:
            # 日本語入力は空白で分かち書きされないため、空白語と短い n-gram の OR 検索にする。
            return self._list_visual_observation_records_by_query(
                memory_set_id=memory_set_id,
                query_text=normalized_query,
                limit=limit,
            )

        params: list[Any] = [memory_set_id, "excluded", limit]

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM visual_observation_records
                WHERE memory_set_id = ?
                  AND retention_status != ?
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

    def list_important_visual_observation_records(
        self,
        *,
        memory_set_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        # 入力検証
        if limit <= 0:
            return []

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(
                """
                SELECT r.payload_json
                FROM visual_observation_search_index AS i
                JOIN visual_observation_records AS r
                  ON r.visual_observation_id = i.visual_observation_id
                WHERE i.memory_set_id = ?
                  AND r.retention_status != ?
                ORDER BY
                    CAST(i.importance_score AS REAL) DESC,
                    CASE r.retention_status
                        WHEN 'active' THEN 0
                        WHEN 'compressed' THEN 1
                        ELSE 2
                    END ASC,
                    r.observed_at DESC,
                    r.rowid DESC
                LIMIT ?
                """,
                (memory_set_id, "excluded", limit),
            ).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def list_recent_visual_observation_records(
        self,
        *,
        memory_set_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        # 入力検証
        if limit <= 0:
            return []

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM visual_observation_records
                WHERE memory_set_id = ?
                  AND retention_status != ?
                ORDER BY observed_at DESC, rowid DESC
                LIMIT ?
                """,
                (memory_set_id, "excluded", limit),
            ).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def _list_visual_observation_records_by_query(
        self,
        *,
        memory_set_id: str,
        query_text: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        # 検索語
        query_terms = self._visual_observation_query_terms(query_text)
        if not query_terms:
            return []
        match_query = self._visual_observation_fts_query(query_terms)
        candidate_limit = max(limit * 12, 48)

        # FTS候補
        with self._memory_db() as conn:
            rows = conn.execute(
                """
                SELECT r.payload_json
                FROM visual_observation_search_index
                JOIN visual_observation_records AS r
                  ON r.visual_observation_id = visual_observation_search_index.visual_observation_id
                WHERE visual_observation_search_index MATCH ?
                  AND visual_observation_search_index.memory_set_id = ?
                  AND r.retention_status != ?
                ORDER BY rank, r.observed_at DESC, r.rowid DESC
                LIMIT ?
                """,
                (match_query, memory_set_id, "excluded", candidate_limit),
            ).fetchall()

        records = [json.loads(row["payload_json"]) for row in rows]
        records.sort(
            key=lambda record: self._visual_observation_query_sort_key(
                record=record,
                query_text=query_text,
                query_terms=query_terms,
            )
        )
        return records[:limit]

    def _visual_observation_query_sort_key(
        self,
        *,
        record: dict[str, Any],
        query_text: str,
        query_terms: list[str],
    ) -> tuple[float, float, int]:
        # query 一致を retention_status より前に置く。
        match_score = self._visual_observation_match_score(
            record=record,
            query_text=query_text,
            query_terms=query_terms,
        )
        importance_score = record.get("importance_score")
        importance = float(importance_score) if isinstance(importance_score, (int, float)) else 0.0
        retention_bonus = 1 if record.get("retention_status") == "active" else 0
        return (-match_score, -importance, -retention_bonus)

    def _visual_observation_match_score(
        self,
        *,
        record: dict[str, Any],
        query_text: str,
        query_terms: list[str],
    ) -> float:
        # 詳細説明や派生ラベルの一致度を単純な点数にする。
        search_text = self._visual_observation_plain_search_text(record).lower()
        compact_query = self._visual_observation_compact_text(query_text).lower()
        score = 0.0
        if compact_query and compact_query in search_text:
            score += 16.0
        for term in query_terms:
            normalized = term.lower().strip()
            if not normalized:
                continue
            if normalized in search_text:
                score += min(float(len(normalized)), 8.0)
        if record.get("image_input_kind") == "conversation_attachment":
            score += 1.0
        return score

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
        return terms[:40]

    def _visual_observation_fts_query(self, query_terms: list[str]) -> str:
        # FTS構文の制御文字を避けるため、全 term を phrase として渡す。
        phrases = []
        for term in query_terms:
            escaped = term.replace('"', '""').strip()
            if escaped:
                phrases.append(f'"{escaped}"')
        return " OR ".join(phrases)

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
        self._upsert_visual_observation_search_index(conn, record)

    def _upsert_visual_observation_search_index(
        self,
        conn: sqlite3.Connection,
        record: dict[str, Any],
    ) -> None:
        # 派生 index 更新
        conn.execute(
            "DELETE FROM visual_observation_search_index WHERE visual_observation_id = ?",
            (record["visual_observation_id"],),
        )
        conn.execute(
            """
            INSERT INTO visual_observation_search_index (
                visual_observation_id,
                memory_set_id,
                observed_at,
                retention_status,
                importance_score,
                source_kind,
                source_label,
                search_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["visual_observation_id"],
                record["memory_set_id"],
                record["observed_at"],
                record["retention_status"],
                str(record.get("importance_score", 0.0)),
                record["source_kind"],
                record.get("source_label") or "",
                self._visual_observation_search_document(record),
            ),
        )

    def _visual_observation_search_document(self, record: dict[str, Any]) -> str:
        # FTS 用に詳細説明、派生ラベル、短い n-gram を同じ document に入れる。
        plain_text = self._visual_observation_plain_search_text(record)
        ngrams = self._visual_observation_search_ngrams(plain_text)
        return " ".join([plain_text, *ngrams]).strip()

    def _visual_observation_plain_search_text(self, record: dict[str, Any]) -> str:
        # 検索対象テキスト
        parts: list[str] = []
        for key in (
            "detailed_summary_text",
            "source_kind",
            "source_label",
            "vision_source_id",
            "image_input_kind",
            "confidence_hint",
        ):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())

        for key in ("scene_entities", "activity_labels", "environment_labels"):
            parts.extend(self._visual_observation_string_list(record.get(key)))

        index = record.get("index")
        if isinstance(index, dict):
            for key in ("short_summary_text", "embedding_text"):
                value = index.get(key)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
            parts.extend(self._visual_observation_string_list(index.get("searchable_terms")))

        client_context_summary = record.get("client_context_summary")
        if isinstance(client_context_summary, dict):
            for value in client_context_summary.values():
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())

        return " ".join(parts)

    def _visual_observation_string_list(self, value: Any) -> list[str]:
        # 文字列配列だけ取り出す。
        if not isinstance(value, list):
            return []
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    def _visual_observation_search_ngrams(self, text: str) -> list[str]:
        # 日本語の部分一致に使う短い n-gram。
        compact = self._visual_observation_compact_text(text)
        grams: list[str] = []
        seen: set[str] = set()
        for size in range(2, min(6, len(compact)) + 1):
            for index in range(0, len(compact) - size + 1):
                gram = compact[index : index + size]
                if gram in seen:
                    continue
                grams.append(gram)
                seen.add(gram)
        return grams

    def _visual_observation_compact_text(self, text: str) -> str:
        # 空白と主要な句読点を落とした検索用文字列。
        ignored = set(" \t\r\n。、，．！？!?「」『』（）()[]{}<>＜＞:：;；,，.．/／\\|｜'\"`~〜")
        return "".join(char for char in text if char not in ignored)
