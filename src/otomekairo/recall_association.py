from __future__ import annotations

from typing import Any

from otomekairo.memory_utils import normalized_text_list


ACTIVE_MEMORY_STATUSES = ("inferred", "confirmed")
ACTIVE_COMMITMENT_STATES = ("open", "waiting_confirmation", "on_hold")
ASSOCIATION_MEMORY_LIMIT = 6
ASSOCIATION_EPISODE_LIMIT = 4
ASSOCIATION_QUERY_KIND_WEIGHTS = {
    "input": 1.0,
    "entity": 0.92,
    "topic": 0.88,
}


class RecallAssociationMixin:
    def _build_association_sections(
        self,
        *,
        state: dict[str, Any],
        input_text: str,
        recall_hint: dict[str, Any],
        scope_context: dict[str, list[tuple[str, str]]],
    ) -> dict[str, list[dict[str, Any]]]:
        # クエリ仕様群
        query_specs = self._association_query_specs(input_text, recall_hint)
        if not query_specs:
            return self._empty_association_sections()

        # 埋め込みコンテキスト
        selected_memory_set = state["memory_sets"][state["selected_memory_set_id"]]
        embedding_definition = selected_memory_set["embedding"]
        embedding_dimension = self._embedding_dimension(embedding_definition)

        # クエリ埋め込み群
        query_embeddings = self.llm.generate_embeddings(
            role_definition=embedding_definition,
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
                query_embedding=query_embedding,
                embedding_dimension=embedding_dimension,
                limit=self._association_search_limit(
                    source_kind="episode",
                    query_kind=spec["kind"],
                ),
                scope_filters=None,
                require_open_loops=recall_hint["primary_recall_focus"] == "commitment",
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
        input_text: str,
        recall_hint: dict[str, Any],
    ) -> list[dict[str, Any]]:
        # 基底状態
        specs: list[dict[str, Any]] = []
        normalized_input = input_text.strip()

        # 入力クエリ
        input_query = self._association_input_query_text(
            normalized_input,
            recall_hint,
        )
        if input_query:
            specs.append(
                {
                    "kind": "input",
                    "text": input_query,
                    "weight": self._association_query_weight(
                        query_kind="input",
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

    def _association_input_query_text(
        self,
        input_text: str,
        recall_hint: dict[str, Any],
    ) -> str:
        # 空
        if not input_text:
            return ""

        # 部品群
        parts = [input_text]
        primary_recall_focus = recall_hint["primary_recall_focus"]
        secondary_recall_focuses = self._secondary_recall_focuses(recall_hint)
        time_reference = str(recall_hint.get("time_reference", "none")).strip()
        if primary_recall_focus:
            parts.append(f"想起焦点: {primary_recall_focus}")
        if secondary_recall_focuses:
            parts.append("補助焦点: " + ", ".join(secondary_recall_focuses))
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
        primary_recall_focus = recall_hint["primary_recall_focus"]
        secondary_recall_focuses = set(self._secondary_recall_focuses(recall_hint))
        time_reference = recall_hint.get("time_reference")

        # focus補正
        if primary_recall_focus == "episodic" and query_kind == "input":
            weight += 0.08
        if primary_recall_focus in {"commitment", "relationship"} and query_kind == "entity":
            weight += 0.12
        if primary_recall_focus in {"user", "state", "preference", "topic"} and query_kind == "topic":
            weight += 0.12

        # 副次補正
        if "episodic" in secondary_recall_focuses and query_kind == "input":
            weight += 0.04
        if "relationship" in secondary_recall_focuses and query_kind == "entity":
            weight += 0.05
        if {"user", "state", "preference", "topic"} & secondary_recall_focuses and query_kind == "topic":
            weight += 0.04

        # 時刻補正
        if time_reference == "past" and query_kind == "input":
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
        if query_kind == "input":
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
        primary_recall_focus = recall_hint["primary_recall_focus"]
        secondary_recall_focuses = set(self._secondary_recall_focuses(recall_hint))

        # クエリ種別補正
        item_scope_type = self._association_item_scope_type(item)
        if query_kind == "entity" and item_scope_type in {"user", "relationship"}:
            score += 0.05
        if query_kind == "topic" and item_scope_type == "topic":
            score += 0.05

        # focus補正
        if self._focus_scope_matches(recall_hint.get("focus_scopes", []), item):
            score += 0.08

        # focus補正
        if primary_recall_focus == "episodic" and item["source_kind"] == "episode":
            score += 0.12
        if primary_recall_focus == "commitment" and (
            item.get("has_open_loops") or item.get("commitment_state") in ACTIVE_COMMITMENT_STATES
        ):
            score += 0.12
        if primary_recall_focus == "relationship" and item.get("scope_type") == "relationship":
            score += 0.1
        if primary_recall_focus in {"user", "state"} and item.get("scope_type") in {"user", "topic"}:
            score += 0.08
        if primary_recall_focus == "preference" and item.get("memory_type") == "preference":
            score += 0.08
        if recall_hint.get("time_reference") == "past" and item["source_kind"] == "episode":
            score += 0.05

        # 副次補正
        if "episodic" in secondary_recall_focuses and item["source_kind"] == "episode":
            score += 0.04
        if "relationship" in secondary_recall_focuses and item_scope_type == "relationship":
            score += 0.04
        if {"user", "state"} & secondary_recall_focuses and item_scope_type in {"user", "topic"}:
            score += 0.03
        if "preference" in secondary_recall_focuses and item.get("memory_type") == "preference":
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
