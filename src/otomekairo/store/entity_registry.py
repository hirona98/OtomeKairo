from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from typing import Any

from otomekairo.memory.utils import clamp_score


NAMED_ENTITY_PREFIXES = ("person:", "place:", "tool:")
ENTITY_REGISTRY_EVIDENCE_LIMIT = 24
ENTITY_REGISTRY_SUPPORT_LIMIT = 24


class StoreEntityRegistryMixin:
    def update_entity_registry_from_turn(
        self,
        *,
        memory_set_id: str,
        observed_at: str,
        recall_hint: dict[str, Any] | None = None,
        episode: dict[str, Any] | None = None,
        memory_actions: list[dict[str, Any]] | None = None,
        episode_affects: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        # 入力から型付き entity ref だけを集める。
        observations = self._entity_registry_observations(
            memory_set_id=memory_set_id,
            observed_at=observed_at,
            recall_hint=recall_hint,
            episode=episode,
            memory_actions=memory_actions or [],
            episode_affects=episode_affects or [],
        )
        if not observations:
            return self._entity_registry_update_summary([])

        # registry 更新は通常記憶保存の後に別トランザクションで行う。
        updated_records: list[dict[str, Any]] = []
        with self._memory_db() as conn:
            for observation in observations:
                updated_records.append(self._upsert_entity_registry_observation(conn, observation))

        return self._entity_registry_update_summary(updated_records)

    def resolve_entity_refs(
        self,
        *,
        memory_set_id: str,
        entity_refs: list[str],
    ) -> dict[str, str]:
        # 正規化
        normalized_refs = self._normalized_entity_ref_list(entity_refs)
        if not normalized_refs:
            return {}

        # クエリ
        resolved: dict[str, str] = {}
        with self._memory_db() as conn:
            for entity_ref in normalized_refs:
                if self._load_entity_registry_record(
                    conn,
                    memory_set_id=memory_set_id,
                    entity_ref=entity_ref,
                ) is not None:
                    resolved[entity_ref] = entity_ref
                    continue
                canonical_ref = self._resolve_entity_ref(conn, memory_set_id=memory_set_id, entity_ref=entity_ref)
                if canonical_ref != entity_ref:
                    resolved[entity_ref] = canonical_ref

        return resolved

    def list_entity_registry_records(self, *, memory_set_id: str, limit: int = 50) -> list[dict[str, Any]]:
        # inspection と検証用
        with self._memory_db() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM entity_registry
                WHERE memory_set_id = ?
                ORDER BY last_seen_at DESC, salience DESC, rowid DESC
                LIMIT ?
                """,
                (memory_set_id, max(1, int(limit))),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def _entity_registry_observations(
        self,
        *,
        memory_set_id: str,
        observed_at: str,
        recall_hint: dict[str, Any] | None,
        episode: dict[str, Any] | None,
        memory_actions: list[dict[str, Any]],
        episode_affects: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # 観測候補
        observations_by_ref: dict[str, dict[str, Any]] = {}
        default_event_ids = self._entity_registry_episode_event_ids(episode)

        # RecallHint
        if isinstance(recall_hint, dict):
            for entity_ref in self._extract_named_entity_refs(recall_hint.get("mentioned_entities")):
                self._merge_entity_registry_observation(
                    observations_by_ref,
                    memory_set_id=memory_set_id,
                    observed_at=observed_at,
                    entity_ref=entity_ref,
                    evidence_event_ids=default_event_ids,
                    supporting_memory_unit_ids=[],
                    confidence=0.5,
                    salience=0.35,
                    source_kinds=["recall_hint"],
                )

        # episode
        if isinstance(episode, dict) and episode.get("primary_scope_type") == "entity":
            entity_ref = episode.get("primary_scope_key")
            if isinstance(entity_ref, str) and self._is_named_entity_ref(entity_ref):
                self._merge_entity_registry_observation(
                    observations_by_ref,
                    memory_set_id=memory_set_id,
                    observed_at=observed_at,
                    entity_ref=entity_ref,
                    evidence_event_ids=self._entity_registry_episode_event_ids(episode),
                    supporting_memory_unit_ids=[],
                    confidence=0.58,
                    salience=float(episode.get("salience", 0.35) or 0.35),
                    source_kinds=["episode"],
                )

        # memory_units
        for action in memory_actions:
            memory_unit = action.get("memory_unit")
            if not isinstance(memory_unit, dict):
                continue
            event_ids = [
                event_id
                for event_id in memory_unit.get("evidence_event_ids", [])
                if isinstance(event_id, str) and event_id
            ]
            supporting_memory_unit_ids = [
                memory_unit["memory_unit_id"]
            ] if isinstance(memory_unit.get("memory_unit_id"), str) and memory_unit["memory_unit_id"] else []
            for entity_ref in self._extract_named_entity_refs_from_memory_unit(memory_unit):
                self._merge_entity_registry_observation(
                    observations_by_ref,
                    memory_set_id=memory_set_id,
                    observed_at=observed_at,
                    entity_ref=entity_ref,
                    evidence_event_ids=event_ids,
                    supporting_memory_unit_ids=supporting_memory_unit_ids,
                    confidence=float(memory_unit.get("confidence", 0.58) or 0.58),
                    salience=float(memory_unit.get("salience", 0.35) or 0.35),
                    source_kinds=["memory_unit"],
                )

        # episode_affects
        for affect in episode_affects:
            if not isinstance(affect, dict) or affect.get("target_scope_type") != "entity":
                continue
            entity_ref = affect.get("target_scope_key")
            if not isinstance(entity_ref, str) or not self._is_named_entity_ref(entity_ref):
                continue
            self._merge_entity_registry_observation(
                observations_by_ref,
                memory_set_id=memory_set_id,
                observed_at=observed_at,
                entity_ref=entity_ref,
                evidence_event_ids=default_event_ids,
                supporting_memory_unit_ids=[],
                confidence=float(affect.get("confidence", 0.5) or 0.5),
                salience=float(affect.get("intensity", 0.35) or 0.35),
                source_kinds=["episode_affect"],
            )

        return list(observations_by_ref.values())

    def _merge_entity_registry_observation(
        self,
        observations_by_ref: dict[str, dict[str, Any]],
        *,
        memory_set_id: str,
        observed_at: str,
        entity_ref: str,
        evidence_event_ids: list[str],
        supporting_memory_unit_ids: list[str],
        confidence: float,
        salience: float,
        source_kinds: list[str],
    ) -> None:
        # 同じ typed ref の観測を 1 件へ集約する。
        normalized_ref = entity_ref.strip()
        existing = observations_by_ref.get(normalized_ref)
        if existing is None:
            observations_by_ref[normalized_ref] = {
                "memory_set_id": memory_set_id,
                "entity_ref": normalized_ref,
                "entity_type": self._entity_type_from_ref(normalized_ref),
                "observed_at": observed_at,
                "confidence": clamp_score(confidence),
                "salience": clamp_score(salience),
                "evidence_event_ids": self._unique_texts(evidence_event_ids, limit=ENTITY_REGISTRY_EVIDENCE_LIMIT),
                "supporting_memory_unit_ids": self._unique_texts(
                    supporting_memory_unit_ids,
                    limit=ENTITY_REGISTRY_SUPPORT_LIMIT,
                ),
                "source_kinds": self._unique_texts(source_kinds, limit=8),
            }
            return

        existing["confidence"] = max(clamp_score(existing.get("confidence")), clamp_score(confidence))
        existing["salience"] = max(clamp_score(existing.get("salience")), clamp_score(salience))
        existing["evidence_event_ids"] = self._unique_texts(
            [*existing.get("evidence_event_ids", []), *evidence_event_ids],
            limit=ENTITY_REGISTRY_EVIDENCE_LIMIT,
        )
        existing["supporting_memory_unit_ids"] = self._unique_texts(
            [*existing.get("supporting_memory_unit_ids", []), *supporting_memory_unit_ids],
            limit=ENTITY_REGISTRY_SUPPORT_LIMIT,
        )
        existing["source_kinds"] = self._unique_texts(
            [*existing.get("source_kinds", []), *source_kinds],
            limit=8,
        )

    def _upsert_entity_registry_observation(
        self,
        conn: sqlite3.Connection,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        # canonical 解決
        memory_set_id = observation["memory_set_id"]
        observed_ref = observation["entity_ref"]
        canonical_ref = self._resolve_entity_ref(conn, memory_set_id=memory_set_id, entity_ref=observed_ref)
        existing = self._load_entity_registry_record(
            conn,
            memory_set_id=memory_set_id,
            entity_ref=canonical_ref,
        )

        # 新規 registry
        if existing is None:
            existing = {
                "entity_ref": canonical_ref,
                "memory_set_id": memory_set_id,
                "entity_type": self._entity_type_from_ref(canonical_ref),
                "display_name": self._display_name_from_entity_ref(canonical_ref),
                "aliases": [],
                "first_seen_at": observation["observed_at"],
                "last_seen_at": observation["observed_at"],
                "confidence": 0.0,
                "salience": 0.0,
                "evidence_event_ids": [],
                "supporting_memory_unit_ids": [],
                "payload": {},
            }

        # 更新
        aliases = self._unique_texts(
            [
                *existing.get("aliases", []),
                canonical_ref,
                observed_ref,
            ],
            limit=24,
        )
        record = {
            **existing,
            "entity_ref": canonical_ref,
            "memory_set_id": memory_set_id,
            "entity_type": self._entity_type_from_ref(canonical_ref),
            "display_name": existing.get("display_name") or self._display_name_from_entity_ref(canonical_ref),
            "aliases": aliases,
            "first_seen_at": min(str(existing.get("first_seen_at") or observation["observed_at"]), observation["observed_at"]),
            "last_seen_at": max(str(existing.get("last_seen_at") or observation["observed_at"]), observation["observed_at"]),
            "confidence": max(clamp_score(existing.get("confidence")), clamp_score(observation.get("confidence"))),
            "salience": max(clamp_score(existing.get("salience")), clamp_score(observation.get("salience"))),
            "evidence_event_ids": self._unique_texts(
                [
                    *existing.get("evidence_event_ids", []),
                    *observation.get("evidence_event_ids", []),
                ],
                limit=ENTITY_REGISTRY_EVIDENCE_LIMIT,
            ),
            "supporting_memory_unit_ids": self._unique_texts(
                [
                    *existing.get("supporting_memory_unit_ids", []),
                    *observation.get("supporting_memory_unit_ids", []),
                ],
                limit=ENTITY_REGISTRY_SUPPORT_LIMIT,
            ),
        }
        record["payload"] = {
            "source_kinds": self._unique_texts(
                [
                    *self._payload_source_kinds(existing.get("payload")),
                    *observation.get("source_kinds", []),
                ],
                limit=8,
            ),
            "alias_keys": [
                self._alias_key_for_entity_ref(alias)
                for alias in aliases
                if self._alias_key_for_entity_ref(alias)
            ],
        }

        self._upsert_entity_registry_record(conn, record)
        for alias in aliases:
            self._upsert_entity_alias(
                conn,
                memory_set_id=memory_set_id,
                entity_ref=canonical_ref,
                alias_text=alias,
                entity_type=record["entity_type"],
                confidence=record["confidence"],
                updated_at=record["last_seen_at"],
            )
        return record

    def _resolve_entity_ref(
        self,
        conn: sqlite3.Connection,
        *,
        memory_set_id: str,
        entity_ref: str,
    ) -> str:
        # exact registry がある場合はその参照を canonical とする。
        normalized_ref = entity_ref.strip()
        if not self._is_named_entity_ref(normalized_ref):
            return normalized_ref
        exact = self._load_entity_registry_record(
            conn,
            memory_set_id=memory_set_id,
            entity_ref=normalized_ref,
        )
        if exact is not None:
            return normalized_ref

        # alias lookup が単一 canonical を返す場合だけ寄せる。
        alias_key = self._alias_key_for_entity_ref(normalized_ref)
        if not alias_key:
            return normalized_ref
        rows = conn.execute(
            """
            SELECT entity_ref
            FROM entity_aliases
            WHERE memory_set_id = ?
              AND alias_key = ?
            ORDER BY confidence DESC, updated_at DESC, rowid DESC
            LIMIT 3
            """,
            (memory_set_id, alias_key),
        ).fetchall()
        entity_refs = [
            str(row["entity_ref"])
            for row in rows
            if isinstance(row["entity_ref"], str) and row["entity_ref"]
        ]
        unique_refs = list(dict.fromkeys(entity_refs))
        if len(unique_refs) == 1:
            return unique_refs[0]
        return normalized_ref

    def _load_entity_registry_record(
        self,
        conn: sqlite3.Connection,
        *,
        memory_set_id: str,
        entity_ref: str,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            """
            SELECT payload_json
            FROM entity_registry
            WHERE memory_set_id = ?
              AND entity_ref = ?
            """,
            (memory_set_id, entity_ref),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        return payload if isinstance(payload, dict) else None

    def _upsert_entity_registry_record(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # 追加
        conn.execute(
            """
            INSERT OR REPLACE INTO entity_registry (
                entity_ref,
                memory_set_id,
                entity_type,
                display_name,
                aliases_json,
                first_seen_at,
                last_seen_at,
                confidence,
                salience,
                evidence_event_ids_json,
                supporting_memory_unit_ids_json,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["entity_ref"],
                record["memory_set_id"],
                record["entity_type"],
                record["display_name"],
                self._to_json(record.get("aliases", [])),
                record["first_seen_at"],
                record["last_seen_at"],
                record["confidence"],
                record["salience"],
                self._to_json(record.get("evidence_event_ids", [])),
                self._to_json(record.get("supporting_memory_unit_ids", [])),
                self._to_json(record),
            ),
        )

    def _upsert_entity_alias(
        self,
        conn: sqlite3.Connection,
        *,
        memory_set_id: str,
        entity_ref: str,
        alias_text: str,
        entity_type: str,
        confidence: float,
        updated_at: str,
    ) -> None:
        alias_key = self._alias_key_for_entity_ref(alias_text)
        if not alias_key:
            return
        record = {
            "memory_set_id": memory_set_id,
            "alias_key": alias_key,
            "entity_ref": entity_ref,
            "alias_text": alias_text,
            "entity_type": entity_type,
            "confidence": clamp_score(confidence),
            "updated_at": updated_at,
        }
        conn.execute(
            """
            INSERT OR REPLACE INTO entity_aliases (
                memory_set_id,
                alias_key,
                entity_ref,
                alias_text,
                entity_type,
                confidence,
                updated_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["memory_set_id"],
                record["alias_key"],
                record["entity_ref"],
                record["alias_text"],
                record["entity_type"],
                record["confidence"],
                record["updated_at"],
                self._to_json(record),
            ),
        )

    def _extract_named_entity_refs_from_memory_unit(self, memory_unit: dict[str, Any]) -> list[str]:
        # memory_unit 内の typed ref を収集する。
        values: list[Any] = []
        if memory_unit.get("scope_type") == "entity":
            values.append(memory_unit.get("scope_key"))
        values.extend(
            [
                memory_unit.get("subject_ref"),
                memory_unit.get("object_ref_or_value"),
                memory_unit.get("qualifiers"),
            ]
        )
        return self._extract_named_entity_refs(values)

    def _extract_named_entity_refs(self, value: Any) -> list[str]:
        # 再帰抽出
        refs: list[str] = []
        if isinstance(value, str):
            stripped = value.strip()
            if self._is_named_entity_ref(stripped):
                refs.append(stripped)
            if "|" in stripped:
                for part in stripped.split("|"):
                    part = part.strip()
                    if self._is_named_entity_ref(part):
                        refs.append(part)
        elif isinstance(value, dict):
            for child in value.values():
                refs.extend(self._extract_named_entity_refs(child))
        elif isinstance(value, list):
            for child in value:
                refs.extend(self._extract_named_entity_refs(child))
        return self._normalized_entity_ref_list(refs)

    def _normalized_entity_ref_list(self, values: list[str]) -> list[str]:
        # 重複排除
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                continue
            entity_ref = value.strip()
            if not self._is_named_entity_ref(entity_ref) or entity_ref in seen:
                continue
            normalized.append(entity_ref)
            seen.add(entity_ref)
        return normalized

    def _entity_registry_episode_event_ids(self, episode: dict[str, Any] | None) -> list[str]:
        # episode 根拠
        if not isinstance(episode, dict):
            return []
        return [
            event_id
            for event_id in episode.get("linked_event_ids", [])
            if isinstance(event_id, str) and event_id
        ]

    def _entity_registry_update_summary(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        # 更新サマリ
        entity_refs = self._unique_texts(
            [
                record.get("entity_ref")
                for record in records
                if isinstance(record.get("entity_ref"), str)
            ],
            limit=24,
        )
        return {
            "result_status": "updated" if entity_refs else "no_change",
            "entity_count": len(entity_refs),
            "entity_refs": entity_refs,
            "failure_reason": None,
        }

    def _payload_source_kinds(self, payload: Any) -> list[str]:
        # payload から source_kinds を読む。
        if not isinstance(payload, dict):
            return []
        source_kinds = payload.get("source_kinds")
        if not isinstance(source_kinds, list):
            return []
        return [
            value
            for value in source_kinds
            if isinstance(value, str) and value
        ]

    def _is_named_entity_ref(self, value: str) -> bool:
        # 型付き entity ref
        return any(value.startswith(prefix) and value != prefix for prefix in NAMED_ENTITY_PREFIXES)

    def _entity_type_from_ref(self, entity_ref: str) -> str:
        # entity type
        prefix, _, _ = entity_ref.partition(":")
        return prefix

    def _display_name_from_entity_ref(self, entity_ref: str) -> str:
        # 表示名
        _, _, suffix = entity_ref.partition(":")
        return suffix.strip() or entity_ref

    def _alias_key_for_entity_ref(self, entity_ref: str) -> str | None:
        # alias lookup 用正規化
        if not self._is_named_entity_ref(entity_ref):
            return None
        entity_type, _, suffix = entity_ref.partition(":")
        normalized_suffix = unicodedata.normalize("NFKC", suffix).strip().casefold()
        normalized_suffix = re.sub(r"\s+", "_", normalized_suffix)
        normalized_suffix = re.sub(r"_+", "_", normalized_suffix).strip("_")
        if not normalized_suffix:
            return None
        return f"{entity_type}:{normalized_suffix}"

    def _unique_texts(self, values: list[Any], *, limit: int) -> list[str]:
        # 順序維持重複排除
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                continue
            text = value.strip()
            if not text or text in seen:
                continue
            normalized.append(text)
            seen.add(text)
            if len(normalized) >= limit:
                break
        return normalized
