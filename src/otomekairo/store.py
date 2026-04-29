from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from otomekairo.memory_utils import parse_iso
from otomekairo.state_store import StateStore
from otomekairo.store_clone import StoreCloneMixin
from otomekairo.store_schema import MEMORY_DB_FILE_NAME, StoreSchemaMixin
from otomekairo.store_vector import StoreVectorMixin


MOOD_BASELINE_HALFLIFE_SECONDS = 86400.0
MOOD_RESIDUAL_HALFLIFE_SECONDS = 21600.0
MOOD_RESIDUAL_ALPHA = 0.75


def _zero_vad() -> dict[str, float]:
    # 既定値
    return {"v": 0.0, "a": 0.0, "d": 0.0}


def _clamp01(value: Any) -> float:
    # 正規化
    if not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, min(float(value), 1.0))


def _clamp_vad_axis(value: Any) -> float:
    # 正規化
    if not isinstance(value, (int, float)):
        return 0.0
    return max(-1.0, min(float(value), 1.0))


def _clamp_vad(value: Any) -> dict[str, float]:
    # 形状
    if not isinstance(value, dict):
        return _zero_vad()

    # 結果
    return {
        "v": _clamp_vad_axis(value.get("v")),
        "a": _clamp_vad_axis(value.get("a")),
        "d": _clamp_vad_axis(value.get("d")),
    }


def _vad_add(left: dict[str, float], right: dict[str, float]) -> dict[str, float]:
    # 軸加算
    return _clamp_vad(
        {
            "v": left["v"] + right["v"],
            "a": left["a"] + right["a"],
            "d": left["d"] + right["d"],
        }
    )


def _vad_sub(left: dict[str, float], right: dict[str, float]) -> dict[str, float]:
    # 軸減算
    return _clamp_vad(
        {
            "v": left["v"] - right["v"],
            "a": left["a"] - right["a"],
            "d": left["d"] - right["d"],
        }
    )


def _vad_scale(vad: dict[str, float], scale: float) -> dict[str, float]:
    # 係数
    return _clamp_vad(
        {
            "v": vad["v"] * scale,
            "a": vad["a"] * scale,
            "d": vad["d"] * scale,
        }
    )


def _vad_lerp(cur: dict[str, float], tgt: dict[str, float], alpha: float) -> dict[str, float]:
    # 線形補間
    return _clamp_vad(
        {
            "v": cur["v"] + alpha * (tgt["v"] - cur["v"]),
            "a": cur["a"] + alpha * (tgt["a"] - cur["a"]),
            "d": cur["d"] + alpha * (tgt["d"] - cur["d"]),
        }
    )


def _vad_decay(vad: dict[str, float], dt_seconds: float, half_life_seconds: float) -> dict[str, float]:
    # 半減期減衰
    if half_life_seconds <= 0:
        return _zero_vad()
    scale = 0.5 ** (max(0.0, dt_seconds) / half_life_seconds)
    return _vad_scale(vad, scale)


