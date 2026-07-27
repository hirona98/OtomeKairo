from __future__ import annotations

import json
import sqlite3
from typing import Any


class StoreActivityMixin:
    def refresh_activity_state(
        self,
        *,
        memory_set_id: str,
        actor_ref: str,
        current_time: str,
        activity_state: dict[str, Any] | None,
        expired_activity_id: str | None = None,
    ) -> dict[str, Any]:
        # トランザクション
        with self._memory_db() as conn:
            expired_count = self._expire_activity_states(
                conn,
                memory_set_id=memory_set_id,
                actor_ref=actor_ref,
                current_time=current_time,
            )
            if isinstance(expired_activity_id, str) and expired_activity_id.strip():
                expired_count += self._expire_activity_state_by_id(
                    conn,
                    memory_set_id=memory_set_id,
                    actor_ref=actor_ref,
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
        actor_ref: str,
        current_time: str,
    ) -> dict[str, Any] | None:
        # 期限切れを先に整理する。
        with self._memory_db() as conn:
            self._expire_activity_states(
                conn,
                memory_set_id=memory_set_id,
                actor_ref=actor_ref,
                current_time=current_time,
            )
            row = conn.execute(
                """
                SELECT payload_json
                FROM activity_states
                WHERE memory_set_id = ?
                  AND actor_ref = ?
                  AND status = 'active'
                  AND expires_at > ?
                ORDER BY salience DESC, updated_at DESC, rowid DESC
                LIMIT 1
                """,
                (memory_set_id, actor_ref, current_time),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload_json"])

    def list_current_activity_states(
        self,
        *,
        memory_set_id: str,
        current_time: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        # inspectionでは人物を選ばず、現在有効な状態を人物参照付きで列挙する。
        with self._memory_db() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM activity_states
                WHERE memory_set_id = ?
                  AND status = 'active'
                  AND expires_at > ?
                ORDER BY salience DESC, updated_at DESC, rowid DESC
                LIMIT ?
                """,
                (memory_set_id, current_time, limit),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def _insert_activity_state(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # 同じ人物の current activity は 1 件に絞る。
        conn.execute(
            """
            UPDATE activity_states
            SET status = 'ended',
                expires_at = ?,
                payload_json = json_set(payload_json, '$.status', 'ended', '$.expires_at', ?)
            WHERE memory_set_id = ?
              AND actor_ref = ?
              AND activity_id != ?
              AND status = 'active'
            """,
            (
                record["updated_at"],
                record["updated_at"],
                record["memory_set_id"],
                record["actor_ref"],
                record["activity_id"],
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO activity_states (
                activity_id,
                memory_set_id,
                actor_ref,
                activity_label,
                status,
                confidence,
                salience,
                started_at,
                updated_at,
                expires_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["activity_id"],
                record["memory_set_id"],
                record["actor_ref"],
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
        actor_ref: str,
        current_time: str,
    ) -> int:
        cursor = conn.execute(
            """
            UPDATE activity_states
            SET status = 'ended',
                payload_json = json_set(payload_json, '$.status', 'ended')
            WHERE memory_set_id = ?
              AND actor_ref = ?
              AND status = 'active'
              AND expires_at <= ?
            """,
            (memory_set_id, actor_ref, current_time),
        )
        return int(cursor.rowcount or 0)

    def _expire_activity_state_by_id(
        self,
        conn: sqlite3.Connection,
        *,
        memory_set_id: str,
        actor_ref: str,
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
              AND actor_ref = ?
              AND activity_id = ?
              AND status = 'active'
            """,
            (current_time, current_time, memory_set_id, actor_ref, activity_id),
        )
        return int(cursor.rowcount or 0)
