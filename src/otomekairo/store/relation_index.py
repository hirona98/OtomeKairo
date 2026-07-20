from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter
from typing import Any

from otomekairo.memory.utils import clamp_score, now_iso, parse_iso


ACTIVE_MEMORY_STATUSES = {"confirmed", "inferred"}
CONFIRMED_MEMORY_STATUS = "confirmed"
RELATION_REF_PREFIXES = ("person:", "place:", "tool:", "topic:")
RELATION_LINK_LABELS = {"supports", "contradicts", "about_same_scope", "affects"}
RELATION_INDEX_NAMESPACE = uuid.UUID("5e0fa4c4-7e9f-4fd8-beca-a2e46e10f2fb")


class StoreRelationIndexMixin:
    def rebuild_relation_index(self, *, memory_set_id: str, updated_at: str) -> dict[str, Any]:
        # 派生索引は memory_set 全体を 1 transaction で置き換える。
        with self._memory_db() as conn:
            return self._rebuild_relation_index(
                conn,
                memory_set_id=memory_set_id,
                updated_at=updated_at,
            )

    def _rebuild_relation_index(
        self,
        conn: sqlite3.Connection,
        *,
        memory_set_id: str,
        updated_at: str,
    ) -> dict[str, Any]:
        units = self._relation_index_memory_units(conn, memory_set_id)
        revisions = self._relation_index_revisions(conn, memory_set_id)
        edge_units: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        unit_edge: dict[str, tuple[str, str, str]] = {}
        skipped_multi_party_count = 0
        skipped_invalid_count = 0

        for unit in units:
            identity, skip_reason = self._relation_edge_identity(unit)
            if identity is None:
                if skip_reason == "multi_party":
                    skipped_multi_party_count += 1
                elif skip_reason is not None:
                    skipped_invalid_count += 1
                continue
            edge_units.setdefault(identity, []).append(unit)
            unit_edge[str(unit["memory_unit_id"])] = identity

        edge_links = self._relation_index_links(
            conn,
            memory_set_id=memory_set_id,
            unit_edge=unit_edge,
        )
        records = [
            self._build_relation_index_record(
                memory_set_id=memory_set_id,
                identity=identity,
                units=supporting_units,
                links=edge_links.get(identity, []),
                revisions=revisions,
                updated_at=updated_at,
            )
            for identity, supporting_units in edge_units.items()
        ]
        records.sort(key=lambda item: (item["source_ref"], item["target_ref"], item["relation_predicate"]))

        conn.execute("DELETE FROM relation_index WHERE memory_set_id = ?", (memory_set_id,))
        for record in records:
            self._insert_relation_index_record(conn, record)

        status_counts = Counter(record["derived_status"] for record in records)
        return {
            "result_status": "succeeded",
            "edge_count": len(records),
            "status_counts": {
                "active": status_counts.get("active", 0),
                "weak": status_counts.get("weak", 0),
                "inactive": status_counts.get("inactive", 0),
            },
            "skipped_multi_party_count": skipped_multi_party_count,
            "skipped_invalid_count": skipped_invalid_count,
            "failure_reason": None,
        }

    def list_relation_index_records(self, *, memory_set_id: str, limit: int = 50) -> list[dict[str, Any]]:
        # inspection 用の現在索引。
        with self._memory_db() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM relation_index
                WHERE memory_set_id = ?
                ORDER BY
                    CASE derived_status WHEN 'active' THEN 0 WHEN 'weak' THEN 1 ELSE 2 END,
                    salience DESC,
                    confidence DESC,
                    last_evidence_at DESC,
                    relation_index_id ASC
                LIMIT ?
                """,
                (memory_set_id, max(1, int(limit))),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def list_relation_index_for_recall(
        self,
        *,
        memory_set_id: str,
        entity_refs: list[str],
        current_time: str,
        limit: int = 12,
    ) -> dict[str, Any]:
        # canonical entity に隣接する完成済み edge だけを読む。
        refs = self._unique_relation_refs(entity_refs)
        if not refs:
            return self._empty_relation_recall_result()
        placeholders = ", ".join("?" for _ in refs)
        query_limit = max(int(limit) * 4, 24)
        with self._memory_db() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM relation_index
                WHERE memory_set_id = ?
                  AND derived_status IN ('active', 'weak')
                  AND (source_ref IN ({placeholders}) OR target_ref IN ({placeholders}))
                ORDER BY
                    CASE derived_status WHEN 'active' THEN 0 ELSE 1 END,
                    salience DESC,
                    confidence DESC,
                    last_evidence_at DESC,
                    relation_index_id ASC
                LIMIT ?
                """,
                (memory_set_id, *refs, *refs, query_limit),
            ).fetchall()
            edges = [json.loads(row["payload_json"]) for row in rows]
            unit_ids = self._relation_unique_texts(
                [
                    memory_unit_id
                    for edge in edges
                    for memory_unit_id in edge.get("supporting_memory_unit_ids", [])
                ]
            )
            units_by_id = self._relation_units_by_id(conn, memory_set_id, unit_ids)

        candidates: list[dict[str, Any]] = []
        seen_unit_ids: set[str] = set()
        stale_memory_unit_ids: list[str] = []
        used_edge_ids: list[str] = []
        status_counts: Counter[str] = Counter()
        for edge in edges:
            active_edge_units = [
                units_by_id[memory_unit_id]
                for memory_unit_id in edge.get("supporting_memory_unit_ids", [])
                if memory_unit_id in units_by_id
                and self._relation_unit_is_active(units_by_id[memory_unit_id], current_time)
            ]
            if not active_edge_units:
                for memory_unit_id in edge.get("supporting_memory_unit_ids", []):
                    if memory_unit_id not in stale_memory_unit_ids:
                        stale_memory_unit_ids.append(memory_unit_id)
                continue
            effective_status = str(edge["derived_status"])
            if effective_status == "active" and not any(
                unit.get("status") == CONFIRMED_MEMORY_STATUS
                for unit in active_edge_units
            ):
                effective_status = "weak"
            edge_used = False
            for memory_unit_id in edge.get("supporting_memory_unit_ids", []):
                unit = units_by_id.get(memory_unit_id)
                if unit is None or not self._relation_unit_is_active(unit, current_time):
                    if memory_unit_id not in stale_memory_unit_ids:
                        stale_memory_unit_ids.append(memory_unit_id)
                    continue
                if memory_unit_id in seen_unit_ids:
                    continue
                candidates.append(
                    {
                        "memory_unit": unit,
                        "relation_index_id": edge["relation_index_id"],
                        "relation_predicate": edge["relation_predicate"],
                        "relation_derived_status": effective_status,
                        "relation_source_ref": edge["source_ref"],
                        "relation_target_ref": edge["target_ref"],
                    }
                )
                seen_unit_ids.add(memory_unit_id)
                edge_used = True
                if len(candidates) >= max(1, int(limit)):
                    break
            if edge_used:
                used_edge_ids.append(edge["relation_index_id"])
                status_counts[effective_status] += 1
            if len(candidates) >= max(1, int(limit)):
                break

        return {
            "candidates": candidates,
            "requested_entity_refs": refs,
            "matched_edge_count": len(edges),
            "used_edge_ids": used_edge_ids,
            "status_counts": {
                "active": status_counts.get("active", 0),
                "weak": status_counts.get("weak", 0),
            },
            "stale_memory_unit_ids": stale_memory_unit_ids,
        }

    def _relation_index_memory_units(self, conn: sqlite3.Connection, memory_set_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM memory_units
            WHERE memory_set_id = ?
              AND (memory_type = 'relation' OR scope_type = 'relationship')
            """,
            (memory_set_id,),
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def _relation_index_revisions(
        self,
        conn: sqlite3.Connection,
        memory_set_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM revisions
            WHERE memory_set_id = ?
            ORDER BY occurred_at ASC, rowid ASC
            """,
            (memory_set_id,),
        ).fetchall()
        revisions: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            revision = json.loads(row["payload_json"])
            memory_unit_id = revision.get("memory_unit_id")
            if isinstance(memory_unit_id, str) and memory_unit_id:
                revisions.setdefault(memory_unit_id, []).append(revision)
        return revisions

    def _relation_index_links(
        self,
        conn: sqlite3.Connection,
        *,
        memory_set_id: str,
        unit_edge: dict[str, tuple[str, str, str]],
    ) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM memory_links
            WHERE memory_set_id = ?
              AND label IN ('supports', 'contradicts', 'about_same_scope', 'affects')
            ORDER BY updated_at ASC, rowid ASC
            """,
            (memory_set_id,),
        ).fetchall()
        edge_links: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in rows:
            link = json.loads(row["payload_json"])
            source_identity = unit_edge.get(str(link.get("source_memory_unit_id") or ""))
            target_identity = unit_edge.get(str(link.get("target_memory_unit_id") or ""))
            if source_identity is None or source_identity != target_identity:
                continue
            edge_links.setdefault(source_identity, []).append(link)
        return edge_links

    def _relation_edge_identity(
        self,
        unit: dict[str, Any],
    ) -> tuple[tuple[str, str, str] | None, str | None]:
        predicate = unit.get("predicate")
        if not isinstance(predicate, str) or not predicate.strip():
            return None, "invalid"

        refs: list[str]
        if unit.get("scope_type") == "relationship":
            scope_key = unit.get("scope_key")
            if not isinstance(scope_key, str):
                return None, "invalid"
            refs = [ref.strip() for ref in scope_key.split("|") if ref.strip()]
            if len(refs) > 2:
                return None, "multi_party"
            if len(refs) != 2:
                return None, "invalid"
        elif unit.get("memory_type") == "relation":
            refs = [
                str(unit.get("subject_ref") or "").strip(),
                str(unit.get("object_ref_or_value") or "").strip(),
            ]
        else:
            return None, None

        if len(set(refs)) != 2 or any(not self._is_relation_ref(ref) for ref in refs):
            return None, "invalid"
        source_ref, target_ref = self._ordered_relation_refs(refs)
        return (source_ref, target_ref, predicate.strip()), None

    def _build_relation_index_record(
        self,
        *,
        memory_set_id: str,
        identity: tuple[str, str, str],
        units: list[dict[str, Any]],
        links: list[dict[str, Any]],
        revisions: dict[str, list[dict[str, Any]]],
        updated_at: str,
    ) -> dict[str, Any]:
        units = sorted(units, key=self._relation_support_sort_key, reverse=True)
        active_units = [unit for unit in units if self._relation_unit_is_active(unit, updated_at)]
        confirmed_units = [unit for unit in active_units if unit.get("status") == CONFIRMED_MEMORY_STATUS]
        active_unit_ids = {str(unit["memory_unit_id"]) for unit in active_units}
        active_contradictions = [
            link
            for link in links
            if link.get("label") == "contradicts"
            and link.get("source_memory_unit_id") in active_unit_ids
            and link.get("target_memory_unit_id") in active_unit_ids
        ]
        if not active_units:
            derived_status = "inactive"
        elif not confirmed_units or active_contradictions:
            derived_status = "weak"
        else:
            derived_status = "active"

        quality_units = active_units or units
        confidence = max((clamp_score(unit.get("confidence")) for unit in quality_units), default=0.0)
        salience = max((clamp_score(unit.get("salience")) for unit in quality_units), default=0.0)
        supporting_ids = self._relation_unique_texts([unit.get("memory_unit_id") for unit in units])
        supporting_link_ids = self._relation_unique_texts([link.get("memory_link_id") for link in links])
        operation_counts: Counter[str] = Counter()
        evidence_times: list[str] = []
        for unit in units:
            for field in ("last_confirmed_at", "formed_at"):
                value = unit.get(field)
                if isinstance(value, str) and value:
                    evidence_times.append(value)
            for revision in revisions.get(str(unit.get("memory_unit_id") or ""), []):
                operation = revision.get("operation")
                if isinstance(operation, str) and operation:
                    operation_counts[operation] += 1
                occurred_at = revision.get("occurred_at")
                if isinstance(occurred_at, str) and occurred_at:
                    evidence_times.append(occurred_at)
        last_evidence_at = max(evidence_times, key=lambda value: parse_iso(value))
        representative = max(
            quality_units,
            key=lambda unit: (
                unit.get("status") == "confirmed",
                clamp_score(unit.get("salience")),
                clamp_score(unit.get("confidence")),
                self._relation_timestamp_key(unit.get("last_confirmed_at") or unit.get("formed_at")),
            ),
        )
        source_ref, target_ref, predicate = identity
        identity_text = f"{memory_set_id}\n{source_ref}\n{target_ref}\n{predicate}"
        record = {
            "relation_index_id": f"relation_index:{uuid.uuid5(RELATION_INDEX_NAMESPACE, identity_text).hex}",
            "memory_set_id": memory_set_id,
            "source_ref": source_ref,
            "target_ref": target_ref,
            "relation_predicate": predicate,
            "supporting_memory_unit_ids": supporting_ids,
            "supporting_memory_link_ids": supporting_link_ids,
            "derived_status": derived_status,
            "confidence": confidence,
            "salience": salience,
            "last_evidence_at": last_evidence_at,
            "updated_at": updated_at,
        }
        record["payload"] = {
            "representative_summary": representative.get("summary_text"),
            "support_count": len(units),
            "active_support_count": len(active_units),
            "confirmed_support_count": len(confirmed_units),
            "inactive_support_count": len(units) - len(active_units),
            "contradiction_count": len(active_contradictions),
            "revision_operation_counts": dict(sorted(operation_counts.items())),
        }
        return record

    def _insert_relation_index_record(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO relation_index (
                relation_index_id,
                memory_set_id,
                source_ref,
                target_ref,
                relation_predicate,
                supporting_memory_unit_ids_json,
                supporting_memory_link_ids_json,
                derived_status,
                confidence,
                salience,
                last_evidence_at,
                updated_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["relation_index_id"],
                record["memory_set_id"],
                record["source_ref"],
                record["target_ref"],
                record["relation_predicate"],
                self._to_json(record["supporting_memory_unit_ids"]),
                self._to_json(record["supporting_memory_link_ids"]),
                record["derived_status"],
                record["confidence"],
                record["salience"],
                record["last_evidence_at"],
                record["updated_at"],
                self._to_json(record),
            ),
        )

    def _relation_units_by_id(
        self,
        conn: sqlite3.Connection,
        memory_set_id: str,
        memory_unit_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        if not memory_unit_ids:
            return {}
        placeholders = ", ".join("?" for _ in memory_unit_ids)
        rows = conn.execute(
            f"""
            SELECT payload_json
            FROM memory_units
            WHERE memory_set_id = ?
              AND memory_unit_id IN ({placeholders})
            """,
            (memory_set_id, *memory_unit_ids),
        ).fetchall()
        units = [json.loads(row["payload_json"]) for row in rows]
        return {str(unit["memory_unit_id"]): unit for unit in units}

    def _relation_unit_is_active(self, unit: dict[str, Any], current_time: str) -> bool:
        if unit.get("status") not in ACTIVE_MEMORY_STATUSES:
            return False
        valid_to = unit.get("valid_to")
        return not isinstance(valid_to, str) or not valid_to or parse_iso(valid_to) > parse_iso(current_time)

    def _relation_index_latest_source_time(self, conn: sqlite3.Connection, memory_set_id: str) -> str:
        row = conn.execute(
            """
            SELECT MAX(source_time)
            FROM (
                SELECT formed_at AS source_time FROM memory_units WHERE memory_set_id = ?
                UNION ALL
                SELECT occurred_at AS source_time FROM revisions WHERE memory_set_id = ?
                UNION ALL
                SELECT updated_at AS source_time FROM memory_links WHERE memory_set_id = ?
            )
            """,
            (memory_set_id, memory_set_id, memory_set_id),
        ).fetchone()
        value = row[0] if row is not None else None
        if isinstance(value, str) and value:
            return value
        return now_iso()

    def _ordered_relation_refs(self, refs: list[str]) -> tuple[str, str]:
        if "self" in refs:
            other = refs[0] if refs[1] == "self" else refs[1]
            return "self", other
        ordered = sorted(refs)
        return ordered[0], ordered[1]

    def _is_relation_ref(self, value: str) -> bool:
        return value in {"self", "user"} or any(
            value.startswith(prefix) and value != prefix
            for prefix in RELATION_REF_PREFIXES
        )

    def _unique_relation_refs(self, values: list[str]) -> list[str]:
        return [value for value in self._relation_unique_texts(values) if self._is_relation_ref(value)]

    def _relation_unique_texts(self, values: list[Any]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            text = value.strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    def _relation_timestamp_key(self, value: Any) -> float:
        if not isinstance(value, str) or not value:
            return float("-inf")
        return parse_iso(value).timestamp()

    def _relation_support_sort_key(self, unit: dict[str, Any]) -> tuple[bool, bool, float, float, float, str]:
        # 支持IDの順序も想起優先度と同じ決定規則に固定する。
        return (
            unit.get("status") in ACTIVE_MEMORY_STATUSES,
            unit.get("status") == CONFIRMED_MEMORY_STATUS,
            clamp_score(unit.get("salience")),
            clamp_score(unit.get("confidence")),
            self._relation_timestamp_key(unit.get("last_confirmed_at") or unit.get("formed_at")),
            str(unit.get("memory_unit_id") or ""),
        )

    def _empty_relation_recall_result(self) -> dict[str, Any]:
        return {
            "candidates": [],
            "requested_entity_refs": [],
            "matched_edge_count": 0,
            "used_edge_ids": [],
            "status_counts": {"active": 0, "weak": 0},
            "stale_memory_unit_ids": [],
        }