# 保存
class SQLiteMemoryStore(StoreCloneMixin, StoreVectorMixin, StoreSchemaMixin):
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
        episode_affects: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # トランザクション
        with self._memory_db() as conn:
            # episode追加
            if episode is not None:
                self._insert_episode(conn, episode)

            # 記憶アクション群
            for action in memory_actions:
                self._apply_memory_action(conn, action)

            # episode affect群
            for episode_affect in episode_affects:
                self._insert_episode_affect(conn, episode_affect)

            # mood更新
            mood_state_update = self._update_mood_state_from_episode_affects(
                conn,
                episode_affects=episode_affects,
                write_time=episode["formed_at"] if episode is not None else None,
            )

        # 結果
        return {
            "mood_state_update": mood_state_update,
            "affect_state_updates": [],
        }

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

    def upsert_memory_postprocess_job(self, *, job: dict[str, Any]) -> None:
        # トランザクション
        with self._memory_db() as conn:
            self._insert_memory_postprocess_job(conn, job)

    def list_memory_postprocess_jobs(
        self,
        *,
        result_statuses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        # Query部品群
        clauses: list[str] = []
        params: list[Any] = []
        if result_statuses:
            placeholders = ", ".join("?" for _ in result_statuses)
            clauses.append(f"result_status IN ({placeholders})")
            params.extend(result_statuses)

        query = """
            SELECT payload_json
            FROM memory_postprocess_jobs
        """
        if clauses:
            query += f"\nWHERE {' AND '.join(clauses)}"
        query += "\nORDER BY queued_at ASC, rowid ASC"

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(query, params).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def count_memory_postprocess_jobs(
        self,
        *,
        result_statuses: list[str] | None = None,
    ) -> int:
        # Query部品群
        clauses: list[str] = []
        params: list[Any] = []
        if result_statuses:
            placeholders = ", ".join("?" for _ in result_statuses)
            clauses.append(f"result_status IN ({placeholders})")
            params.extend(result_statuses)

        query = """
            SELECT COUNT(*)
            FROM memory_postprocess_jobs
        """
        if clauses:
            query += f"\nWHERE {' AND '.join(clauses)}"

        # クエリ
        with self._memory_db() as conn:
            row = conn.execute(query, params).fetchone()

        # 結果
        return int(row[0]) if row is not None else 0

    def get_memory_postprocess_job(self, cycle_id: str) -> dict[str, Any] | None:
        # クエリ
        with self._memory_db() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM memory_postprocess_jobs
                WHERE cycle_id = ?
                """,
                (cycle_id,),
            ).fetchone()

        # 結果
        if row is None:
            return None
        return json.loads(row["payload_json"])

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

    def list_drive_states(
        self,
        *,
        memory_set_id: str,
        current_time: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM drive_states
                WHERE memory_set_id = ?
                  AND expires_at > ?
                ORDER BY salience DESC, updated_at DESC, rowid DESC
                LIMIT ?
                """,
                (memory_set_id, current_time, limit),
            ).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def replace_drive_states(
        self,
        *,
        memory_set_id: str,
        drive_states: list[dict[str, Any]],
    ) -> None:
        # トランザクション
        with self._memory_db() as conn:
            conn.execute("DELETE FROM drive_states WHERE memory_set_id = ?", (memory_set_id,))
            for drive_state in drive_states:
                self._insert_drive_state(conn, drive_state)

    def clear_drive_states(self, *, memory_set_id: str) -> None:
        # トランザクション
        with self._memory_db() as conn:
            conn.execute("DELETE FROM drive_states WHERE memory_set_id = ?", (memory_set_id,))

    def get_ongoing_action(
        self,
        *,
        memory_set_id: str,
        current_time: str,
    ) -> dict[str, Any] | None:
        # クエリ
        with self._memory_db() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM ongoing_actions
                WHERE memory_set_id = ?
                  AND expires_at > ?
                ORDER BY updated_at DESC, rowid DESC
                LIMIT 1
                """,
                (memory_set_id, current_time),
            ).fetchone()

        # 結果
        if row is None:
            return None
        return json.loads(row["payload_json"])

    def upsert_ongoing_action(self, *, ongoing_action: dict[str, Any]) -> None:
        # トランザクション
        with self._memory_db() as conn:
            self._insert_ongoing_action(conn, ongoing_action)

    def list_world_states(
        self,
        *,
        memory_set_id: str,
        current_time: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM world_states
                WHERE memory_set_id = ?
                  AND expires_at > ?
                ORDER BY salience DESC, updated_at DESC, rowid DESC
                LIMIT ?
                """,
                (memory_set_id, current_time, limit),
            ).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

    def refresh_world_states(
        self,
        *,
        memory_set_id: str,
        current_time: str,
        world_states: list[dict[str, Any]],
        max_active: int,
    ) -> dict[str, int]:
        # トランザクション
        with self._memory_db() as conn:
            expired_cursor = conn.execute(
                """
                DELETE FROM world_states
                WHERE memory_set_id = ?
                  AND expires_at <= ?
                """,
                (memory_set_id, current_time),
            )
            expired_count = max(int(expired_cursor.rowcount or 0), 0)
            replaced_count = 0
            inserted_count = 0

            for record in world_states:
                replaced_count += self._delete_conflicting_world_states(
                    conn,
                    memory_set_id=memory_set_id,
                    record=record,
                )
                self._insert_world_state(conn, record)
                inserted_count += 1

            dropped_count = 0
            if max_active > 0:
                rows = conn.execute(
                    """
                    SELECT world_state_id
                    FROM world_states
                    WHERE memory_set_id = ?
                      AND expires_at > ?
                    ORDER BY salience DESC, updated_at DESC, rowid DESC
                    LIMIT -1 OFFSET ?
                    """,
                    (memory_set_id, current_time, max_active),
                ).fetchall()
                drop_ids = [row["world_state_id"] for row in rows]
                if drop_ids:
                    placeholders = ", ".join("?" for _ in drop_ids)
                    conn.execute(
                        f"DELETE FROM world_states WHERE world_state_id IN ({placeholders})",
                        drop_ids,
                    )
                    dropped_count = len(drop_ids)

        # 結果
        return {
            "expired_count": expired_count,
            "updated_count": inserted_count,
            "replaced_count": replaced_count,
            "dropped_count": dropped_count,
        }

    def _delete_conflicting_world_states(
        self,
        conn: sqlite3.Connection,
        *,
        memory_set_id: str,
        record: dict[str, Any],
    ) -> int:
        rows = conn.execute(
            """
            SELECT world_state_id, payload_json
            FROM world_states
            WHERE memory_set_id = ?
              AND state_type = ?
            """,
            (memory_set_id, record["state_type"]),
        ).fetchall()
        delete_ids: list[str] = []
        for row in rows:
            existing = json.loads(row["payload_json"])
            if self._world_state_records_conflict(existing=existing, incoming=record):
                delete_ids.append(row["world_state_id"])
        if not delete_ids:
            return 0
        placeholders = ", ".join("?" for _ in delete_ids)
        conn.execute(
            f"DELETE FROM world_states WHERE world_state_id IN ({placeholders})",
            delete_ids,
        )
        return len(delete_ids)

    def _world_state_records_conflict(
        self,
        *,
        existing: dict[str, Any],
        incoming: dict[str, Any],
    ) -> bool:
        existing_key = str(existing.get("integration_key") or "").strip()
        incoming_key = str(incoming.get("integration_key") or "").strip()
        if existing_key and incoming_key:
            return existing_key == incoming_key
        return (
            existing.get("scope_type") == incoming.get("scope_type")
            and existing.get("scope_key") == incoming.get("scope_key")
        )

    def clear_world_states(self, *, memory_set_id: str) -> None:
        # トランザクション
        with self._memory_db() as conn:
            conn.execute("DELETE FROM world_states WHERE memory_set_id = ?", (memory_set_id,))

    def clear_ongoing_action(self, *, memory_set_id: str) -> None:
        # トランザクション
        with self._memory_db() as conn:
            conn.execute("DELETE FROM ongoing_actions WHERE memory_set_id = ?", (memory_set_id,))

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
                  AND kind IN ('conversation_input', 'reply')
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

    def get_mood_state(self, *, memory_set_id: str, current_time: str) -> dict[str, Any]:
        # クエリ
        with self._memory_db() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM mood_state
                WHERE memory_set_id = ?
                """,
                (memory_set_id,),
            ).fetchone()

        # 既定値
        if row is None:
            return {
                "baseline_vad": _zero_vad(),
                "residual_vad": _zero_vad(),
                "current_vad": _zero_vad(),
                "confidence": 0.0,
                "observed_at": None,
                "created_at": None,
                "updated_at": None,
            }

        # 現在値導出
        record = json.loads(row["payload_json"])
        return self._with_current_vad(record, current_time=current_time)

    def list_affect_states_for_context(
        self,
        *,
        memory_set_id: str,
        scope_filters: list[tuple[str, str]] | None = None,
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

    def list_recent_episode_affects_for_context(
        self,
        *,
        memory_set_id: str,
        scope_filters: list[tuple[str, str]] | None = None,
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

        query = f"""
            SELECT payload_json
            FROM episode_affects
            WHERE {" AND ".join(clauses)}
            ORDER BY observed_at DESC, intensity DESC, rowid DESC
            LIMIT ?
        """

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # 結果
        return [json.loads(row["payload_json"]) for row in rows]

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

    def _insert_drive_state(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # 追加
        conn.execute(
            """
            INSERT OR REPLACE INTO drive_states (
                drive_id,
                memory_set_id,
                salience,
                updated_at,
                expires_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record["drive_id"],
                record["memory_set_id"],
                record["salience"],
                record["updated_at"],
                record["expires_at"],
                self._to_json(record),
            ),
        )

    def _insert_ongoing_action(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # 追加
        conn.execute(
            """
            INSERT OR REPLACE INTO ongoing_actions (
                action_id,
                memory_set_id,
                status,
                updated_at,
                expires_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record["action_id"],
                record["memory_set_id"],
                record["status"],
                record["updated_at"],
                record["expires_at"],
                self._to_json(record),
            ),
        )

    def _insert_world_state(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # 追加
        conn.execute(
            """
            INSERT OR REPLACE INTO world_states (
                world_state_id,
                memory_set_id,
                state_type,
                scope_type,
                scope_key,
                salience,
                updated_at,
                expires_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["world_state_id"],
                record["memory_set_id"],
                record["state_type"],
                record["scope_type"],
                record["scope_key"],
                record["salience"],
                record["updated_at"],
                record["expires_at"],
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

    def _insert_memory_postprocess_job(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # 追加
        conn.execute(
            """
            INSERT OR REPLACE INTO memory_postprocess_jobs (
                cycle_id,
                memory_set_id,
                queued_at,
                started_at,
                finished_at,
                result_status,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["cycle_id"],
                record["memory_set_id"],
                record["queued_at"],
                record.get("started_at"),
                record.get("finished_at"),
                record["result_status"],
                self._to_json(record),
            ),
        )

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

    def _insert_episode_affect(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # 追加
        conn.execute(
            """
            INSERT OR REPLACE INTO episode_affects (
                episode_affect_id,
                memory_set_id,
                episode_id,
                target_scope_type,
                target_scope_key,
                affect_label,
                intensity,
                confidence,
                observed_at,
                created_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["episode_affect_id"],
                record["memory_set_id"],
                record["episode_id"],
                record["target_scope_type"],
                record["target_scope_key"],
                record["affect_label"],
                record["intensity"],
                record["confidence"],
                record["observed_at"],
                record["created_at"],
                self._to_json(record),
            ),
        )

    def _update_mood_state_from_episode_affects(
        self,
        conn: sqlite3.Connection,
        *,
        episode_affects: list[dict[str, Any]],
        write_time: str | None,
    ) -> dict[str, Any]:
        # self だけに絞る
        self_affects = [
            affect
            for affect in episode_affects
            if affect.get("target_scope_type") == "self" and affect.get("target_scope_key") == "self"
        ]
        if not self_affects:
            return {
                "updated": False,
                "reason": "no_self_episode_affect",
            }

        # 集約
        weighted_vad = _zero_vad()
        sum_weight = 0.0
        observed_times: list[str] = []
        for affect in self_affects:
            weight = _clamp01(affect.get("intensity")) * _clamp01(affect.get("confidence"))
            if weight <= 0.0:
                continue
            weighted_vad = _vad_add(weighted_vad, _vad_scale(_clamp_vad(affect.get("vad")), weight))
            sum_weight += weight
            if isinstance(affect.get("observed_at"), str) and affect["observed_at"]:
                observed_times.append(affect["observed_at"])

        if sum_weight <= 0.0 or not observed_times:
            return {
                "updated": False,
                "reason": "zero_weight_episode_affect",
            }

        # 現在 row
        existing_row = conn.execute(
            """
            SELECT payload_json
            FROM mood_state
            WHERE memory_set_id = ?
            """,
            (self_affects[0]["memory_set_id"],),
        ).fetchone()

        moment_vad = _vad_scale(weighted_vad, 1.0 / sum_weight)
        moment_strength = _clamp01(sum_weight)
        moment_observed_at = max(observed_times)
        write_timestamp = write_time or moment_observed_at

        if existing_row is None:
            previous = {
                "mood_state_id": f"mood_state:{self_affects[0]['memory_set_id']}",
                "memory_set_id": self_affects[0]["memory_set_id"],
                "baseline_vad": _zero_vad(),
                "residual_vad": _zero_vad(),
                "confidence": 0.0,
                "observed_at": moment_observed_at,
                "created_at": write_timestamp,
                "updated_at": write_timestamp,
            }
        else:
            previous = json.loads(existing_row["payload_json"])

        previous_observed_at = previous.get("observed_at") or moment_observed_at
        dt_seconds = max(0.0, (parse_iso(moment_observed_at) - parse_iso(previous_observed_at)).total_seconds())
        alpha_base = _clamp01((1 - 0.5 ** (dt_seconds / MOOD_BASELINE_HALFLIFE_SECONDS)) * moment_strength)
        baseline_vad_new = _vad_lerp(_clamp_vad(previous.get("baseline_vad")), moment_vad, alpha_base)
        residual_vad_decayed = _vad_decay(
            _clamp_vad(previous.get("residual_vad")),
            dt_seconds,
            MOOD_RESIDUAL_HALFLIFE_SECONDS,
        )
        residual_input = _vad_sub(moment_vad, baseline_vad_new)
        residual_alpha = _clamp01(MOOD_RESIDUAL_ALPHA * moment_strength)
        residual_vad_new = _vad_lerp(residual_vad_decayed, residual_input, residual_alpha)
        current_vad = _vad_add(baseline_vad_new, residual_vad_new)
        payload = {
            "mood_state_id": previous["mood_state_id"],
            "memory_set_id": previous["memory_set_id"],
            "baseline_vad": baseline_vad_new,
            "residual_vad": residual_vad_new,
            "confidence": moment_strength,
            "observed_at": moment_observed_at,
            "created_at": previous["created_at"],
            "updated_at": write_timestamp,
        }
        self._upsert_mood_state(conn, payload)

        return {
            "updated": True,
            "reason": None,
            "confidence": payload["confidence"],
            "baseline_vad": baseline_vad_new,
            "residual_vad": residual_vad_new,
            "current_vad": current_vad,
            "observed_at": payload["observed_at"],
            "updated_at": payload["updated_at"],
        }

    def _upsert_mood_state(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # 保存
        conn.execute(
            """
            INSERT OR REPLACE INTO mood_state (
                mood_state_id,
                memory_set_id,
                confidence,
                observed_at,
                created_at,
                updated_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["mood_state_id"],
                record["memory_set_id"],
                record["confidence"],
                record["observed_at"],
                record["created_at"],
                record["updated_at"],
                self._to_json(record),
            ),
        )

    def _with_current_vad(self, record: dict[str, Any], *, current_time: str) -> dict[str, Any]:
        # 減衰後現在値
        observed_at = record.get("observed_at")
        elapsed_seconds = 0.0
        if isinstance(observed_at, str) and observed_at:
            elapsed_seconds = max(0.0, (parse_iso(current_time) - parse_iso(observed_at)).total_seconds())
        current_vad = _vad_add(
            _clamp_vad(record.get("baseline_vad")),
            _vad_decay(_clamp_vad(record.get("residual_vad")), elapsed_seconds, MOOD_RESIDUAL_HALFLIFE_SECONDS),
        )

        # 結果
        return {
            **record,
            "baseline_vad": _clamp_vad(record.get("baseline_vad")),
            "residual_vad": _clamp_vad(record.get("residual_vad")),
            "current_vad": current_vad,
        }

    def _upsert_affect_state(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # 既存検索
        existing_row = conn.execute(
            """
            SELECT affect_state_id, observed_at, created_at
            FROM affect_state
            WHERE memory_set_id = ?
              AND target_scope_type = ?
              AND target_scope_key = ?
              AND affect_label = ?
            """,
            (
                record["memory_set_id"],
                record["target_scope_type"],
                record["target_scope_key"],
                record["affect_label"],
            ),
        ).fetchone()

        # 識別解決
        affect_state_id = record["affect_state_id"]
        observed_at = record["observed_at"]
        created_at = record["created_at"]
        if existing_row is not None:
            affect_state_id = existing_row["affect_state_id"]
            observed_at = existing_row["observed_at"]
            created_at = existing_row["created_at"]

        payload = {
            **record,
            "affect_state_id": affect_state_id,
            "observed_at": observed_at,
            "created_at": created_at,
        }

        # upsert実行
        conn.execute(
            """
            INSERT OR REPLACE INTO affect_state (
                affect_state_id,
                memory_set_id,
                target_scope_type,
                target_scope_key,
                affect_label,
                intensity,
                confidence,
                observed_at,
                created_at,
                updated_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["affect_state_id"],
                payload["memory_set_id"],
                payload["target_scope_type"],
                payload["target_scope_key"],
                payload["affect_label"],
                payload["intensity"],
                payload["confidence"],
                payload["observed_at"],
                payload["created_at"],
                payload["updated_at"],
                self._to_json(payload),
            ),
        )

    def _to_json(self, payload: Any) -> str:
        # 直列化
        return json.dumps(payload, ensure_ascii=False)

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
