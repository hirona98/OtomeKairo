from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any


INACTIVE_MEMORY_LINK_TARGET_STATUSES = {"revoked", "superseded"}


class StoreMemoryLinksMixin:
    def persist_memory_actions(self, *, memory_actions: list[dict[str, Any]]) -> dict[str, Any]:
        # 空
        if not memory_actions:
            return self._memory_link_update_summary([])

        # トランザクション
        memory_link_records: list[dict[str, Any]] = []
        with self._memory_db() as conn:
            for action in memory_actions:
                memory_link_records.extend(self._apply_memory_action(conn, action))

        # 結果
        return self._memory_link_update_summary(memory_link_records)

    def list_memory_links_for_recall(
        self,
        *,
        memory_set_id: str,
        memory_unit_ids: list[str],
        limit_per_unit: int = 3,
        total_limit: int = 24,
    ) -> list[dict[str, Any]]:
        # 対象 memory_unit_id 群
        normalized_memory_unit_ids: list[str] = []
        seen_memory_unit_ids: set[str] = set()
        for value in memory_unit_ids:
            if not isinstance(value, str):
                continue
            normalized = value.strip()
            if not normalized or normalized in seen_memory_unit_ids:
                continue
            normalized_memory_unit_ids.append(normalized)
            seen_memory_unit_ids.add(normalized)
        if not normalized_memory_unit_ids:
            return []

        # 上限
        per_unit_limit = max(1, int(limit_per_unit))
        record_limit = max(1, int(total_limit))
        query_limit = max(record_limit * 4, len(normalized_memory_unit_ids) * per_unit_limit * 4, 16)
        selected_ids = set(normalized_memory_unit_ids)
        placeholders = ", ".join("?" for _ in normalized_memory_unit_ids)

        # クエリ
        with self._memory_db() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    link.payload_json AS link_payload_json,
                    source.payload_json AS source_payload_json,
                    target.payload_json AS target_payload_json
                FROM memory_links AS link
                LEFT JOIN memory_units AS source
                  ON source.memory_set_id = link.memory_set_id
                 AND source.memory_unit_id = link.source_memory_unit_id
                LEFT JOIN memory_units AS target
                  ON target.memory_set_id = link.memory_set_id
                 AND target.memory_unit_id = link.target_memory_unit_id
                WHERE link.memory_set_id = ?
                  AND (
                    link.source_memory_unit_id IN ({placeholders})
                    OR link.target_memory_unit_id IN ({placeholders})
                  )
                ORDER BY link.updated_at DESC, link.rowid DESC
                LIMIT ?
                """,
                (
                    memory_set_id,
                    *normalized_memory_unit_ids,
                    *normalized_memory_unit_ids,
                    query_limit,
                ),
            ).fetchall()

        # 整形
        records: list[dict[str, Any]] = []
        per_unit_counts: dict[str, int] = {memory_unit_id: 0 for memory_unit_id in normalized_memory_unit_ids}
        seen_link_ids: set[str] = set()
        for row in rows:
            link_payload = json.loads(row["link_payload_json"])
            if not isinstance(link_payload, dict):
                continue
            memory_link_id = str(link_payload.get("memory_link_id") or "").strip()
            if not memory_link_id or memory_link_id in seen_link_ids:
                continue
            source_memory_unit_id = str(link_payload.get("source_memory_unit_id") or "").strip()
            target_memory_unit_id = str(link_payload.get("target_memory_unit_id") or "").strip()
            related_selected_ids = [
                memory_unit_id
                for memory_unit_id in (source_memory_unit_id, target_memory_unit_id)
                if memory_unit_id in selected_ids
            ]
            if not related_selected_ids:
                continue
            if all(per_unit_counts[memory_unit_id] >= per_unit_limit for memory_unit_id in related_selected_ids):
                continue

            source_payload = self._loads_optional_payload(row["source_payload_json"])
            target_payload = self._loads_optional_payload(row["target_payload_json"])
            for memory_unit_id in related_selected_ids:
                per_unit_counts[memory_unit_id] += 1
            records.append(
                {
                    "memory_link_id": memory_link_id,
                    "memory_set_id": link_payload.get("memory_set_id"),
                    "source_memory_unit_id": source_memory_unit_id,
                    "target_memory_unit_id": target_memory_unit_id,
                    "label": link_payload.get("label"),
                    "confidence": link_payload.get("confidence"),
                    "evidence_revision_id": link_payload.get("evidence_revision_id"),
                    "created_at": link_payload.get("created_at"),
                    "updated_at": link_payload.get("updated_at"),
                    "operation": link_payload.get("operation"),
                    "reason": link_payload.get("reason"),
                    "selected_memory_unit_ids": related_selected_ids,
                    "source_memory_unit": self._compact_memory_unit_for_link_context(source_payload),
                    "target_memory_unit": self._compact_memory_unit_for_link_context(target_payload),
                }
            )
            seen_link_ids.add(memory_link_id)
            if len(records) >= record_limit:
                break

        # 結果
        return records

    def _loads_optional_payload(self, value: Any) -> dict[str, Any] | None:
        # 空値
        if value is None:
            return None

        # JSON
        payload = json.loads(value)
        return payload if isinstance(payload, dict) else None

    def _compact_memory_unit_for_link_context(self, payload: dict[str, Any] | None) -> dict[str, Any] | None:
        # 欠損
        if not isinstance(payload, dict):
            return None

        # 結果
        return {
            "memory_unit_id": payload.get("memory_unit_id"),
            "memory_type": payload.get("memory_type"),
            "scope_type": payload.get("scope_type"),
            "scope_key": payload.get("scope_key"),
            "summary_text": payload.get("summary_text"),
            "status": payload.get("status"),
            "confidence": payload.get("confidence"),
            "salience": payload.get("salience"),
        }

    def _apply_memory_action(self, conn: sqlite3.Connection, action: dict[str, Any]) -> list[dict[str, Any]]:
        # 操作読み取り
        operation = action["operation"]
        memory_unit = action.get("memory_unit")

        # 何もしない処理
        if operation == "noop":
            return []

        # memory unit upsert実行
        if memory_unit is not None:
            self._upsert_memory_unit(conn, memory_unit)

        # 改訂追加
        self._insert_revision(conn, action)

        # 関係追加
        return self._upsert_memory_links_from_action(conn, action)

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

    def _upsert_memory_links_from_action(
        self,
        conn: sqlite3.Connection,
        action: dict[str, Any],
    ) -> list[dict[str, Any]]:
        # related_memory_unit_ids は更新履歴の監査、memory_links は理解同士の関係を保持する。
        related_memory_unit_ids = [
            value
            for value in action.get("related_memory_unit_ids", [])
            if isinstance(value, str) and value and value != action.get("memory_unit_id")
        ]
        if not related_memory_unit_ids:
            return []

        operation = action.get("operation")
        records: list[dict[str, Any]] = []
        for target_memory_unit_id in related_memory_unit_ids:
            for link_spec in self._memory_link_specs_for_action(
                conn=conn,
                action=action,
                target_memory_unit_id=target_memory_unit_id,
            ):
                record = self._upsert_memory_link(
                    conn,
                    {
                        "memory_link_id": f"memory_link:{uuid.uuid4().hex}",
                        "memory_set_id": action["memory_set_id"],
                        "source_memory_unit_id": link_spec["source_memory_unit_id"],
                        "target_memory_unit_id": link_spec["target_memory_unit_id"],
                        "label": link_spec["label"],
                        "confidence": link_spec["confidence"],
                        "evidence_revision_id": action["revision_id"],
                        "created_at": action["occurred_at"],
                        "updated_at": action["occurred_at"],
                        "operation": operation,
                        "reason": action.get("reason"),
                    },
                )
                records.append(record)
        return records

    def _memory_link_specs_for_action(
        self,
        *,
        conn: sqlite3.Connection,
        action: dict[str, Any],
        target_memory_unit_id: str,
    ) -> list[dict[str, Any]]:
        # revision の関連 ID を、検索しやすい意味リンクへ最小展開する。
        operation = action.get("operation")
        source_memory_unit_id = action["memory_unit_id"]
        source_unit = action.get("memory_unit") if isinstance(action.get("memory_unit"), dict) else {}
        target_unit = self._load_memory_unit_for_link(
            conn,
            memory_set_id=action["memory_set_id"],
            memory_unit_id=target_memory_unit_id,
        )
        target_status = target_unit.get("status") if isinstance(target_unit, dict) else None

        specs: list[dict[str, Any]] = []
        if operation == "create":
            specs.append(
                self._memory_link_spec(
                    source_memory_unit_id=source_memory_unit_id,
                    target_memory_unit_id=target_memory_unit_id,
                    label="derived_from",
                    confidence=0.72,
                )
            )
            if target_status in INACTIVE_MEMORY_LINK_TARGET_STATUSES:
                specs.append(
                    self._memory_link_spec(
                        source_memory_unit_id=source_memory_unit_id,
                        target_memory_unit_id=target_memory_unit_id,
                        label="affects",
                        confidence=0.74,
                    )
                )
            elif target_unit is not None:
                specs.append(
                    self._memory_link_spec(
                        source_memory_unit_id=target_memory_unit_id,
                        target_memory_unit_id=source_memory_unit_id,
                        label="supports",
                        confidence=0.68,
                    )
                )
            if self._same_memory_link_scope(source_unit, target_unit):
                specs.append(
                    self._memory_link_spec(
                        source_memory_unit_id=source_memory_unit_id,
                        target_memory_unit_id=target_memory_unit_id,
                        label="about_same_scope",
                        confidence=0.62,
                    )
                )
        elif operation in {"revoke", "supersede"}:
            specs.append(
                self._memory_link_spec(
                    source_memory_unit_id=source_memory_unit_id,
                    target_memory_unit_id=target_memory_unit_id,
                    label="contradicts",
                    confidence=0.86,
                )
            )
        elif (
            operation in {"reinforce", "refine"}
            and target_unit is not None
            and target_status not in INACTIVE_MEMORY_LINK_TARGET_STATUSES
        ):
            specs.append(
                self._memory_link_spec(
                    source_memory_unit_id=source_memory_unit_id,
                    target_memory_unit_id=target_memory_unit_id,
                    label="derived_from",
                    confidence=0.7,
                )
            )
            specs.append(
                self._memory_link_spec(
                    source_memory_unit_id=target_memory_unit_id,
                    target_memory_unit_id=source_memory_unit_id,
                    label="supports",
                    confidence=0.66,
                )
            )
            if self._same_memory_link_scope(source_unit, target_unit):
                specs.append(
                    self._memory_link_spec(
                        source_memory_unit_id=source_memory_unit_id,
                        target_memory_unit_id=target_memory_unit_id,
                        label="about_same_scope",
                        confidence=0.6,
                    )
                )

        return [
            spec
            for spec in specs
            if spec["source_memory_unit_id"] != spec["target_memory_unit_id"]
        ]

    def _memory_link_spec(
        self,
        *,
        source_memory_unit_id: str,
        target_memory_unit_id: str,
        label: str,
        confidence: float,
    ) -> dict[str, Any]:
        # リンク仕様
        return {
            "source_memory_unit_id": source_memory_unit_id,
            "target_memory_unit_id": target_memory_unit_id,
            "label": label,
            "confidence": confidence,
        }

    def _load_memory_unit_for_link(
        self,
        conn: sqlite3.Connection,
        *,
        memory_set_id: str,
        memory_unit_id: str,
    ) -> dict[str, Any] | None:
        # related target の状態を使って supports/affects の境界を決める。
        row = conn.execute(
            """
            SELECT payload_json
            FROM memory_units
            WHERE memory_set_id = ?
              AND memory_unit_id = ?
            """,
            (memory_set_id, memory_unit_id),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        return payload if isinstance(payload, dict) else None

    def _same_memory_link_scope(
        self,
        source_unit: dict[str, Any] | None,
        target_unit: dict[str, Any] | None,
    ) -> bool:
        # 同一 scope の別理解だけを about_same_scope とする。
        if not isinstance(source_unit, dict) or not isinstance(target_unit, dict):
            return False
        return (
            isinstance(source_unit.get("scope_type"), str)
            and isinstance(source_unit.get("scope_key"), str)
            and source_unit.get("scope_type") == target_unit.get("scope_type")
            and source_unit.get("scope_key") == target_unit.get("scope_key")
        )

    def _upsert_memory_link(self, conn: sqlite3.Connection, record: dict[str, Any]) -> dict[str, Any]:
        # 既存検索
        existing_row = conn.execute(
            """
            SELECT memory_link_id, created_at
            FROM memory_links
            WHERE memory_set_id = ?
              AND source_memory_unit_id = ?
              AND target_memory_unit_id = ?
              AND label = ?
            """,
            (
                record["memory_set_id"],
                record["source_memory_unit_id"],
                record["target_memory_unit_id"],
                record["label"],
            ),
        ).fetchone()

        # 識別解決
        payload = dict(record)
        if existing_row is not None:
            payload["memory_link_id"] = existing_row["memory_link_id"]
            payload["created_at"] = existing_row["created_at"]

        # 保存
        conn.execute(
            """
            INSERT OR REPLACE INTO memory_links (
                memory_link_id,
                memory_set_id,
                source_memory_unit_id,
                target_memory_unit_id,
                label,
                confidence,
                evidence_revision_id,
                created_at,
                updated_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["memory_link_id"],
                payload["memory_set_id"],
                payload["source_memory_unit_id"],
                payload["target_memory_unit_id"],
                payload["label"],
                payload["confidence"],
                payload["evidence_revision_id"],
                payload["created_at"],
                payload["updated_at"],
                self._to_json(payload),
            ),
        )
        return payload

    def _memory_link_update_summary(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        # trace向け要約
        labels: dict[str, int] = {}
        memory_link_ids: list[str] = []
        for record in records:
            label = str(record.get("label") or "unknown")
            labels[label] = labels.get(label, 0) + 1
            memory_link_id = record.get("memory_link_id")
            if isinstance(memory_link_id, str) and memory_link_id:
                memory_link_ids.append(memory_link_id)
        return {
            "result_status": "updated" if records else "no_change",
            "link_count": len(records),
            "labels": labels,
            "memory_link_ids": memory_link_ids,
        }
