from __future__ import annotations

import json
import sqlite3
from typing import Any

# 複製する正本と、その識別子・保存処理。実行中の作業と短期状態は複製しない。
CLONE_TABLES = (
    ("events", "event_id", "event", "_insert_event"),
    ("visual_observation_records", "visual_observation_id", "visual_observation", "_insert_visual_observation_record"),
    ("daily_visual_digests", "digest_id", "daily_visual_digest", "_insert_daily_visual_digest"),
    ("episodes", "episode_id", "episode", "_insert_episode"),
    ("memory_units", "memory_unit_id", "memory_unit", "_upsert_memory_unit"),
    ("revisions", "revision_id", "revision", "_insert_revision"),
    ("memory_links", "memory_link_id", "memory_link", "_upsert_memory_link"),
    ("entity_registry", None, None, "_upsert_entity_registry_record"),
    ("episode_affects", "episode_affect_id", "episode_affect", "_insert_episode_affect"),
    ("mood_state", "mood_state_id", "mood_state", "_upsert_mood_state"),
    ("affect_state", "affect_state_id", "affect_state", "_upsert_affect_state"),
    ("reflection_runs", "reflection_run_id", "reflection_run", "_insert_reflection_run"),
)
# 本文には触れず、閉じた参照フィールドだけを処理する。
CLONE_REFERENCE_FIELDS = {
    "ref", "source_ref", "source_id", "event_id", "cycle_id", "related_cycle_id", "source_cycle_id",
    "episode_id", "episode_series_id", "memory_unit_id", "revision_id", "memory_link_id",
    "source_memory_unit_id", "target_memory_unit_id", "evidence_revision_id", "corrects_revision_id",
    "correction_group_id", "episode_affect_id", "mood_state_id", "affect_state_id", "reflection_run_id",
    "observation_id", "visual_observation_id", "representative_visual_observation_id",
    "digest_id", "daily_digest_id", "duplicate_group_id", "group_ref", "evidence_ref", "memory_ref",
    "linked_event_ids", "evidence_event_ids", "evidence_cycle_ids", "correction_basis_event_ids",
    "source_episode_ids", "affected_memory_unit_ids", "related_memory_unit_ids", "supporting_memory_unit_ids",
    "memory_unit_ids", "promoted_memory_unit_ids", "support_group_refs", "candidate_groups", "evidence_groups",
    "evidence_refs", "source_event_refs", "source_event_ids", "source_observation_ids", "source_observation_refs",
}
CLONE_AUXILIARY_IDS = {
    "cycle_id": "cycle", "related_cycle_id": "cycle", "source_cycle_id": "cycle",
    "evidence_cycle_ids": "cycle", "episode_series_id": "episode_series", "correction_group_id": "correction",
}
CLONE_ID_KINDS = {kind for _, _, kind, _ in CLONE_TABLES if kind} | set(CLONE_AUXILIARY_IDS.values()) | {"visual_duplicate_group"}
CLONE_STRICT_REFERENCE_FIELDS = CLONE_REFERENCE_FIELDS - {
    "ref", "source_ref", "source_id", "observation_id", "source_observation_ids", "source_observation_refs",
    "group_ref", "evidence_ref", "memory_ref", "evidence_refs",
}


class MemorySetInUseError(ValueError):
    """実行中の記憶後処理がある集合は削除できない。"""


