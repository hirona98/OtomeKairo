from __future__ import annotations

from typing import Any

from otomekairo.llm.client import LLMClient
from otomekairo.memory.utils import normalized_text_list
from otomekairo.recall.association import (
    ACTIVE_COMMITMENT_STATES,
    ACTIVE_MEMORY_STATUSES,
    RecallAssociationMixin,
)
from otomekairo.recall.event_evidence import RecallEventEvidenceMixin
from otomekairo.recall.selection import SECTION_LIMITS, RecallPackSelectionError, RecallSelectionMixin
from otomekairo.store.file_store import FileStore


MEMORY_LINK_RECALL_LABEL_PRIORITY = [
    "contradicts",
    "supports",
    "derived_from",
    "about_same_scope",
    "affects",
]
MEMORY_LINK_RECALL_HINT_LIMIT = 3
MEMORY_LINK_RECALL_TRACE_LIMIT = 8
VISUAL_OBSERVATION_RECALL_LIMIT = 3
VISUAL_DAILY_DIGEST_RECALL_LIMIT = 2


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
        augmented_query_text: str,
        recall_hint: dict[str, Any],
    ) -> dict[str, Any]:
        # コンテキスト
        memory_set_id = state["selected_memory_set_id"]
        primary_recall_focus = recall_hint["primary_recall_focus"]
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
            )
            + self._build_scope_memory_section(
                memory_set_id=memory_set_id,
                scope_filters=(
                    scope_context["entity_filters"]
                    + scope_context["world_filters"]
                ),
                limit=SECTION_LIMITS["active_topics"] * 2,
                exclude_memory_types=["commitment"],
            ),
            limit=SECTION_LIMITS["active_topics"],
        )
        self._collect_raw_candidate_ids(raw_candidate_ids, active_topics)

        # エピソード根拠
        episodic_evidence = self._limit_episode_section(
            raw_items=self._build_episodic_evidence(
                memory_set_id=memory_set_id,
                scope_filters=scope_context["episode_scope_filters"],
                primary_recall_focus=primary_recall_focus,
            ),
            limit=SECTION_LIMITS["episodic_evidence"],
        )
        self._collect_raw_candidate_ids(raw_candidate_ids, episodic_evidence)

        # 関連セクション群
        association_sections = self._build_association_sections(
            state=state,
            input_text=augmented_query_text,
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

        # 選別候補
        candidate_sections = {
            "self_model": self_model,
            "user_model": user_model,
            "relationship_model": relationship_model,
            "active_topics": active_topics,
            "active_commitments": active_commitments,
            "episodic_evidence": episodic_evidence,
        }

        # relation 補助情報
        candidate_memory_links = self.store.list_memory_links_for_recall(
            memory_set_id=memory_set_id,
            memory_unit_ids=self._collect_selected_ids(candidate_sections, key="memory_unit_id"),
            limit_per_unit=2,
            total_limit=40,
        )
        self._attach_memory_link_summaries_to_sections(
            sections=candidate_sections,
            memory_links=candidate_memory_links,
        )

        # RecallPack 選別
        recall_pack_selection_role = state["model_presets"][state["selected_model_preset_id"]]["roles"][
            "recall_pack_selection"
        ]
        selection_result = self._select_recall_pack_sections(
            augmented_query_text=augmented_query_text,
            recall_hint=recall_hint,
            candidate_sections=candidate_sections,
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
        memory_links = self.store.list_memory_links_for_recall(
            memory_set_id=memory_set_id,
            memory_unit_ids=selected_memory_ids,
            limit_per_unit=3,
            total_limit=24,
        )
        self._attach_memory_link_summaries_to_sections(
            sections=sections,
            memory_links=memory_links,
        )
        memory_link_context = self._build_memory_link_context(
            memory_links=memory_links,
            selected_memory_ids=selected_memory_ids,
        )
        recall_pack_selection = dict(selection_result["recall_pack_selection"])
        self._attach_memory_link_context_to_selection_trace(
            recall_pack_selection=recall_pack_selection,
            memory_link_context=memory_link_context,
        )
        event_evidence_role = state["model_presets"][state["selected_model_preset_id"]]["roles"]["event_evidence_generation"]
        event_evidence_result = self._build_event_evidence(
            memory_set_id=memory_set_id,
            primary_recall_focus=primary_recall_focus,
            recall_hint=recall_hint,
            sections=sections,
            role_definition=event_evidence_role,
        )
        event_evidence = event_evidence_result["event_evidence"]
        selected_event_ids = event_evidence_result["selected_event_ids"]
        visual_observations = self._build_visual_observations(
            memory_set_id=memory_set_id,
            augmented_query_text=augmented_query_text,
            limit=VISUAL_OBSERVATION_RECALL_LIMIT,
        )
        visual_daily_digests = self._build_visual_daily_digests(
            memory_set_id=memory_set_id,
            augmented_query_text=augmented_query_text,
            limit=VISUAL_DAILY_DIGEST_RECALL_LIMIT,
        )

        # 結果
        return {
            **sections,
            "event_evidence": event_evidence,
            "visual_observations": visual_observations,
            "visual_daily_digests": visual_daily_digests,
            "event_evidence_generation": event_evidence_result["event_evidence_generation"],
            "selected_memory_ids": selected_memory_ids,
            "selected_episode_ids": selected_episode_ids,
            "association_selected_memory_ids": association_selected_memory_ids,
            "association_selected_episode_ids": association_selected_episode_ids,
            "selected_event_ids": selected_event_ids,
            "memory_link_context": memory_link_context,
            "recall_pack_selection": recall_pack_selection,
            "candidate_count": len(raw_candidate_ids),
        }

    def _build_scope_context(self, recall_hint: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
        # focus scope群
        focus_specs = self._parse_focus_scopes(recall_hint.get("focus_scopes", []))
        mentioned_entity_filters = self._parse_mentioned_entities(
            recall_hint.get("mentioned_entities", [])
        )
        mentioned_topic_filters = self._parse_mentioned_topics(
            recall_hint.get("mentioned_topics", [])
        )
        primary_recall_focus = recall_hint["primary_recall_focus"]

        # 基底scope群
        user_filters = self._merged_scope_filters([("user", "user")], focus_specs, allowed_scope_type="user")
        self_filters = self._merged_scope_filters([("self", "self")], focus_specs, allowed_scope_type="self")
        relationship_defaults = [("relationship", "self|user")]
        relationship_filters = self._merged_scope_filters(
            relationship_defaults if primary_recall_focus in {"commitment", "user", "relationship"} else [],
            focus_specs,
            allowed_scope_type="relationship",
        )
        topic_filters = self._merged_scope_filters(
            mentioned_topic_filters,
            focus_specs,
            allowed_scope_type="topic",
        )
        world_filters = (
            [("world", "world")]
            if primary_recall_focus in {"state", "fact"}
            else []
        )
        episode_scope_filters = self._merged_scope_filters(
            user_filters
            + relationship_filters
            + self_filters
            + topic_filters
            + mentioned_entity_filters
            + world_filters,
            [],
            allowed_scope_type=None,
        )

        # 結果
        return {
            "user_filters": user_filters,
            "self_filters": self_filters,
            "relationship_filters": relationship_filters,
            "topic_filters": topic_filters,
            "entity_filters": mentioned_entity_filters,
            "world_filters": world_filters,
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
        primary_recall_focus: str,
    ) -> list[dict[str, Any]]:
        # クエリ
        records = self.store.list_episodes_for_recall(
            memory_set_id=memory_set_id,
            scope_filters=scope_filters,
            require_open_loops=primary_recall_focus == "commitment",
            limit=SECTION_LIMITS["episodic_evidence"] * 4,
        )

        # 結果
        return [self._to_episode_item(record) for record in records]

    def _section_priority(self, recall_hint: dict[str, Any]) -> list[str]:
        # 主順序
        primary_recall_focus = recall_hint["primary_recall_focus"]
        ordered = self._primary_section_priority(primary_recall_focus)

        # 副次補正群
        boosted_sections = self._secondary_section_boosts(
            self._secondary_recall_focuses(recall_hint),
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

    def _primary_section_priority(self, primary_recall_focus: str) -> list[str]:
        # マッピング
        if primary_recall_focus == "commitment":
            return [
                "active_commitments",
                "relationship_model",
                "episodic_evidence",
                "user_model",
                "active_topics",
                "self_model",
            ]
        if primary_recall_focus == "relationship":
            return [
                "relationship_model",
                "user_model",
                "episodic_evidence",
                "active_commitments",
                "active_topics",
                "self_model",
            ]
        if primary_recall_focus == "user":
            return [
                "user_model",
                "relationship_model",
                "active_topics",
                "episodic_evidence",
                "active_commitments",
                "self_model",
            ]
        if primary_recall_focus == "episodic":
            return [
                "episodic_evidence",
                "active_topics",
                "user_model",
                "relationship_model",
                "active_commitments",
                "self_model",
            ]
        if primary_recall_focus == "state":
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

    def _secondary_section_boosts(self, secondary_recall_focuses: list[str]) -> list[str]:
        # 状態
        boosted: list[str] = []

        # 収集
        for focus in secondary_recall_focuses:
            for section_name in self._primary_section_priority(focus)[:2]:
                if section_name not in boosted:
                    boosted.append(section_name)

        # 結果
        return boosted

    def _secondary_recall_focuses(self, recall_hint: dict[str, Any]) -> list[str]:
        # 正規化
        secondary_recall_focuses = normalized_text_list(
            recall_hint.get("secondary_recall_focuses", []),
            limit=2,
        )

        # 結果
        return secondary_recall_focuses

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
            if scope_type == "topic":
                parsed.append((scope_type, normalized))
                continue
            parsed.append((scope_type, scope_key.strip()))

        # 結果
        return parsed

    def _parse_mentioned_entities(self, entities: list[Any]) -> list[tuple[str, str]]:
        # 解析
        parsed: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for entity in entities:
            if not isinstance(entity, str):
                continue
            normalized = entity.strip()
            if not normalized:
                continue
            prefix, separator, value = normalized.partition(":")
            if not separator or not value:
                continue
            if prefix not in {"person", "place", "tool"}:
                continue
            scope_filter = ("entity", normalized)
            if scope_filter in seen:
                continue
            parsed.append(scope_filter)
            seen.add(scope_filter)

        # 結果
        return parsed

    def _parse_mentioned_topics(self, topics: list[Any]) -> list[tuple[str, str]]:
        # 解析
        parsed: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for topic in topics:
            if not isinstance(topic, str):
                continue
            normalized = topic.strip()
            if not normalized:
                continue
            if not normalized.startswith("topic:") or normalized == "topic:":
                continue
            scope_filter = ("topic", normalized)
            if scope_filter in seen:
                continue
            parsed.append(scope_filter)
            seen.add(scope_filter)

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
            "visual_observations": [],
            "visual_daily_digests": [],
            "event_evidence_generation": {
                "requested_event_count": 0,
                "loaded_event_count": 0,
                "succeeded_event_count": 0,
                "failed_items": [],
                "precise_evidence_used": False,
                "precise_reason_codes": [],
                "precise_reason_summary": None,
                "precise_selected_event_ids": [],
                "precise_requested_event_count": 0,
                "precise_loaded_event_count": 0,
            },
            "recall_pack_selection": self._empty_recall_pack_selection(),
            "conflicts": [],
            "selected_memory_ids": [],
            "selected_episode_ids": [],
            "association_selected_memory_ids": [],
            "association_selected_episode_ids": [],
            "selected_event_ids": [],
            "memory_link_context": self._empty_memory_link_context(),
            "candidate_count": 0,
        }

    def _build_visual_observations(
        self,
        *,
        memory_set_id: str,
        augmented_query_text: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        # 検索一致、重要、直近の枠を分け、毎分キャプチャで直近だけに寄らないようにする。
        queried_records = self.store.list_visual_observation_records(
            memory_set_id=memory_set_id,
            query_text=augmented_query_text,
            limit=max(limit * 4, 12),
        )
        important_records = self.store.list_important_visual_observation_records(
            memory_set_id=memory_set_id,
            limit=max(limit * 2, 6),
        )
        recent_records = self.store.list_recent_visual_observation_records(
            memory_set_id=memory_set_id,
            limit=max(limit * 2, 6),
        )

        # 枠別選定
        selected_records: list[dict[str, Any]] = []
        seen: set[str] = set()
        used_groups: set[str] = set()
        used_sources: set[str] = set()

        query_target = min(2, limit)
        self._append_visual_observation_records(
            selected_records=selected_records,
            seen=seen,
            used_groups=used_groups,
            used_sources=used_sources,
            records=queried_records,
            target_count=query_target,
            allow_same_source=False,
        )
        important_target = min(limit, len(selected_records) + 1)
        self._append_visual_observation_records(
            selected_records=selected_records,
            seen=seen,
            used_groups=used_groups,
            used_sources=used_sources,
            records=important_records,
            target_count=important_target,
            allow_same_source=False,
        )
        self._append_visual_observation_records(
            selected_records=selected_records,
            seen=seen,
            used_groups=used_groups,
            used_sources=used_sources,
            records=recent_records,
            target_count=limit,
            allow_same_source=False,
        )

        if len(selected_records) < limit:
            self._append_visual_observation_records(
                selected_records=selected_records,
                seen=seen,
                used_groups=used_groups,
                used_sources=used_sources,
                records=queried_records + important_records + recent_records,
                target_count=limit,
                allow_same_source=True,
            )

        # 結果
        return [self._to_visual_observation_item(record) for record in selected_records[:limit]]

    def _append_visual_observation_records(
        self,
        *,
        selected_records: list[dict[str, Any]],
        seen: set[str],
        used_groups: set[str],
        used_sources: set[str],
        records: list[dict[str, Any]],
        target_count: int,
        allow_same_source: bool,
    ) -> None:
        # 代表を優先し、不足時だけ同一 source/group の重複を許す。
        for record in records:
            if len(selected_records) >= target_count:
                return
            visual_observation_id = record.get("visual_observation_id")
            if not isinstance(visual_observation_id, str) or visual_observation_id in seen:
                continue

            duplicate_group_id = record.get("duplicate_group_id")
            if isinstance(duplicate_group_id, str) and duplicate_group_id in used_groups and not allow_same_source:
                continue

            source_key = self._visual_observation_source_key(record)
            if source_key in used_sources and not allow_same_source:
                continue

            selected_records.append(record)
            seen.add(visual_observation_id)
            if isinstance(duplicate_group_id, str) and duplicate_group_id:
                used_groups.add(duplicate_group_id)
            used_sources.add(source_key)

    def _visual_observation_source_key(self, record: dict[str, Any]) -> str:
        # source の代表化
        for key in ("vision_source_id", "source_label", "source_kind"):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "unknown"

    def _build_visual_daily_digests(
        self,
        *,
        memory_set_id: str,
        augmented_query_text: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        # 検索一致と直近日次 digest を合わせる。
        queried_records = self.store.list_daily_visual_digests(
            memory_set_id=memory_set_id,
            query_text=augmented_query_text,
            limit=limit,
        )
        recent_records = self.store.list_daily_visual_digests(
            memory_set_id=memory_set_id,
            query_text=None,
            limit=limit,
        )

        # 重複除去
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for record in queried_records + recent_records:
            digest_id = record.get("digest_id")
            if not isinstance(digest_id, str) or digest_id in seen:
                continue
            selected.append(self._to_visual_daily_digest_item(record))
            seen.add(digest_id)
            if len(selected) >= limit:
                break

        # 結果
        return selected

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

    def _empty_memory_link_context(self) -> dict[str, Any]:
        # 結果
        return {
            "selected_memory_unit_count": 0,
            "link_count": 0,
            "label_counts": {},
            "representative_links": [],
            "result_status": "empty",
        }

    def _attach_memory_link_summaries_to_sections(
        self,
        *,
        sections: dict[str, list[dict[str, Any]]],
        memory_links: list[dict[str, Any]],
    ) -> None:
        # 対象なし
        if not memory_links:
            return

        # memory_unit ごと要約
        summaries_by_memory_id = self._memory_link_summaries_by_memory_id(
            memory_links=memory_links,
            memory_unit_ids=self._collect_selected_ids(sections, key="memory_unit_id"),
        )

        # 反映
        for section_items in sections.values():
            for item in section_items:
                memory_unit_id = item.get("memory_unit_id")
                if not isinstance(memory_unit_id, str):
                    continue
                summary = summaries_by_memory_id.get(memory_unit_id)
                if summary is None:
                    continue
                item["memory_link_summary"] = summary

    def _memory_link_summaries_by_memory_id(
        self,
        *,
        memory_links: list[dict[str, Any]],
        memory_unit_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        # 状態
        selected_ids = set(memory_unit_ids)
        buckets: dict[str, dict[str, Any]] = {
            memory_unit_id: {
                "label_counts": {},
                "representative_links": [],
            }
            for memory_unit_id in memory_unit_ids
        }

        # 集計
        for link in memory_links:
            label = self._normalized_memory_link_label(link.get("label"))
            if label is None:
                continue
            endpoints = self._memory_link_selected_endpoints(link=link, selected_ids=selected_ids)
            for memory_unit_id, direction, related_unit in endpoints:
                bucket = buckets.get(memory_unit_id)
                if bucket is None:
                    continue
                label_counts = bucket["label_counts"]
                label_counts[label] = int(label_counts.get(label, 0)) + 1
                representatives = bucket["representative_links"]
                if len(representatives) >= MEMORY_LINK_RECALL_HINT_LIMIT:
                    continue
                related_summary = self._short_relation_text(
                    related_unit.get("summary_text") if isinstance(related_unit, dict) else None,
                    limit=72,
                )
                if related_summary is None:
                    continue
                representatives.append(
                    {
                        "label": label,
                        "direction": direction,
                        "summary_text": f"{label}/{direction}: {related_summary}",
                        "related_summary_text": related_summary,
                    }
                )

        # 空bucketを落とす
        return {
            memory_unit_id: {
                "label_counts": self._ordered_memory_link_label_counts(bucket["label_counts"]),
                "representative_links": bucket["representative_links"],
            }
            for memory_unit_id, bucket in buckets.items()
            if bucket["label_counts"]
        }

    def _build_memory_link_context(
        self,
        *,
        memory_links: list[dict[str, Any]],
        selected_memory_ids: list[str],
    ) -> dict[str, Any]:
        # 空
        if not selected_memory_ids or not memory_links:
            context = self._empty_memory_link_context()
            context["selected_memory_unit_count"] = len(selected_memory_ids)
            return context

        # 集計
        selected_ids = set(selected_memory_ids)
        label_counts: dict[str, int] = {}
        representative_links: list[dict[str, Any]] = []
        link_count = 0
        for link in memory_links:
            label = self._normalized_memory_link_label(link.get("label"))
            if label is None:
                continue
            endpoints = self._memory_link_selected_endpoints(link=link, selected_ids=selected_ids)
            if not endpoints:
                continue
            link_count += 1
            label_counts[label] = label_counts.get(label, 0) + 1
            if len(representative_links) >= MEMORY_LINK_RECALL_TRACE_LIMIT:
                continue
            representative = self._memory_link_trace_item(link=link, label=label, selected_ids=selected_ids)
            if representative is not None:
                representative_links.append(representative)

        # 結果
        return {
            "selected_memory_unit_count": len(selected_memory_ids),
            "link_count": link_count,
            "label_counts": self._ordered_memory_link_label_counts(label_counts),
            "representative_links": representative_links,
            "result_status": "linked" if link_count > 0 else "empty",
        }

    def _attach_memory_link_context_to_selection_trace(
        self,
        *,
        recall_pack_selection: dict[str, Any],
        memory_link_context: dict[str, Any],
    ) -> None:
        # trace は inspection 用の compact summary に留める。
        recall_pack_selection["memory_link_count"] = int(memory_link_context.get("link_count", 0) or 0)
        recall_pack_selection["memory_link_label_counts"] = memory_link_context.get("label_counts", {})
        recall_pack_selection["memory_link_representative_links"] = [
            {
                "label": item.get("label"),
                "summary_text": item.get("summary_text"),
            }
            for item in memory_link_context.get("representative_links", [])
            if isinstance(item, dict)
        ][:MEMORY_LINK_RECALL_HINT_LIMIT]

    def _memory_link_trace_item(
        self,
        *,
        link: dict[str, Any],
        label: str,
        selected_ids: set[str],
    ) -> dict[str, Any] | None:
        # endpoint
        source_memory_unit_id = str(link.get("source_memory_unit_id") or "").strip()
        target_memory_unit_id = str(link.get("target_memory_unit_id") or "").strip()
        if source_memory_unit_id in selected_ids and target_memory_unit_id in selected_ids:
            selected_endpoint = "internal"
        elif source_memory_unit_id in selected_ids:
            selected_endpoint = "outgoing"
        elif target_memory_unit_id in selected_ids:
            selected_endpoint = "incoming"
        else:
            return None

        # 要約
        source_unit = link.get("source_memory_unit")
        target_unit = link.get("target_memory_unit")
        source_summary = self._short_relation_text(
            source_unit.get("summary_text") if isinstance(source_unit, dict) else None,
            limit=80,
        )
        target_summary = self._short_relation_text(
            target_unit.get("summary_text") if isinstance(target_unit, dict) else None,
            limit=80,
        )
        if source_summary is None and target_summary is None:
            return None

        # 結果
        return {
            "memory_link_id": link.get("memory_link_id"),
            "label": label,
            "selected_endpoint": selected_endpoint,
            "source_memory_unit_id": source_memory_unit_id,
            "target_memory_unit_id": target_memory_unit_id,
            "source_status": source_unit.get("status") if isinstance(source_unit, dict) else None,
            "target_status": target_unit.get("status") if isinstance(target_unit, dict) else None,
            "source_summary_text": source_summary,
            "target_summary_text": target_summary,
            "summary_text": f"{label}: {source_summary or '?'} -> {target_summary or '?'}",
        }

    def _memory_link_selected_endpoints(
        self,
        *,
        link: dict[str, Any],
        selected_ids: set[str],
    ) -> list[tuple[str, str, dict[str, Any] | None]]:
        # endpoint 判定
        source_memory_unit_id = str(link.get("source_memory_unit_id") or "").strip()
        target_memory_unit_id = str(link.get("target_memory_unit_id") or "").strip()
        source_unit = link.get("source_memory_unit")
        target_unit = link.get("target_memory_unit")
        endpoints: list[tuple[str, str, dict[str, Any] | None]] = []
        if source_memory_unit_id in selected_ids:
            endpoints.append(
                (
                    source_memory_unit_id,
                    "outgoing",
                    target_unit if isinstance(target_unit, dict) else None,
                )
            )
        if target_memory_unit_id in selected_ids:
            endpoints.append(
                (
                    target_memory_unit_id,
                    "incoming",
                    source_unit if isinstance(source_unit, dict) else None,
                )
            )
        return endpoints

    def _normalized_memory_link_label(self, value: Any) -> str | None:
        # 正規化
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    def _ordered_memory_link_label_counts(self, label_counts: dict[str, int]) -> dict[str, int]:
        # label priority 順に安定化する。
        ordered: dict[str, int] = {}
        for label in MEMORY_LINK_RECALL_LABEL_PRIORITY:
            count = int(label_counts.get(label, 0) or 0)
            if count > 0:
                ordered[label] = count
        for label in sorted(label_counts):
            if label in ordered:
                continue
            count = int(label_counts.get(label, 0) or 0)
            if count > 0:
                ordered[label] = count
        return ordered

    def _short_relation_text(self, value: Any, *, limit: int) -> str | None:
        # 短縮
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.strip().split())
        if not normalized:
            return None
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 1].rstrip() + "…"

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

    def _to_visual_observation_item(self, record: dict[str, Any]) -> dict[str, Any]:
        # 視覚記録項目化
        return {
            "source_kind": "visual_observation_record",
            "visual_observation_id": record["visual_observation_id"],
            "observed_at": record["observed_at"],
            "vision_source_id": record.get("vision_source_id"),
            "source_label": record.get("source_label"),
            "image_input_kind": record["image_input_kind"],
            "detailed_summary_text": record["detailed_summary_text"],
            "confidence_hint": record.get("confidence_hint"),
            "retention_status": record["retention_status"],
        }

    def _to_visual_daily_digest_item(self, record: dict[str, Any]) -> dict[str, Any]:
        # 日次視覚整理項目化
        group_summaries = [
            self._to_visual_daily_group_summary(item)
            for item in record.get("group_summaries", [])
            if isinstance(item, dict)
        ][:3]
        memory_candidate_summaries = [
            self._to_visual_daily_memory_candidate_summary(item)
            for item in record.get("memory_candidate_summaries", [])
            if isinstance(item, dict)
        ][:3]
        return {
            "source_kind": "daily_visual_digest",
            "digest_id": record["digest_id"],
            "local_date": record["local_date"],
            "record_count": int(record.get("record_count", 0) or 0),
            "group_count": int(record.get("group_count", 0) or 0),
            "retained_count": int(record.get("retained_count", 0) or 0),
            "compressed_count": int(record.get("compressed_count", 0) or 0),
            "group_summaries": group_summaries,
            "memory_candidate_summaries": memory_candidate_summaries,
        }

    def _to_visual_daily_group_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        # group要約項目化
        return {
            "summary_text": item.get("summary_text"),
            "record_count": int(item.get("record_count", 0) or 0),
            "first_observed_at": item.get("first_observed_at"),
            "last_observed_at": item.get("last_observed_at"),
            "representative_visual_observation_id": item.get("representative_visual_observation_id"),
        }

    def _to_visual_daily_memory_candidate_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        # 候補要約項目化
        return {
            "summary_text": item.get("summary_text"),
            "reason_code": item.get("reason_code"),
            "duplicate_group_id": item.get("duplicate_group_id"),
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
