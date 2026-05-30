from __future__ import annotations

import json
import sqlite3
from typing import Any


class StoreActivityMixin:
    def refresh_activity_state(
        self,
        *,
        memory_set_id: str,
        current_time: str,
        activity_state: dict[str, Any] | None,
        expired_activity_id: str | None = None,
    ) -> dict[str, Any]:
        # トランザクション
        with self._memory_db() as conn:
            expired_count = self._expire_activity_states(
                conn,
                memory_set_id=memory_set_id,
                current_time=current_time,
            )
            if isinstance(expired_activity_id, str) and expired_activity_id.strip():
                expired_count += self._expire_activity_state_by_id(
                    conn,
                    memory_set_id=memory_set_id,
                    activity_id=expired_activity_id.strip(),
                    current_time=current_time,
                )
            updated_count = 0
            if isinstance(activity_state, dict):
                self._insert_activity_state(conn, activity_state)
                updated_count = 1
        return {
            "updated_count": updated_count,
            "expired_count": expired_count,
        }

    def get_current_activity_state(
        self,
        *,
        memory_set_id: str,
        current_time: str,
    ) -> dict[str, Any] | None:
        # 期限切れを先に整理する。
        with self._memory_db() as conn:
            self._expire_activity_states(
                conn,
                memory_set_id=memory_set_id,
                current_time=current_time,
            )
            row = conn.execute(
                """
                SELECT payload_json
                FROM activity_states
                WHERE memory_set_id = ?
                  AND status IN ('active', 'recently_active', 'unknown')
                  AND expires_at > ?
                ORDER BY salience DESC, updated_at DESC, rowid DESC
                LIMIT 1
                """,
                (memory_set_id, current_time),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload_json"])

    def _insert_activity_state(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # 同じ記憶集合の current activity は 1 件に絞る。
        conn.execute(
            """
            UPDATE activity_states
            SET status = 'ended',
                expires_at = ?,
                payload_json = json_set(payload_json, '$.status', 'ended', '$.expires_at', ?)
            WHERE memory_set_id = ?
              AND activity_id != ?
              AND status IN ('active', 'recently_active', 'unknown')
            """,
            (
                record["updated_at"],
                record["updated_at"],
                record["memory_set_id"],
                record["activity_id"],
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO activity_states (
                activity_id,
                memory_set_id,
                activity_label,
                status,
                confidence,
                salience,
                started_at,
                updated_at,
                expires_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["activity_id"],
                record["memory_set_id"],
                record["label"],
                record["status"],
                float(record["confidence"]),
                float(record["salience"]),
                record["started_at"],
                record["updated_at"],
                record["expires_at"],
                self._to_json(record),
            ),
        )

    def _expire_activity_states(
        self,
        conn: sqlite3.Connection,
        *,
        memory_set_id: str,
        current_time: str,
    ) -> int:
        cursor = conn.execute(
            """
            UPDATE activity_states
            SET status = 'ended',
                payload_json = json_set(payload_json, '$.status', 'ended')
            WHERE memory_set_id = ?
              AND status IN ('active', 'recently_active', 'unknown')
              AND expires_at <= ?
            """,
            (memory_set_id, current_time),
        )
        return int(cursor.rowcount or 0)

    def _expire_activity_state_by_id(
        self,
        conn: sqlite3.Connection,
        *,
        memory_set_id: str,
        activity_id: str,
        current_time: str,
    ) -> int:
        cursor = conn.execute(
            """
            UPDATE activity_states
            SET status = 'ended',
                expires_at = ?,
                payload_json = json_set(payload_json, '$.status', 'ended', '$.expires_at', ?)
            WHERE memory_set_id = ?
              AND activity_id = ?
              AND status IN ('active', 'recently_active', 'unknown')
            """,
            (current_time, current_time, memory_set_id, activity_id),
        )
        return int(cursor.rowcount or 0)
