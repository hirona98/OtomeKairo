from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any


class StoreCloneMixin:
    def delete_memory_set_records(self, memory_set_id: str) -> None:
        # トランザクション
        with self._memory_db() as conn:
            # ベクトル削除
            self._delete_vector_index_entries(conn, memory_set_id)

            # 削除順序
            conn.execute("DELETE FROM memory_postprocess_jobs WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM revisions WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM episode_affects WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM mood_state WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM affect_state WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM memory_units WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM episodes WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM reflection_runs WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM events WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM retrieval_runs WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM cycle_traces WHERE selected_memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM cycle_summaries WHERE selected_memory_set_id = ?", (memory_set_id,))

    def clone_memory_set_records(
        self,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
    ) -> None:
        # トランザクション
        with self._memory_db() as conn:
            cycle_id_map, event_id_map = self._clone_event_records(
                conn,
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
            )
            episode_id_map = self._clone_episode_records(
                conn,
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
                cycle_id_map=cycle_id_map,
                event_id_map=event_id_map,
            )
            memory_unit_id_map = self._clone_memory_unit_records(
                conn,
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
                event_id_map=event_id_map,
            )
            self._clone_revision_records(
                conn,
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
                event_id_map=event_id_map,
                memory_unit_id_map=memory_unit_id_map,
            )
            self._clone_episode_affect_records(
                conn,
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
                episode_id_map=episode_id_map,
            )
            self._clone_mood_state_records(
                conn,
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
            )
            self._clone_affect_state_records(
                conn,
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
            )
            self._clone_reflection_run_records(
                conn,
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
                episode_id_map=episode_id_map,
                memory_unit_id_map=memory_unit_id_map,
            )
            self._clone_vector_index_entries(
                conn,
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
                episode_id_map=episode_id_map,
                memory_unit_id_map=memory_unit_id_map,
            )

    def _clone_event_records(
        self,
        conn: sqlite3.Connection,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
    ) -> tuple[dict[str, str], dict[str, str]]:
        cycle_id_map: dict[str, str] = {}
        event_id_map: dict[str, str] = {}
        source_events = self._load_payload_rows(conn, "events", source_memory_set_id)
        for record in source_events:
            source_cycle_id = record["cycle_id"]
            cycle_id_map.setdefault(source_cycle_id, f"cycle:{uuid.uuid4().hex}")
            old_event_id = record["event_id"]
            event_id_map[old_event_id] = f"event:{uuid.uuid4().hex}"
            self._insert_event(
                conn,
                {
                    **record,
                    "event_id": event_id_map[old_event_id],
                    "cycle_id": cycle_id_map[source_cycle_id],
                    "memory_set_id": target_memory_set_id,
                },
            )
        return cycle_id_map, event_id_map

    def _clone_episode_records(
        self,
        conn: sqlite3.Connection,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
        cycle_id_map: dict[str, str],
        event_id_map: dict[str, str],
    ) -> dict[str, str]:
        episode_id_map: dict[str, str] = {}
        source_episodes = self._load_payload_rows(conn, "episodes", source_memory_set_id)
        cloned_episodes: list[dict[str, Any]] = []
        for record in source_episodes:
            source_cycle_id = record["cycle_id"]
            cycle_id_map.setdefault(source_cycle_id, f"cycle:{uuid.uuid4().hex}")
            old_episode_id = record["episode_id"]
            episode_id_map[old_episode_id] = f"episode:{uuid.uuid4().hex}"
            cloned_episodes.append(
                {
                    **record,
                    "episode_id": episode_id_map[old_episode_id],
                    "cycle_id": cycle_id_map[source_cycle_id],
                    "memory_set_id": target_memory_set_id,
                }
            )

        for record in cloned_episodes:
            record["linked_event_ids"] = [
                event_id_map.get(event_id, event_id)
                for event_id in record.get("linked_event_ids", [])
            ]
            episode_series_id = record.get("episode_series_id")
            if isinstance(episode_series_id, str) and episode_series_id in episode_id_map:
                record["episode_series_id"] = episode_id_map[episode_series_id]
            self._insert_episode(conn, record)
        return episode_id_map

    def _clone_memory_unit_records(
        self,
        conn: sqlite3.Connection,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
        event_id_map: dict[str, str],
    ) -> dict[str, str]:
        memory_unit_id_map: dict[str, str] = {}
        source_memory_units = self._load_payload_rows(conn, "memory_units", source_memory_set_id)
        for record in source_memory_units:
            old_memory_unit_id = record["memory_unit_id"]
            memory_unit_id_map[old_memory_unit_id] = f"memory_unit:{uuid.uuid4().hex}"
            self._upsert_memory_unit(
                conn,
                {
                    **record,
                    "memory_unit_id": memory_unit_id_map[old_memory_unit_id],
                    "memory_set_id": target_memory_set_id,
                    "evidence_event_ids": [
                        event_id_map.get(event_id, event_id)
                        for event_id in record.get("evidence_event_ids", [])
                    ],
                },
            )
        return memory_unit_id_map

    def _clone_revision_records(
        self,
        conn: sqlite3.Connection,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
        event_id_map: dict[str, str],
        memory_unit_id_map: dict[str, str],
    ) -> None:
        source_revisions = self._load_payload_rows(conn, "revisions", source_memory_set_id)
        for record in source_revisions:
            self._insert_revision(
                conn,
                {
                    **record,
                    "revision_id": f"revision:{uuid.uuid4().hex}",
                    "memory_set_id": target_memory_set_id,
                    "memory_unit_id": memory_unit_id_map.get(record["memory_unit_id"], record["memory_unit_id"]),
                    "related_memory_unit_ids": [
                        memory_unit_id_map.get(memory_unit_id, memory_unit_id)
                        for memory_unit_id in record.get("related_memory_unit_ids", [])
                    ],
                    "before_snapshot": self._clone_memory_unit_snapshot(
                        record.get("before_snapshot"),
                        event_id_map=event_id_map,
                        memory_unit_id_map=memory_unit_id_map,
                    ),
                    "after_snapshot": self._clone_memory_unit_snapshot(
                        record.get("after_snapshot"),
                        event_id_map=event_id_map,
                        memory_unit_id_map=memory_unit_id_map,
                    ),
                    "evidence_event_ids": [
                        event_id_map.get(event_id, event_id)
                        for event_id in record.get("evidence_event_ids", [])
                    ],
                },
            )

    def _clone_affect_state_records(
        self,
        conn: sqlite3.Connection,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
    ) -> None:
        source_affect_states = self._load_payload_rows(conn, "affect_state", source_memory_set_id)
        for record in source_affect_states:
            self._upsert_affect_state(
                conn,
                {
                    **record,
                    "affect_state_id": f"affect_state:{uuid.uuid4().hex}",
                    "memory_set_id": target_memory_set_id,
                },
            )

    def _clone_episode_affect_records(
        self,
        conn: sqlite3.Connection,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
        episode_id_map: dict[str, str],
    ) -> None:
        source_episode_affects = self._load_payload_rows(conn, "episode_affects", source_memory_set_id)
        for record in source_episode_affects:
            self._insert_episode_affect(
                conn,
                {
                    **record,
                    "episode_affect_id": f"episode_affect:{uuid.uuid4().hex}",
                    "memory_set_id": target_memory_set_id,
                    "episode_id": episode_id_map.get(record["episode_id"], record["episode_id"]),
                },
            )

    def _clone_mood_state_records(
        self,
        conn: sqlite3.Connection,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
    ) -> None:
        source_mood_states = self._load_payload_rows(conn, "mood_state", source_memory_set_id)
        for record in source_mood_states:
            self._upsert_mood_state(
                conn,
                {
                    **record,
                    "mood_state_id": f"mood_state:{target_memory_set_id}",
                    "memory_set_id": target_memory_set_id,
                },
            )

    def _clone_reflection_run_records(
        self,
        conn: sqlite3.Connection,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
        episode_id_map: dict[str, str],
        memory_unit_id_map: dict[str, str],
    ) -> None:
        source_reflection_runs = self._load_payload_rows(conn, "reflection_runs", source_memory_set_id)
        for record in source_reflection_runs:
            self._insert_reflection_run(
                conn,
                {
                    **record,
                    "reflection_run_id": f"reflection_run:{uuid.uuid4().hex}",
                    "memory_set_id": target_memory_set_id,
                    "source_episode_ids": [
                        episode_id_map.get(episode_id, episode_id)
                        for episode_id in record.get("source_episode_ids", [])
                    ],
                    "affected_memory_unit_ids": [
                        memory_unit_id_map.get(memory_unit_id, memory_unit_id)
                        for memory_unit_id in record.get("affected_memory_unit_ids", [])
                    ],
                },
            )

    def _load_payload_rows(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        memory_set_id: str,
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            f"""
            SELECT payload_json
            FROM {table_name}
            WHERE memory_set_id = ?
            ORDER BY rowid ASC
            """,
            (memory_set_id,),
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def _clone_memory_unit_snapshot(
        self,
        snapshot: dict[str, Any] | None,
        *,
        event_id_map: dict[str, str],
        memory_unit_id_map: dict[str, str],
    ) -> dict[str, Any] | None:
        if snapshot is None:
            return None
        cloned_snapshot = {
            **snapshot,
            "memory_unit_id": memory_unit_id_map.get(snapshot["memory_unit_id"], snapshot["memory_unit_id"]),
            "evidence_event_ids": [
                event_id_map.get(event_id, event_id)
                for event_id in snapshot.get("evidence_event_ids", [])
            ],
        }
        return cloned_snapshot

    def _clone_vector_index_entries(
        self,
        conn: sqlite3.Connection,
        *,
        source_memory_set_id: str,
        target_memory_set_id: str,
        episode_id_map: dict[str, str],
        memory_unit_id_map: dict[str, str],
    ) -> None:
        rows = conn.execute(
            """
            SELECT *
            FROM vector_index_entries
            WHERE memory_set_id = ?
            ORDER BY vector_entry_id ASC
            """,
            (source_memory_set_id,),
        ).fetchall()

        for row in rows:
            source_kind = row["source_kind"]
            source_id = row["source_id"]
            if source_kind == "episode":
                target_source_id = episode_id_map.get(source_id)
            elif source_kind == "memory_unit":
                target_source_id = memory_unit_id_map.get(source_id)
            else:
                continue
            if target_source_id is None:
                continue

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
                    target_memory_set_id,
                    source_kind,
                    target_source_id,
                    row["source_text"],
                    row["scope_type"],
                    row["scope_key"],
                    row["source_type"],
                    row["status"],
                    row["salience"],
                    row["has_open_loops"],
                    row["updated_at"],
                    row["text_hash"],
                ),
            )
            new_vector_entry_id = int(cursor.lastrowid)

            vector_blob = conn.execute(
                f"SELECT embedding FROM {self._vector_table_name(source_kind)} WHERE id = ?",
                (row["vector_entry_id"],),
            ).fetchone()
            if vector_blob is None:
                continue
            conn.execute(
                f"INSERT INTO {self._vector_table_name(source_kind)}(id, embedding) VALUES (?, ?)",
                (new_vector_entry_id, vector_blob["embedding"]),
            )
