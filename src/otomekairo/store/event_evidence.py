from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from otomekairo.memory.event_references import (
    REFERENCE_SOURCES,
    merge_event_reference_integrity,
    preserve_event_reference_integrity,
)
from otomekairo.service.common import debug_log


class StoreEventEvidenceMixin:
    def load_events_for_evidence(
        self, *, memory_set_id: str, event_ids: list[str], limit: int,
    ) -> list[dict[str, Any]]:
        if not event_ids or limit <= 0:
            return []
        requested = [value for value in event_ids if isinstance(value, str)]
        if not requested:
            return []
        with self._memory_db() as conn:
            records = self._load_evidence_events(conn, memory_set_id=memory_set_id, event_ids=requested)
        by_id = {record["event_id"]: record for record in records}
        return [by_id[value] for value in requested if value in by_id][:limit]

    def load_and_repair_event_evidence(
        self, *, memory_set_id: str, event_ids: list[str], limit: int,
        cycle_id: str, detected_at: str,
    ) -> dict[str, Any]:
        if not isinstance(cycle_id, str) or not cycle_id:
            raise ValueError("根拠eventの取得には起点cycle_idが必要です。")
        if any(not isinstance(value, str) or not value for value in event_ids):
            raise ValueError("根拠eventのIDは空でない文字列である必要があります。")
        requested = list(dict.fromkeys(event_ids))[:max(0, limit)]
        result = {"events": [], "missing_event_ids": [], "repaired_sources": []}
        if not requested:
            return result
        # 存在確認、現行参照の修復、監査を一緒に確定する。
        with self._memory_db() as conn:
            result["events"] = self._load_evidence_events(
                conn, memory_set_id=memory_set_id, event_ids=requested,
            )
            loaded = {record["event_id"] for record in result["events"]}
            missing = [value for value in requested if value not in loaded]
            result["missing_event_ids"] = missing
            if not missing:
                return result
            integrity = {"removed_event_ids": missing, "repaired_at": detected_at}
            new_repairs: list[dict[str, Any]] = []
            placeholders = ", ".join("?" for _ in missing)
            for kind, (table, id_key, reference_key) in REFERENCE_SOURCES.items():
                column = f"{reference_key}_json"
                rows = conn.execute(
                    f"""SELECT payload_json, {column} AS references_json FROM {table}
                    WHERE memory_set_id = ? AND (EXISTS (
                        SELECT 1 FROM json_each({column}) WHERE value IN ({placeholders})
                    ) OR EXISTS (
                        SELECT 1 FROM json_each(json_extract(payload_json, '$.event_reference_integrity.removed_event_ids'))
                        WHERE value IN ({placeholders})
                    ))""", (memory_set_id, *missing, *missing),
                ).fetchall()
                for row in rows:
                    record = json.loads(row["payload_json"])
                    references = json.loads(row["references_json"])
                    if record.get(reference_key, []) != references:
                        raise ValueError(f"{table} のevent参照がJSON本文と一致しません。")
                    removed = [value for value in references if value in missing]
                    repaired = preserve_event_reference_integrity(
                        record, reference_key=reference_key,
                        integrity={**integrity, "removed_event_ids": list(dict.fromkeys(removed))} if removed else None,
                    )
                    state = {
                        "source_kind": kind, "source_id": record[id_key],
                        "removed_event_ids": list(dict.fromkeys(removed)),
                        "event_reference_integrity": repaired["event_reference_integrity"],
                    }
                    result["repaired_sources"].append(state)
                    if removed:
                        conn.execute(
                            f"UPDATE {table} SET {column} = ?, payload_json = ? WHERE memory_set_id = ? AND {id_key} = ?",
                            (self._to_json(repaired[reference_key]), self._to_json(repaired), memory_set_id, record[id_key]),
                        )
                        new_repairs.append(state)
            known = self._known_event_reference_integrity(conn, memory_set_id=memory_set_id, event_ids=missing)
            new_ids = [value for value in missing if known is None or value not in known["removed_event_ids"]]
            if new_ids or new_repairs:
                self._insert_event(conn, {
                    "event_id": f"event:{uuid.uuid4().hex}", "cycle_id": cycle_id,
                    "memory_set_id": memory_set_id, "kind": "event_reference_repaired", "role": "system",
                    "created_at": detected_at, "failure_stage": "load_event",
                    "failure_reason": "referenced event was not found in this memory set.",
                    "missing_event_ids": missing, "repaired_sources": new_repairs,
                })
        if new_ids or new_repairs:
            debug_log("Store", f"event_reference_repaired cycle_id={cycle_id} "
                      f"missing_event_ids={missing} repaired_source_count={len(new_repairs)}", level="ERROR")
        return result

    def _load_evidence_events(
        self, conn: sqlite3.Connection, *, memory_set_id: str, event_ids: list[str],
    ) -> list[dict[str, Any]]:
        placeholders = ", ".join("?" for _ in event_ids)
        rows = conn.execute(
            f"SELECT event_id, payload_json FROM events WHERE memory_set_id = ? AND event_id IN ({placeholders})",
            (memory_set_id, *event_ids),
        ).fetchall()
        by_id = {}
        for row in rows:
            record = json.loads(row["payload_json"])
            if not isinstance(record, dict) or record.get("event_id") != row["event_id"]:
                raise ValueError("eventの識別子がJSON本文と一致しません。")
            by_id[row["event_id"]] = record
        return [by_id[value] for value in dict.fromkeys(event_ids) if value in by_id]

    def _known_event_reference_integrity(
        self, conn: sqlite3.Connection, *, memory_set_id: str, event_ids: list[str],
    ) -> dict[str, Any] | None:
        if not event_ids:
            return None
        placeholders = ", ".join("?" for _ in event_ids)
        rows = conn.execute(
            f"""SELECT created_at, payload_json FROM events
            WHERE memory_set_id = ? AND kind = 'event_reference_repaired' AND EXISTS (
                SELECT 1 FROM json_each(json_extract(payload_json, '$.missing_event_ids'))
                WHERE value IN ({placeholders})
            ) ORDER BY created_at, rowid""", (memory_set_id, *event_ids),
        ).fetchall()
        return merge_event_reference_integrity(*[
            {"removed_event_ids": [value for value in json.loads(row["payload_json"])["missing_event_ids"]
                                   if value in event_ids], "repaired_at": row["created_at"]}
            for row in rows
        ])

    def _preserve_stored_event_reference_integrity(
        self, conn: sqlite3.Connection, *, source_kind: str, record: dict[str, Any],
    ) -> dict[str, Any]:
        table, id_key, reference_key = REFERENCE_SOURCES[source_kind]
        row = conn.execute(
            f"SELECT payload_json FROM {table} WHERE memory_set_id = ? AND {id_key} = ?",
            (record["memory_set_id"], record[id_key]),
        ).fetchone()
        previous = json.loads(row["payload_json"]) if row is not None else {}
        known = self._known_event_reference_integrity(
            conn, memory_set_id=record["memory_set_id"], event_ids=record.get(reference_key, []),
        )
        return preserve_event_reference_integrity(record, reference_key=reference_key, integrity=
            merge_event_reference_integrity(previous.get("event_reference_integrity"), known))
