from __future__ import annotations

from typing import Any

from otomekairo.llm import LLMClient
from otomekairo.memory_utils import normalized_text_list
from otomekairo.recall_association import (
    ACTIVE_COMMITMENT_STATES,
    ACTIVE_MEMORY_STATUSES,
    RecallAssociationMixin,
)
from otomekairo.recall_event_evidence import RecallEventEvidenceMixin
from otomekairo.recall_selection import SECTION_LIMITS, RecallPackSelectionError, RecallSelectionMixin
from otomekairo.store import FileStore


# recall構築
class RecallBuilder(RecallSelectionMixin, RecallAssociationMixin, RecallEventEvidenceMixin):
    def __init__(self, *, store: FileStore, llm: LLMClient) -> None:
        # 依存関係
        self.store = store
        self.llm = llm

    def build_recall_pack(
        self,
        *,
        state: dict[str, Any],
        observation_text: str,
        recall_hint: dict[str, Any],
    ) -> dict[str, Any]:
        # コンテキスト
        memory_set_id = state["selected_memory_set_id"]
        primary_intent = recall_hint["primary_intent"]
        scope_context = self._build_scope_context(recall_hint)
        raw_candidate_ids: set[str] = set()

        # 有効なcommitment群
        active_commitments = self._limit_memory_section(
            raw_items=self._build_active_commitments(memory_set_id=memory_set_id),
            limit=SECTION_LIMITS["active_commitments"],
        )
        self._collect_raw_candidate_ids(raw_candidate_ids, active_commitments)

        # 関係モデル
        relationship_model = self._limit_memory_section(
            raw_items=self._build_scope_memory_section(
                memory_set_id=memory_set_id,
                scope_filters=scope_context["relationship_filters"],
                limit=SECTION_LIMITS["relationship_model"] * 3,
                exclude_memory_types=["commitment"],
            ),
            limit=SECTION_LIMITS["relationship_model"],
        )
        self._collect_raw_candidate_ids(raw_candidate_ids, relationship_model)

        # ユーザーモデル
        user_model = self._limit_memory_section(
            raw_items=self._build_scope_memory_section(
                memory_set_id=memory_set_id,
                scope_filters=scope_context["user_filters"],
                limit=SECTION_LIMITS["user_model"] * 3,
                exclude_memory_types=["commitment"],
            ),
            limit=SECTION_LIMITS["user_model"],
        )
        self._collect_raw_candidate_ids(raw_candidate_ids, user_model)

        # 自己モデル
        self_model = self._limit_memory_section(
            raw_items=self._build_scope_memory_section(
                memory_set_id=memory_set_id,
                scope_filters=scope_context["self_filters"],
                limit=SECTION_LIMITS["self_model"] * 3,
                exclude_memory_types=["commitment"],
            ),
            limit=SECTION_LIMITS["self_model"],
        )
        self._collect_raw_candidate_ids(raw_candidate_ids, self_model)

        # 有効なトピック群
        active_topics = self._limit_mixed_section(
            raw_items=self._build_active_topics(
                memory_set_id=memory_set_id,
                topic_scope_filters=scope_context["topic_filters"],
            ),
            limit=SECTION_LIMITS["active_topics"],
        )
        self._collect_raw_candidate_ids(raw_candidate_ids, active_topics)

        # エピソード根拠
        episodic_evidence = self._limit_episode_section(
            raw_items=self._build_episodic_evidence(
                memory_set_id=memory_set_id,
                scope_filters=scope_context["episode_scope_filters"],
                primary_intent=primary_intent,
            ),
            limit=SECTION_LIMITS["episodic_evidence"],
        )
        self._collect_raw_candidate_ids(raw_candidate_ids, episodic_evidence)

        # 関連セクション群
        association_sections = self._build_association_sections(
            state=state,
            observation_text=observation_text,
            recall_hint=recall_hint,
            scope_context=scope_context,
        )
        self._collect_raw_candidate_ids(raw_candidate_ids, association_sections["self_model"])
        self._collect_raw_candidate_ids(raw_candidate_ids, association_sections["user_model"])
        self._collect_raw_candidate_ids(raw_candidate_ids, association_sections["relationship_model"])
        self._collect_raw_candidate_ids(raw_candidate_ids, association_sections["active_topics"])
        self._collect_raw_candidate_ids(raw_candidate_ids, association_sections["episodic_evidence"])

        # 関連統合
        self_model = self._limit_memory_section(
            raw_items=self_model + association_sections["self_model"],
            limit=SECTION_LIMITS["self_model"],
        )
        user_model = self._limit_memory_section(
            raw_items=user_model + association_sections["user_model"],
            limit=SECTION_LIMITS["user_model"],
        )
        relationship_model = self._limit_memory_section(
            raw_items=relationship_model + association_sections["relationship_model"],
            limit=SECTION_LIMITS["relationship_model"],
        )
        active_topics = self._limit_mixed_section(
            raw_items=active_topics + association_sections["active_topics"],
            limit=SECTION_LIMITS["active_topics"],
        )
        episodic_evidence = self._limit_episode_section(
            raw_items=episodic_evidence + association_sections["episodic_evidence"],
            limit=SECTION_LIMITS["episodic_evidence"],
        )

        # 競合元
        selected_memory_items = active_commitments + relationship_model + user_model + self_model

        # 競合群
        conflicts = self._build_conflicts(
            memory_set_id=memory_set_id,
            selected_memory_items=selected_memory_items,
        )

        # RecallPack 選別
        recall_pack_selection_role = state["model_presets"][state["selected_model_preset_id"]]["roles"][
            "recall_pack_selection"
        ]
        selection_result = self._select_recall_pack_sections(
            observation_text=observation_text,
            recall_hint=recall_hint,
            candidate_sections={
                "self_model": self_model,
                "user_model": user_model,
                "relationship_model": relationship_model,
                "active_topics": active_topics,
                "active_commitments": active_commitments,
                "episodic_evidence": episodic_evidence,
            },
            conflicts=conflicts,
            role_definition=recall_pack_selection_role,
        )
        sections = selection_result["sections"]

        # 選択要約
        selected_memory_ids = self._collect_selected_ids(sections, key="memory_unit_id")
        selected_episode_ids = self._collect_selected_ids(sections, key="episode_id")
        association_selected_memory_ids = self._collect_selected_ids(
            sections,
            key="memory_unit_id",
            retrieval_lane="association",
        )
        association_selected_episode_ids = self._collect_selected_ids(
            sections,
            key="episode_id",
            retrieval_lane="association",
        )
        event_evidence_role = state["model_presets"][state["selected_model_preset_id"]]["roles"]["event_evidence_generation"]
        event_evidence_result = self._build_event_evidence(
            memory_set_id=memory_set_id,
            primary_intent=primary_intent,
            recall_hint=recall_hint,
            sections=sections,
            role_definition=event_evidence_role,
        )
        event_evidence = event_evidence_result["event_evidence"]
        selected_event_ids = event_evidence_result["selected_event_ids"]

        # 結果
        return {
            **sections,
            "event_evidence": event_evidence,
            "event_evidence_generation": event_evidence_result["event_evidence_generation"],
            "selected_memory_ids": selected_memory_ids,
            "selected_episode_ids": selected_episode_ids,
            "association_selected_memory_ids": association_selected_memory_ids,
            "association_selected_episode_ids": association_selected_episode_ids,
            "selected_event_ids": selected_event_ids,
            "recall_pack_selection": selection_result["recall_pack_selection"],
            "candidate_count": len(raw_candidate_ids),
        }

    def _build_scope_context(self, recall_hint: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
        # focus scope群
        focus_specs = self._parse_focus_scopes(recall_hint.get("focus_scopes", []))
        primary_intent = recall_hint["primary_intent"]

        # 基底scope群
        user_filters = self._merged_scope_filters([("user", "user")], focus_specs, allowed_scope_type="user")
        self_filters = self._merged_scope_filters([("self", "self")], focus_specs, allowed_scope_type="self")
        relationship_defaults = [("relationship", "self|user")]
        relationship_filters = self._merged_scope_filters(
            relationship_defaults if primary_intent in {"commitment_check", "consult", "meta_relationship"} else [],
            focus_specs,
            allowed_scope_type="relationship",
        )
        topic_filters = self._merged_scope_filters([], focus_specs, allowed_scope_type="topic")
        episode_scope_filters = self._merged_scope_filters(
            user_filters + relationship_filters + self_filters + topic_filters,
            [],
            allowed_scope_type=None,
        )

        # 結果
        return {
            "user_filters": user_filters,
            "self_filters": self_filters,
            "relationship_filters": relationship_filters,
            "topic_filters": topic_filters,
            "episode_scope_filters": episode_scope_filters,
        }

    def _build_active_commitments(self, *, memory_set_id: str) -> list[dict[str, Any]]:
        # クエリ
        records = self.store.list_memory_units_for_recall(
            memory_set_id=memory_set_id,
            include_memory_types=["commitment"],
            statuses=list(ACTIVE_MEMORY_STATUSES),
            commitment_states=list(ACTIVE_COMMITMENT_STATES),
            limit=SECTION_LIMITS["active_commitments"] * 3,
        )

        # 結果
        return [self._to_memory_item(record) for record in records]

    def _build_scope_memory_section(
        self,
        *,
        memory_set_id: str,
        scope_filters: list[tuple[str, str]],
        limit: int,
        exclude_memory_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        # 空
        if not scope_filters:
            return []

        # クエリ
        records = self.store.list_memory_units_for_recall(
            memory_set_id=memory_set_id,
            scope_filters=scope_filters,
            exclude_memory_types=exclude_memory_types,
            statuses=list(ACTIVE_MEMORY_STATUSES),
            limit=limit,
        )

        # 結果
        return [self._to_memory_item(record) for record in records]

    def _build_active_topics(
        self,
        *,
        memory_set_id: str,
        topic_scope_filters: list[tuple[str, str]],
    ) -> list[dict[str, Any]]:
        # トピック記憶
        topic_records = self.store.list_memory_units_for_recall(
            memory_set_id=memory_set_id,
            scope_filters=topic_scope_filters or None,
            scope_types=["topic"],
            statuses=list(ACTIVE_MEMORY_STATUSES),
            limit=SECTION_LIMITS["active_topics"] * 2,
        )

        # トピック項目群
        items = [self._to_memory_item(record) for record in topic_records]

        # 未完了Loops
        episode_records = self.store.list_episodes_for_recall(
            memory_set_id=memory_set_id,
            scope_filters=[],
            require_open_loops=True,
            limit=SECTION_LIMITS["active_topics"] * 3,
        )
        items.extend(self._to_topic_episode_item(record) for record in episode_records)

        # 結果
        return items

    def _build_episodic_evidence(
        self,
        *,
        memory_set_id: str,
        scope_filters: list[tuple[str, str]],
        primary_intent: str,
    ) -> list[dict[str, Any]]:
        # クエリ
        records = self.store.list_episodes_for_recall(
            memory_set_id=memory_set_id,
            scope_filters=scope_filters,
            require_open_loops=primary_intent == "commitment_check",
            limit=SECTION_LIMITS["episodic_evidence"] * 4,
        )

        # 結果
        return [self._to_episode_item(record) for record in records]

    def _section_priority(self, recall_hint: dict[str, Any]) -> list[str]:
        # 主順序
        primary_intent = recall_hint["primary_intent"]
        ordered = self._primary_section_priority(primary_intent)

        # 副次補正群
        boosted_sections = self._secondary_section_boosts(
            self._secondary_intents(recall_hint),
        )

        # 統合
        merged: list[str] = []
        if ordered:
            merged.append(ordered[0])
        for section_name in boosted_sections:
            if section_name not in merged:
                merged.append(section_name)
        for section_name in ordered:
            if section_name not in merged:
                merged.append(section_name)

        # 結果
        return merged

    def _primary_section_priority(self, primary_intent: str) -> list[str]:
        # マッピング
        if primary_intent == "commitment_check":
            return [
                "active_commitments",
                "relationship_model",
                "episodic_evidence",
                "user_model",
                "active_topics",
                "self_model",
            ]
        if primary_intent == "meta_relationship":
            return [
                "relationship_model",
                "user_model",
                "episodic_evidence",
                "active_commitments",
                "active_topics",
                "self_model",
            ]
        if primary_intent == "consult":
            return [
                "user_model",
                "relationship_model",
                "active_topics",
                "episodic_evidence",
                "active_commitments",
                "self_model",
            ]
        if primary_intent == "reminisce":
            return [
                "episodic_evidence",
                "active_topics",
                "user_model",
                "relationship_model",
                "active_commitments",
                "self_model",
            ]
        if primary_intent == "check_state":
            return [
                "user_model",
                "active_topics",
                "relationship_model",
                "episodic_evidence",
                "active_commitments",
                "self_model",
            ]
        return [
            "user_model",
            "relationship_model",
            "active_topics",
            "active_commitments",
            "episodic_evidence",
            "self_model",
        ]

    def _secondary_section_boosts(self, secondary_intents: list[str]) -> list[str]:
        # 状態
        boosted: list[str] = []

        # 収集
        for intent in secondary_intents:
            for section_name in self._primary_section_priority(intent)[:2]:
                if section_name not in boosted:
                    boosted.append(section_name)

        # 結果
        return boosted

    def _secondary_intents(self, recall_hint: dict[str, Any]) -> list[str]:
        # 正規化
        secondary_intents = normalized_text_list(
            recall_hint.get("secondary_intents", []),
            limit=2,
        )

        # 結果
        return secondary_intents

    def _limit_memory_section(
        self,
        *,
        raw_items: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        # 選択
        selected: list[dict[str, Any]] = []
        seen_record_ids: set[str] = set()
        summary_count = 0
        for item in raw_items:
            record_id = item["memory_unit_id"]
            if record_id in seen_record_ids:
                continue
            if item["memory_type"] == "summary":
                if summary_count >= 1:
                    continue
                summary_count += 1
            selected.append(item)
            seen_record_ids.add(record_id)
            if len(selected) >= limit:
                break

        # 結果
        return selected

    def _limit_episode_section(
        self,
        *,
        raw_items: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        # 選択
        selected: list[dict[str, Any]] = []
        seen_record_ids: set[str] = set()
        for item in raw_items:
            record_id = item["episode_id"]
            if record_id in seen_record_ids:
                continue
            selected.append(item)
            seen_record_ids.add(record_id)
            if len(selected) >= limit:
                break

        # 結果
        return selected

    def _limit_mixed_section(
        self,
        *,
        raw_items: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        # 選択
        selected: list[dict[str, Any]] = []
        seen_record_ids: set[str] = set()
        summary_count = 0
        for item in raw_items:
            record_id = self._record_id(item)
            if record_id in seen_record_ids:
                continue
            if item.get("memory_type") == "summary":
                if summary_count >= 1:
                    continue
                summary_count += 1
            selected.append(item)
            seen_record_ids.add(record_id)
            if len(selected) >= limit:
                break

        # 結果
        return selected

    def _parse_focus_scopes(self, scopes: list[Any]) -> list[tuple[str, str]]:
        # 状態
        parsed: list[tuple[str, str]] = []

        # 解析
        for scope in scopes:
            if not isinstance(scope, str):
                continue
            normalized = scope.strip()
            if not normalized:
                continue
            if normalized in {"self", "user"}:
                parsed.append((normalized, normalized))
                continue
            scope_type, separator, scope_key = normalized.partition(":")
            if not separator or not scope_key:
                continue
            if scope_type not in {"relationship", "topic"}:
                continue
            parsed.append((scope_type, scope_key.strip()))

        # 結果
        return parsed

    def _merged_scope_filters(
        self,
        defaults: list[tuple[str, str]],
        focus_specs: list[tuple[str, str]],
        *,
        allowed_scope_type: str | None,
    ) -> list[tuple[str, str]]:
        # 状態
        merged: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        # 既定値
        for scope_filter in defaults:
            if scope_filter in seen:
                continue
            merged.append(scope_filter)
            seen.add(scope_filter)

        # focus仕様群
        for scope_filter in focus_specs:
            if allowed_scope_type is not None and scope_filter[0] != allowed_scope_type:
                continue
            if scope_filter in seen:
                continue
            merged.append(scope_filter)
            seen.add(scope_filter)

        # 結果
        return merged

    def _empty_association_sections(self) -> dict[str, list[dict[str, Any]]]:
        # 結果
        return {
            "self_model": [],
            "user_model": [],
            "relationship_model": [],
            "active_topics": [],
            "episodic_evidence": [],
        }

    def _empty_recall_pack(self) -> dict[str, Any]:
        # 結果
        return {
            "self_model": [],
            "user_model": [],
            "relationship_model": [],
            "active_topics": [],
            "active_commitments": [],
            "episodic_evidence": [],
            "event_evidence": [],
            "event_evidence_generation": {
                "requested_event_count": 0,
                "loaded_event_count": 0,
                "succeeded_event_count": 0,
                "failed_items": [],
            },
            "recall_pack_selection": self._empty_recall_pack_selection(),
            "conflicts": [],
            "selected_memory_ids": [],
            "selected_episode_ids": [],
            "association_selected_memory_ids": [],
            "association_selected_episode_ids": [],
            "selected_event_ids": [],
            "candidate_count": 0,
        }

    def _embedding_dimension(self, definition: dict[str, Any]) -> int:
        embedding_dimension = definition.get("embedding_dimension")
        if not isinstance(embedding_dimension, int) or embedding_dimension <= 0:
            raise ValueError("memory_set.embedding.embedding_dimension must be a positive integer.")
        return embedding_dimension

    def _collect_raw_candidate_ids(self, raw_candidate_ids: set[str], items: list[dict[str, Any]]) -> None:
        # 収集
        for item in items:
            raw_candidate_ids.add(self._record_id(item))

    def _collect_selected_ids(
        self,
        sections: dict[str, list[dict[str, Any]]],
        *,
        key: str,
        retrieval_lane: str | None = None,
    ) -> list[str]:
        # 状態
        selected: list[str] = []
        seen: set[str] = set()

        # 収集
        for section_items in sections.values():
            for item in section_items:
                if retrieval_lane is not None and item.get("retrieval_lane") != retrieval_lane:
                    continue
                value = item.get(key)
                if not isinstance(value, str) or value in seen:
                    continue
                selected.append(value)
                seen.add(value)

        # 結果
        return selected

    def _record_id(self, item: dict[str, Any]) -> str:
        # 記憶単位
        if "memory_unit_id" in item:
            return item["memory_unit_id"]

        # Episode要約
        return item["episode_id"]

    def _to_memory_item(self, record: dict[str, Any]) -> dict[str, Any]:
        # 項目化
        return {
            "source_kind": "memory_unit",
            "memory_unit_id": record["memory_unit_id"],
            "memory_type": record["memory_type"],
            "scope_type": record["scope_type"],
            "scope_key": record["scope_key"],
            "subject_ref": record["subject_ref"],
            "predicate": record["predicate"],
            "object_ref_or_value": record.get("object_ref_or_value"),
            "summary_text": record["summary_text"],
            "status": record["status"],
            "commitment_state": record.get("commitment_state"),
            "confidence": record["confidence"],
            "salience": record["salience"],
            "evidence_event_ids": record.get("evidence_event_ids", []),
        }

    def _to_episode_item(self, record: dict[str, Any]) -> dict[str, Any]:
        # 項目化
        return {
            "source_kind": "episode",
            "episode_id": record["episode_id"],
            "episode_type": record["episode_type"],
            "episode_series_id": record.get("episode_series_id"),
            "primary_scope_type": record["primary_scope_type"],
            "primary_scope_key": record["primary_scope_key"],
            "summary_text": record["summary_text"],
            "outcome_text": record.get("outcome_text"),
            "open_loops": record.get("open_loops", []),
            "has_open_loops": bool(record.get("open_loops")),
            "salience": record["salience"],
            "formed_at": record["formed_at"],
            "linked_event_ids": record.get("linked_event_ids", []),
        }

    def _to_topic_episode_item(self, record: dict[str, Any]) -> dict[str, Any]:
        # 未完了ループ要約
        open_loops = record.get("open_loops", [])
        summary_text = open_loops[0] if open_loops else record["summary_text"]

        # 項目化
        return {
            "source_kind": "episode",
            "episode_id": record["episode_id"],
            "episode_type": record["episode_type"],
            "episode_series_id": record.get("episode_series_id"),
            "primary_scope_type": record["primary_scope_type"],
            "primary_scope_key": record["primary_scope_key"],
            "summary_text": summary_text,
            "outcome_text": record.get("outcome_text"),
            "open_loops": open_loops,
            "has_open_loops": bool(open_loops),
            "salience": record["salience"],
            "formed_at": record["formed_at"],
            "linked_event_ids": record.get("linked_event_ids", []),
        }
