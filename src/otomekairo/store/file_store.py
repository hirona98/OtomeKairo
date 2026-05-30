from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from otomekairo.store.state import StateStore
from otomekairo.store.affect import StoreAffectMixin
from otomekairo.store.activity import StoreActivityMixin
from otomekairo.store.clone import StoreCloneMixin
from otomekairo.store.cycle import StoreCycleMixin
from otomekairo.store.memory_links import StoreMemoryLinksMixin
from otomekairo.store.schema import MEMORY_DB_FILE_NAME, StoreSchemaMixin
from otomekairo.store.vector import StoreVectorMixin


# 保存
class SQLiteMemoryStore(
    StoreCycleMixin,
    StoreMemoryLinksMixin,
    StoreAffectMixin,
    StoreActivityMixin,
    StoreCloneMixin,
    StoreVectorMixin,
    StoreSchemaMixin,
):
    def __init__(self, root_dir: Path) -> None:
        # パス群
        self.root_dir = root_dir
        self.memory_db_path = root_dir / MEMORY_DB_FILE_NAME

        # 初期化
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._initialize_memory_db()

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

    def list_recent_memory_revision_targets_for_correction(
        self,
        *,
        memory_set_id: str,
        before_finished_at: str,
        exclude_cycle_id: str,
        cycle_limit: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        # 入力検証
        if cycle_limit <= 0 or limit <= 0:
            return []

        # トランザクション
        with self._memory_db() as conn:
            cycle_rows = conn.execute(
                """
                SELECT cycle_id, trigger_kind, started_at, finished_at
                FROM cycle_summaries
                WHERE selected_memory_set_id = ?
                  AND cycle_id != ?
                  AND failed = 0
                  AND finished_at <= ?
                ORDER BY started_at DESC, rowid DESC
                LIMIT ?
                """,
                (memory_set_id, exclude_cycle_id, before_finished_at, cycle_limit),
            ).fetchall()
            if not cycle_rows:
                return []

            cycle_payloads = [
                {
                    "cycle_id": row["cycle_id"],
                    "trigger_kind": row["trigger_kind"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                }
                for row in cycle_rows
            ]
            cycle_by_id = {cycle["cycle_id"]: cycle for cycle in cycle_payloads}
            recent_cycle_ids = list(cycle_by_id)
            placeholders = ", ".join("?" for _ in recent_cycle_ids)

            event_rows = conn.execute(
                f"""
                SELECT event_id, cycle_id
                FROM events
                WHERE memory_set_id = ?
                  AND cycle_id IN ({placeholders})
                """,
                (memory_set_id, *recent_cycle_ids),
            ).fetchall()
            event_cycle_ids = {
                row["event_id"]: row["cycle_id"]
                for row in event_rows
            }

            revision_rows = conn.execute(
                """
                SELECT
                    rev.payload_json AS revision_payload_json,
                    unit.payload_json AS unit_payload_json,
                    rev.operation AS operation,
                    rev.occurred_at AS occurred_at
                FROM revisions AS rev
                JOIN memory_units AS unit
                  ON unit.memory_set_id = rev.memory_set_id
                 AND unit.memory_unit_id = rev.memory_unit_id
                WHERE rev.memory_set_id = ?
                  AND rev.operation IN (
                      'create',
                      'reinforce',
                      'refine',
                      'supersede',
                      'revoke',
                      'dormant'
                  )
                  AND rev.occurred_at < ?
                ORDER BY rev.occurred_at DESC, rev.rowid DESC
                LIMIT ?
                """,
                (memory_set_id, before_finished_at, max(limit * 8, 32)),
            ).fetchall()

            # 最新revisionだけを候補にする。
            targets: list[dict[str, Any]] = []
            seen_memory_unit_ids: set[str] = set()
            for row in revision_rows:
                revision = json.loads(row["revision_payload_json"])
                unit = json.loads(row["unit_payload_json"])
                memory_unit_id = revision.get("memory_unit_id")
                if not isinstance(memory_unit_id, str) or not memory_unit_id:
                    continue
                if memory_unit_id in seen_memory_unit_ids:
                    continue
                seen_memory_unit_ids.add(memory_unit_id)

                source_cycle_ids = self._revision_source_cycle_ids(
                    revision=revision,
                    unit=unit,
                    event_cycle_ids=event_cycle_ids,
                    cycle_by_id=cycle_by_id,
                )
                if not source_cycle_ids:
                    continue

                related_memory_units = self._load_memory_units_by_id(
                    conn,
                    memory_set_id=memory_set_id,
                    memory_unit_ids=revision.get("related_memory_unit_ids", []),
                )
                targets.append(
                    {
                        "memory_unit": unit,
                        "revision": revision,
                        "operation": row["operation"],
                        "occurred_at": row["occurred_at"],
                        "source_cycle_ids": source_cycle_ids,
                        "source_cycles": [
                            cycle_by_id[cycle_id]
                            for cycle_id in source_cycle_ids
                            if cycle_id in cycle_by_id
                        ],
                        "related_memory_units": related_memory_units,
                    }
                )
                if len(targets) >= limit:
                    break

        # 結果
        return targets

    def _revision_source_cycle_ids(
        self,
        *,
        revision: dict[str, Any],
        unit: dict[str, Any],
        event_cycle_ids: dict[str, str],
        cycle_by_id: dict[str, dict[str, Any]],
    ) -> list[str]:
        # revision の根拠 event から cycle を復元する。
        source_cycle_ids: list[str] = []
        for event_id in revision.get("evidence_event_ids", []):
            if not isinstance(event_id, str):
                continue
            cycle_id = event_cycle_ids.get(event_id)
            if cycle_id in cycle_by_id and cycle_id not in source_cycle_ids:
                source_cycle_ids.append(cycle_id)

        # 古い action 互換として memory_unit 側の cycle ids も見る。
        if not source_cycle_ids:
            for cycle_id in unit.get("evidence_cycle_ids", []):
                if isinstance(cycle_id, str) and cycle_id in cycle_by_id and cycle_id not in source_cycle_ids:
                    source_cycle_ids.append(cycle_id)

        # 結果
        return source_cycle_ids

    def _load_memory_units_by_id(
        self,
        conn: sqlite3.Connection,
        *,
        memory_set_id: str,
        memory_unit_ids: list[Any],
    ) -> list[dict[str, Any]]:
        # 正規化
        normalized_ids: list[str] = []
        for value in memory_unit_ids:
            if isinstance(value, str) and value and value not in normalized_ids:
                normalized_ids.append(value)
        if not normalized_ids:
            return []

        # クエリ
        placeholders = ", ".join("?" for _ in normalized_ids)
        rows = conn.execute(
            f"""
            SELECT payload_json
            FROM memory_units
            WHERE memory_set_id = ?
              AND memory_unit_id IN ({placeholders})
            """,
            (memory_set_id, *normalized_ids),
        ).fetchall()
        units_by_id = {
            payload["memory_unit_id"]: payload
            for payload in (json.loads(row["payload_json"]) for row in rows)
            if isinstance(payload.get("memory_unit_id"), str)
        }
        return [
            units_by_id[memory_unit_id]
            for memory_unit_id in normalized_ids
            if memory_unit_id in units_by_id
        ]

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

    def list_boundary_events_for_evidence(
        self,
        *,
        memory_set_id: str,
        target_actor: str,
        boundary: str,
        before_iso: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        # 空
        if limit <= 0:
            return []

        # Query部品群
        clauses = ["memory_set_id = ?", "text IS NOT NULL", "created_at < ?"]
        params: list[Any] = [memory_set_id, before_iso]
        self._append_in_clause(clauses, params, "kind", self._event_kinds_for_actor(target_actor))
        roles = self._event_roles_for_actor(target_actor)
        if roles:
            self._append_in_clause(clauses, params, "role", roles)
        direction = "ASC" if boundary == "first" else "DESC"

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(
                f"""
                SELECT event_id, cycle_id, memory_set_id, kind, role, text, created_at, payload_json
                FROM events
                WHERE {" AND ".join(clauses)}
                ORDER BY created_at {direction}, rowid {direction}
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()

        # 結果
        return [dict(row) for row in rows]

    def search_text_events_for_evidence(
        self,
        *,
        memory_set_id: str,
        target_actor: str,
        query_terms: list[str],
        before_iso: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        # 空
        if limit <= 0:
            return []

        # Query部品群
        clauses = ["memory_set_id = ?", "text IS NOT NULL", "created_at < ?"]
        params: list[Any] = [memory_set_id, before_iso]
        self._append_in_clause(clauses, params, "kind", self._event_kinds_for_actor(target_actor))
        roles = self._event_roles_for_actor(target_actor)
        if roles:
            self._append_in_clause(clauses, params, "role", roles)
        for term in query_terms:
            if not isinstance(term, str) or not term.strip():
                continue
            clauses.append("text LIKE ?")
            params.append(f"%{term.strip()}%")

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(
                f"""
                SELECT event_id, cycle_id, memory_set_id, kind, role, text, created_at, payload_json
                FROM events
                WHERE {" AND ".join(clauses)}
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()

        # 結果
        return [dict(row) for row in rows]

    def list_cycle_events_for_evidence(
        self,
        *,
        memory_set_id: str,
        cycle_id: str,
        target_actor: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        # 空
        if limit <= 0:
            return []

        # Query部品群
        clauses = ["memory_set_id = ?", "cycle_id = ?", "text IS NOT NULL"]
        params: list[Any] = [memory_set_id, cycle_id]
        self._append_in_clause(clauses, params, "kind", self._event_kinds_for_actor(target_actor))
        roles = self._event_roles_for_actor(target_actor)
        if roles:
            self._append_in_clause(clauses, params, "role", roles)

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(
                f"""
                SELECT event_id, cycle_id, memory_set_id, kind, role, text, created_at, payload_json
                FROM events
                WHERE {" AND ".join(clauses)}
                ORDER BY created_at ASC, rowid ASC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()

        # 結果
        return [dict(row) for row in rows]

    def _append_in_clause(
        self,
        clauses: list[str],
        params: list[Any],
        column_name: str,
        values: tuple[str, ...],
    ) -> None:
        placeholders = ", ".join("?" for _ in values)
        clauses.append(f"{column_name} IN ({placeholders})")
        params.extend(values)

    def _event_kinds_for_actor(self, target_actor: str) -> tuple[str, ...]:
        if target_actor == "assistant":
            return ("reply",)
        if target_actor == "user":
            return ("conversation_input", "observation")
        return ("conversation_input", "observation", "reply")

    def _event_roles_for_actor(self, target_actor: str) -> tuple[str, ...]:
        if target_actor == "assistant":
            return ("assistant",)
        if target_actor == "user":
            return ("user",)
        return ()

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
