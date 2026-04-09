from __future__ import annotations

import json
from typing import Any

from otomekairo.llm import LLMClient
from otomekairo.memory_utils import normalized_text_list
from otomekairo.recall_event_evidence import RecallEventEvidenceMixin
from otomekairo.store import FileStore


# 定数
ACTIVE_MEMORY_STATUSES = ("inferred", "confirmed")
ACTIVE_COMMITMENT_STATES = ("open", "waiting_confirmation", "on_hold")
SECTION_LIMITS = {
    "self_model": 2,
    "user_model": 4,
    "relationship_model": 3,
    "active_topics": 2,
    "active_commitments": 3,
    "episodic_evidence": 2,
    "conflicts": 2,
}
GLOBAL_RECALL_LIMIT = 14
ASSOCIATION_MEMORY_LIMIT = 6
ASSOCIATION_EPISODE_LIMIT = 4
ASSOCIATION_QUERY_KIND_WEIGHTS = {
    "observation": 1.0,
    "entity": 0.92,
    "topic": 0.88,
}


# recall構築
class RecallBuilder(RecallEventEvidenceMixin):
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
        # 記憶切り替え
        if not state.get("memory_enabled", True):
            return self._empty_recall_pack()

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

        # 全体トリム
        sections = self._apply_global_limit(
            recall_hint=recall_hint,
            sections={
                "self_model": self_model,
                "user_model": user_model,
                "relationship_model": relationship_model,
                "active_topics": active_topics,
                "active_commitments": active_commitments,
                "episodic_evidence": episodic_evidence,
                "conflicts": conflicts,
            },
        )

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
        event_evidence = self._build_event_evidence(
            memory_set_id=memory_set_id,
            primary_intent=primary_intent,
            recall_hint=recall_hint,
            sections=sections,
        )
        selected_event_ids = [item["event_id"] for item in event_evidence]

        # 結果
        return {
            **sections,
            "event_evidence": event_evidence,
            "selected_memory_ids": selected_memory_ids,
            "selected_episode_ids": selected_episode_ids,
            "association_selected_memory_ids": association_selected_memory_ids,
            "association_selected_episode_ids": association_selected_episode_ids,
            "selected_event_ids": selected_event_ids,
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

    def _build_association_sections(
        self,
        *,
        state: dict[str, Any],
        observation_text: str,
        recall_hint: dict[str, Any],
        scope_context: dict[str, list[tuple[str, str]]],
    ) -> dict[str, list[dict[str, Any]]]:
        # クエリ仕様群
        query_specs = self._association_query_specs(observation_text, recall_hint)
        if not query_specs:
            return self._empty_association_sections()

        # 埋め込みコンテキスト
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        embedding_role = selected_preset["roles"]["embedding"]
        embedding_dimension = self._embedding_dimension(embedding_role)
        embedding_preset = self._embedding_preset(embedding_role, embedding_dimension)

        # クエリ埋め込み群
        query_embeddings = self.llm.generate_embeddings(
            role_definition=embedding_role,
            texts=[spec["text"] for spec in query_specs],
        )

        # 候補状態
        memory_candidates: dict[str, dict[str, Any]] = {}
        episode_candidates: dict[str, dict[str, Any]] = {}

        # クエリループ
        for spec, query_embedding in zip(query_specs, query_embeddings, strict=True):
            # memory hit群
            memory_hits = self.store.search_memory_unit_vector_entries(
                memory_set_id=state["selected_memory_set_id"],
                embedding_preset=embedding_preset,
                query_embedding=query_embedding,
                embedding_dimension=embedding_dimension,
                limit=self._association_search_limit(
                    source_kind="memory_unit",
                    query_kind=spec["kind"],
                ),
                exclude_source_types=["commitment"],
                statuses=list(ACTIVE_MEMORY_STATUSES),
            )

            # 記憶統合
            for hit in memory_hits:
                item = self._to_memory_item(hit["record"])
                section_name = self._section_name_for_memory_item(item)
                if section_name is None:
                    continue
                item["retrieval_lane"] = "association"
                item["association_score"] = self._association_score(
                    recall_hint=recall_hint,
                    distance=hit["distance"],
                    item=item,
                    query_kind=spec["kind"],
                    query_weight=float(spec["weight"]),
                )
                self._merge_association_candidate(
                    candidates=memory_candidates,
                    item=item,
                    query_kind=spec["kind"],
                )

            # episode hit群
            episode_hits = self.store.search_episode_vector_entries(
                memory_set_id=state["selected_memory_set_id"],
                embedding_preset=embedding_preset,
                query_embedding=query_embedding,
                embedding_dimension=embedding_dimension,
                limit=self._association_search_limit(
                    source_kind="episode",
                    query_kind=spec["kind"],
                ),
                scope_filters=None,
                require_open_loops=recall_hint["primary_intent"] == "commitment_check",
            )

            # 要約統合
            for hit in episode_hits:
                item = self._to_episode_item(hit["record"])
                item["retrieval_lane"] = "association"
                item["association_score"] = self._association_score(
                    recall_hint=recall_hint,
                    distance=hit["distance"],
                    item=item,
                    query_kind=spec["kind"],
                    query_weight=float(spec["weight"]),
                )
                self._merge_association_candidate(
                    candidates=episode_candidates,
                    item=item,
                    query_kind=spec["kind"],
                )

        # セクション群
        sections = self._empty_association_sections()
        for item in self._finalize_association_candidates(memory_candidates):
            section_name = self._section_name_for_memory_item(item)
            if section_name is None:
                continue
            sections[section_name].append(item)
        sections["episodic_evidence"].extend(self._finalize_association_candidates(episode_candidates))

        # 並べ替え
        for section_name, items in sections.items():
            items.sort(
                key=lambda item: (
                    float(item.get("association_score", 0.0)),
                    float(item.get("salience", 0.0)),
                ),
                reverse=True,
            )

        # 結果
        return sections

    def _association_query_specs(
        self,
        observation_text: str,
        recall_hint: dict[str, Any],
    ) -> list[dict[str, Any]]:
        # 基底状態
        specs: list[dict[str, Any]] = []
        normalized_observation = observation_text.strip()

        # 観測クエリ
        observation_query = self._association_observation_query_text(
            normalized_observation,
            recall_hint,
        )
        if observation_query:
            specs.append(
                {
                    "kind": "observation",
                    "text": observation_query,
                    "weight": self._association_query_weight(
                        query_kind="observation",
                        recall_hint=recall_hint,
                    ),
                }
            )

        # エンティティクエリ
        entity_terms = self._association_hint_terms(recall_hint.get("mentioned_entities", []))
        if entity_terms:
            specs.append(
                {
                    "kind": "entity",
                    "text": "関連対象\n" + "\n".join(entity_terms),
                    "weight": self._association_query_weight(
                        query_kind="entity",
                        recall_hint=recall_hint,
                    ),
                }
            )

        # トピッククエリ
        topic_terms = self._association_hint_terms(recall_hint.get("mentioned_topics", []))
        if topic_terms:
            specs.append(
                {
                    "kind": "topic",
                    "text": "関連話題\n" + "\n".join(topic_terms),
                    "weight": self._association_query_weight(
                        query_kind="topic",
                        recall_hint=recall_hint,
                    ),
                }
            )

        # 結果
        return specs

    def _association_observation_query_text(
        self,
        observation_text: str,
        recall_hint: dict[str, Any],
    ) -> str:
        # 空
        if not observation_text:
            return ""

        # 部品群
        parts = [observation_text]
        primary_intent = recall_hint["primary_intent"]
        secondary_intents = self._secondary_intents(recall_hint)
        time_reference = str(recall_hint.get("time_reference", "none")).strip()
        if primary_intent:
            parts.append(f"意図: {primary_intent}")
        if secondary_intents:
            parts.append("補助意図: " + ", ".join(secondary_intents))
        if time_reference and time_reference != "none":
            parts.append(f"時間軸: {time_reference}")

        # 結果
        return "\n".join(parts)

    def _association_hint_terms(self, values: list[Any]) -> list[str]:
        # 状態
        expanded: list[str] = []

        # 展開
        for value in normalized_text_list(values, limit=4):
            expanded.extend(self._expanded_association_terms(value))

        # 結果
        return normalized_text_list(expanded, limit=8)

    def _expanded_association_terms(self, value: str) -> list[str]:
        # 状態
        terms = [value]

        # タグ付き値
        prefix, separator, suffix = value.partition(":")
        if separator and suffix:
            cleaned_suffix = suffix.strip().strip("<>").replace("|", " ")
            if cleaned_suffix:
                terms.append(cleaned_suffix)
                terms.append(f"{prefix} {cleaned_suffix}")

        # 結果
        return normalized_text_list(terms, limit=3)

    def _association_query_weight(
        self,
        *,
        query_kind: str,
        recall_hint: dict[str, Any],
    ) -> float:
        # 基底
        weight = ASSOCIATION_QUERY_KIND_WEIGHTS.get(query_kind, 1.0)
        primary_intent = recall_hint["primary_intent"]
        secondary_intents = set(self._secondary_intents(recall_hint))
        time_reference = recall_hint.get("time_reference")

        # intent補正
        if primary_intent == "reminisce" and query_kind == "observation":
            weight += 0.08
        if primary_intent in {"commitment_check", "meta_relationship"} and query_kind == "entity":
            weight += 0.12
        if primary_intent in {"consult", "check_state", "preference_query"} and query_kind == "topic":
            weight += 0.12

        # 副次補正
        if "reminisce" in secondary_intents and query_kind == "observation":
            weight += 0.04
        if "meta_relationship" in secondary_intents and query_kind == "entity":
            weight += 0.05
        if {"consult", "check_state", "preference_query"} & secondary_intents and query_kind == "topic":
            weight += 0.04

        # 時刻補正
        if time_reference == "past" and query_kind == "observation":
            weight += 0.04
        if time_reference == "future" and query_kind == "entity":
            weight += 0.04
        if time_reference == "persistent" and query_kind == "topic":
            weight += 0.04

        # 結果
        return weight

    def _association_search_limit(
        self,
        *,
        source_kind: str,
        query_kind: str,
    ) -> int:
        # 基底
        if source_kind == "memory_unit":
            base_limit = ASSOCIATION_MEMORY_LIMIT
        else:
            base_limit = ASSOCIATION_EPISODE_LIMIT

        # クエリ補正
        if query_kind == "observation":
            return base_limit
        return max(2, base_limit - 2)

    def _merge_association_candidate(
        self,
        *,
        candidates: dict[str, dict[str, Any]],
        item: dict[str, Any],
        query_kind: str,
    ) -> None:
        # 検索
        record_id = self._record_id(item)
        existing = candidates.get(record_id)
        if existing is None:
            item["association_query_kinds"] = [query_kind]
            item["association_match_count"] = 1
            candidates[record_id] = item
            return

        # スコア
        existing["association_score"] = max(
            float(existing.get("association_score", 0.0)),
            float(item.get("association_score", 0.0)),
        )

        # クエリ種別群
        query_kinds = normalized_text_list(
            list(existing.get("association_query_kinds", [])) + [query_kind],
            limit=4,
        )
        existing["association_query_kinds"] = query_kinds
        existing["association_match_count"] = len(query_kinds)

    def _finalize_association_candidates(
        self,
        candidates: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # 確定
        finalized: list[dict[str, Any]] = []
        for item in candidates.values():
            match_count = int(item.get("association_match_count", 1))
            if match_count > 1:
                item["association_score"] = float(item.get("association_score", 0.0)) + 0.03 * (match_count - 1)
            finalized.append(item)

        # 結果
        return finalized

    def _association_score(
        self,
        *,
        recall_hint: dict[str, Any],
        distance: float,
        item: dict[str, Any],
        query_kind: str,
        query_weight: float,
    ) -> float:
        # 基底
        score = query_weight * (1.0 / (1.0 + max(distance, 0.0)))
        primary_intent = recall_hint["primary_intent"]
        secondary_intents = set(self._secondary_intents(recall_hint))

        # クエリ種別補正
        item_scope_type = self._association_item_scope_type(item)
        if query_kind == "entity" and item_scope_type in {"user", "relationship"}:
            score += 0.05
        if query_kind == "topic" and item_scope_type == "topic":
            score += 0.05

        # focus補正
        if self._focus_scope_matches(recall_hint.get("focus_scopes", []), item):
            score += 0.08

        # intent補正
        if primary_intent == "reminisce" and item["source_kind"] == "episode":
            score += 0.12
        if primary_intent == "commitment_check" and (
            item.get("has_open_loops") or item.get("commitment_state") in ACTIVE_COMMITMENT_STATES
        ):
            score += 0.12
        if primary_intent == "meta_relationship" and item.get("scope_type") == "relationship":
            score += 0.1
        if primary_intent in {"consult", "check_state"} and item.get("scope_type") in {"user", "topic"}:
            score += 0.08
        if primary_intent == "preference_query" and item.get("memory_type") == "preference":
            score += 0.08
        if recall_hint.get("time_reference") == "past" and item["source_kind"] == "episode":
            score += 0.05

        # 副次補正
        if "reminisce" in secondary_intents and item["source_kind"] == "episode":
            score += 0.04
        if "meta_relationship" in secondary_intents and item_scope_type == "relationship":
            score += 0.04
        if {"consult", "check_state"} & secondary_intents and item_scope_type in {"user", "topic"}:
            score += 0.03
        if "preference_query" in secondary_intents and item.get("memory_type") == "preference":
            score += 0.03

        # 結果
        return score

    def _association_item_scope_type(self, item: dict[str, Any]) -> str:
        # Episode要約
        if item["source_kind"] == "episode":
            return str(item.get("primary_scope_type", ""))

        # 記憶単位
        return str(item.get("scope_type", ""))

    def _focus_scope_matches(self, focus_scopes: list[Any], item: dict[str, Any]) -> bool:
        # 解析
        focus_specs = self._parse_focus_scopes(focus_scopes)
        if item["source_kind"] == "episode":
            scope_type = item["primary_scope_type"]
            scope_key = item["primary_scope_key"]
        else:
            scope_type = item["scope_type"]
            scope_key = item["scope_key"]

        # 一致
        return (scope_type, scope_key) in focus_specs

    def _section_name_for_memory_item(self, item: dict[str, Any]) -> str | None:
        # マッピング
        scope_type = item["scope_type"]
        if scope_type == "self":
            return "self_model"
        if scope_type == "user":
            return "user_model"
        if scope_type == "relationship":
            return "relationship_model"
        if scope_type == "topic":
            return "active_topics"
        return None

    def _build_conflicts(
        self,
        *,
        memory_set_id: str,
        selected_memory_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # 状態
        conflicts: list[dict[str, Any]] = []
        seen_conflict_keys: set[tuple[str, str, str, str, str]] = set()

        # 走査
        for item in selected_memory_items:
            compare_key = (
                item["memory_type"],
                item["scope_type"],
                item["scope_key"],
                item["subject_ref"],
                item["predicate"],
            )
            if compare_key in seen_conflict_keys:
                continue

            matches = self.store.find_memory_units_for_compare(
                memory_set_id=memory_set_id,
                memory_type=item["memory_type"],
                scope_type=item["scope_type"],
                scope_key=item["scope_key"],
                subject_ref=item["subject_ref"],
                predicate=item["predicate"],
                limit=5,
            )
            active_matches = [
                match
                for match in matches
                if match["status"] in ACTIVE_MEMORY_STATUSES
            ]
            if len(active_matches) < 2:
                continue

            variant_signatures = {
                (
                    match.get("object_ref_or_value"),
                    json.dumps(match.get("qualifiers", {}), ensure_ascii=False, sort_keys=True),
                )
                for match in active_matches
            }
            if len(variant_signatures) < 2:
                continue

            # 競合エントリ
            conflicts.append(
                {
                    "source_kind": "conflict",
                    "compare_key": {
                        "memory_type": item["memory_type"],
                        "scope_type": item["scope_type"],
                        "scope_key": item["scope_key"],
                        "subject_ref": item["subject_ref"],
                        "predicate": item["predicate"],
                    },
                    "memory_unit_ids": [match["memory_unit_id"] for match in active_matches],
                    "summary_text": "同じ対象について異なる理解が併存している。",
                }
            )
            seen_conflict_keys.add(compare_key)
            if len(conflicts) >= SECTION_LIMITS["conflicts"]:
                break

        # 結果
        return conflicts

    def _apply_global_limit(
        self,
        *,
        recall_hint: dict[str, Any],
        sections: dict[str, list[dict[str, Any]]],
    ) -> dict[str, list[dict[str, Any]]]:
        # 初期状態
        used_record_ids: set[str] = set()
        trimmed = {
            "self_model": [],
            "user_model": [],
            "relationship_model": [],
            "active_topics": [],
            "active_commitments": [],
            "episodic_evidence": [],
            "conflicts": sections["conflicts"][: SECTION_LIMITS["conflicts"]],
        }
        remaining = GLOBAL_RECALL_LIMIT - len(trimmed["conflicts"])

        # 順序
        for section_name in self._section_priority(recall_hint):
            if remaining <= 0:
                break
            section_items: list[dict[str, Any]] = []
            for item in sections[section_name]:
                record_id = self._record_id(item)
                if record_id in used_record_ids:
                    continue
                section_items.append(item)
                used_record_ids.add(record_id)
                if len(section_items) >= remaining:
                    break
            trimmed[section_name] = section_items
            remaining -= len(section_items)

        # 結果
        return trimmed

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
            "conflicts": [],
            "selected_memory_ids": [],
            "selected_episode_ids": [],
            "association_selected_memory_ids": [],
            "association_selected_episode_ids": [],
            "selected_event_ids": [],
            "candidate_count": 0,
        }

    def _embedding_preset(self, role_definition: dict[str, Any], embedding_dimension: int) -> str:
        # 識別子
        provider = str(role_definition.get("provider", "unknown")).strip() or "unknown"
        model = str(role_definition.get("model", "unknown")).strip() or "unknown"
        endpoint_ref = str(role_definition.get("endpoint_ref", "default")).strip() or "default"
        return f"{provider}:{model}:{endpoint_ref}:dim{embedding_dimension}"

    def _embedding_dimension(self, role_definition: dict[str, Any]) -> int:
        _ = role_definition
        return 3072

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
