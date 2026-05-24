from __future__ import annotations

import sqlite3
from typing import Any


class StoreCycleMixin:
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