class StoreCloneMixin:
    def delete_memory_set_records(self, memory_set_id: str) -> None:
        # トランザクション
        with self._memory_db() as conn:
            running_job = conn.execute(
                "SELECT 1 FROM memory_postprocess_jobs "
                "WHERE memory_set_id = ? AND result_status = 'running' LIMIT 1",
                (memory_set_id,),
            ).fetchone()
            if running_job is not None:
                raise MemorySetInUseError("The memory set has a running postprocess job.")

            # ベクトル削除
            self._delete_vector_index_entries(conn, memory_set_id)

            # 削除順序
            conn.execute("DELETE FROM world_states WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM daily_visual_digests WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM visual_observation_search_index WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM visual_observation_records WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM ongoing_actions WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM autonomous_runs WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM drive_states WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM memory_postprocess_jobs WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM entity_aliases WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM entity_registry WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM relation_index WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM memory_links WHERE memory_set_id = ?", (memory_set_id,))
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
            conn.execute("DELETE FROM internal_identities WHERE scope = ?", (memory_set_id,))

    def clone_memory_set_records(
        self, *, source_memory_set_id: str, target_memory_set_id: str,
    ) -> None:
        if source_memory_set_id == target_memory_set_id:
            raise ValueError("記憶集合の複製先は別の集合にします。")
        with self._memory_db() as conn:
            records = {table: self._load_payload_rows(conn, table, source_memory_set_id)
                       for table, _, _, _ in CLONE_TABLES}
            if any(self._load_payload_rows(conn, table, target_memory_set_id) for table, _, _, _ in CLONE_TABLES):
                raise ValueError("記憶集合の複製先は空である必要があります。")
            references: dict[str, str] = {}
            for table, id_key, kind, _ in CLONE_TABLES:
                if id_key is None:
                    continue
                for record in records[table]:
                    if kind == "daily_visual_digest":
                        identifier = self._resolve_internal_id(
                            conn, kind, scope=target_memory_set_id, key=record["local_date"],
                        )
                    elif kind == "mood_state":
                        identifier = self._resolve_internal_id(conn, kind, scope=target_memory_set_id, key="self")
                    else:
                        identifier = self._allocate_id(conn, kind)
                    references[record[id_key]] = identifier
            for digest in records["daily_visual_digests"]:
                for index, group in enumerate(digest["group_summaries"], start=1):
                    references[group["duplicate_group_id"]] = self._resolve_internal_id(
                        conn, "visual_duplicate_group", scope=target_memory_set_id,
                        key=f"{digest['local_date']}:{index}",
                    )

            def prepare_auxiliary_ids(value):
                if isinstance(value, list):
                    for item in value:
                        prepare_auxiliary_ids(item)
                elif isinstance(value, dict):
                    for key, item in value.items():
                        if key in CLONE_AUXILIARY_IDS and item is not None:
                            for identifier in item if isinstance(item, list) else [item]:
                                if not isinstance(identifier, str) or not identifier:
                                    raise ValueError("複製する識別子は空でない文字列である必要があります。")
                                if identifier not in references:
                                    references[identifier] = self._allocate_id(conn, CLONE_AUXILIARY_IDS[key])
                        elif key != "event_reference_integrity":
                            prepare_auxiliary_ids(item)
            prepare_auxiliary_ids(list(records.values()))
            missing_event_ids = {
                identifier for event in records["events"] if event["kind"] == "event_reference_repaired"
                for identifier in event["missing_event_ids"]
            }

            for table, _, _, insert_method in CLONE_TABLES:
                for record in records[table]:
                    cloned = self._clone_record_references(
                        record, references, target_memory_set_id,
                        historical=table == "revisions", missing_event_ids=missing_event_ids,
                    )
                    getattr(self, insert_method)(conn, cloned)
                    if table == "entity_registry":
                        for alias in cloned.get("aliases", []):
                            self._upsert_entity_alias(
                                conn, memory_set_id=target_memory_set_id, entity_ref=cloned["entity_ref"],
                                alias_text=alias, entity_type=cloned["entity_type"], confidence=cloned["confidence"],
                                updated_at=cloned["last_seen_at"],
                            )
            self._rebuild_relation_index(
                conn, memory_set_id=target_memory_set_id,
                updated_at=self._relation_index_latest_source_time(conn, target_memory_set_id),
            )
            self._clone_vector_index_entries(
                conn, source_memory_set_id=source_memory_set_id, target_memory_set_id=target_memory_set_id,
                episode_id_map={record["episode_id"]: references[record["episode_id"]] for record in records["episodes"]},
                memory_unit_id_map={record["memory_unit_id"]: references[record["memory_unit_id"]] for record in records["memory_units"]},
            )

    def _clone_record_references(
        self, value: Any, references: dict[str, str], memory_set_id: str, *,
        historical: bool, missing_event_ids: set[str],
    ) -> Any:
        def reference(item, *, strict):
            if item is None:
                return None
            if isinstance(item, list):
                return [reference(child, strict=strict) for child in item]
            if isinstance(item, dict):
                return {references[key] if key in references else key: reference(child, strict=strict)
                        for key, child in item.items()}
            if not isinstance(item, str):
                raise ValueError("複製する根拠参照は文字列である必要があります。")
            if item in references:
                return references[item]
            if historical and item in missing_event_ids:
                # revisionは当時の記録。欠落が監査済みの根拠IDは履歴として保持する。
                return item
            if strict or item.partition(":")[0] in CLONE_ID_KINDS:
                raise ValueError(f"記憶集合の複製元参照がありません: {item}")
            # 人物・機器・外部sampleや履歴にのみ属する実行識別子は複製対象ではない。
            return item

        if isinstance(value, list):
            return [self._clone_record_references(
                item, references, memory_set_id, historical=historical, missing_event_ids=missing_event_ids,
            ) for item in value]
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key == "memory_set_id":
                    result[key] = memory_set_id
                elif key == "event_reference_integrity":
                    result[key] = item
                elif key in CLONE_REFERENCE_FIELDS:
                    result[key] = reference(item, strict=key in CLONE_STRICT_REFERENCE_FIELDS)
                else:
                    result[key] = self._clone_record_references(
                        item, references, memory_set_id, historical=historical, missing_event_ids=missing_event_ids,
                    )
            return result
        return value

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
                raise ValueError(f"複製する検索索引の種類が不正です: {source_kind}")
            if target_source_id is None:
                raise ValueError(f"複製する検索索引の正本がありません: {source_id}")

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
                raise ValueError(f"複製する検索索引のベクトルがありません: {source_id}")
            conn.execute(
                f"INSERT INTO {self._vector_table_name(source_kind)}(id, embedding) VALUES (?, ?)",
                (new_vector_entry_id, vector_blob["embedding"]),
            )
