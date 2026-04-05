from __future__ import annotations

import json
from typing import Any

from otomekairo.llm import LLMClient
from otomekairo.memory_utils import normalized_text_list
from otomekairo.store import FileStore


# Block: Constants
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
ASSOCIATION_DIGEST_LIMIT = 4
ASSOCIATION_QUERY_KIND_WEIGHTS = {
    "observation": 1.0,
    "entity": 0.92,
    "topic": 0.88,
}
EVENT_EVIDENCE_LIMIT = 3
EVENT_EVIDENCE_INTENTS = {
    "commitment_check",
    "fact_query",
    "meta_relationship",
    "reminisce",
}


# Block: RecallBuilder
class RecallBuilder:
    def __init__(self, *, store: FileStore, llm: LLMClient) -> None:
        # Block: Dependencies
        self.store = store
        self.llm = llm

    def build_recall_pack(
        self,
        *,
        state: dict[str, Any],
        observation_text: str,
        recall_hint: dict[str, Any],
    ) -> dict[str, Any]:
        # Block: MemoryToggle
        if not state.get("memory_enabled", True):
            return self._empty_recall_pack()

        # Block: Context
        memory_set_id = state["selected_memory_set_id"]
        primary_intent = recall_hint["primary_intent"]
        scope_context = self._build_scope_context(recall_hint)
        raw_candidate_ids: set[str] = set()

        # Block: ActiveCommitments
        active_commitments = self._limit_memory_section(
            raw_items=self._build_active_commitments(memory_set_id=memory_set_id),
            limit=SECTION_LIMITS["active_commitments"],
        )
        self._collect_raw_candidate_ids(raw_candidate_ids, active_commitments)

        # Block: RelationshipModel
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

        # Block: UserModel
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

        # Block: SelfModel
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

        # Block: ActiveTopics
        active_topics = self._limit_mixed_section(
            raw_items=self._build_active_topics(
                memory_set_id=memory_set_id,
                topic_scope_filters=scope_context["topic_filters"],
            ),
            limit=SECTION_LIMITS["active_topics"],
        )
        self._collect_raw_candidate_ids(raw_candidate_ids, active_topics)

        # Block: EpisodicEvidence
        episodic_evidence = self._limit_digest_section(
            raw_items=self._build_episodic_evidence(
                memory_set_id=memory_set_id,
                scope_filters=scope_context["episode_scope_filters"],
                primary_intent=primary_intent,
            ),
            limit=SECTION_LIMITS["episodic_evidence"],
        )
        self._collect_raw_candidate_ids(raw_candidate_ids, episodic_evidence)

        # Block: AssociationSections
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

        # Block: MergeAssociation
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
        episodic_evidence = self._limit_digest_section(
            raw_items=episodic_evidence + association_sections["episodic_evidence"],
            limit=SECTION_LIMITS["episodic_evidence"],
        )

        # Block: ConflictSource
        selected_memory_items = active_commitments + relationship_model + user_model + self_model

        # Block: Conflicts
        conflicts = self._build_conflicts(
            memory_set_id=memory_set_id,
            selected_memory_items=selected_memory_items,
        )

        # Block: GlobalTrim
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

        # Block: SelectionSummary
        selected_memory_ids = self._collect_selected_ids(sections, key="memory_unit_id")
        selected_episode_digest_ids = self._collect_selected_ids(sections, key="episode_digest_id")
        association_selected_memory_ids = self._collect_selected_ids(
            sections,
            key="memory_unit_id",
            retrieval_lane="association",
        )
        association_selected_episode_digest_ids = self._collect_selected_ids(
            sections,
            key="episode_digest_id",
            retrieval_lane="association",
        )
        event_evidence = self._build_event_evidence(
            memory_set_id=memory_set_id,
            primary_intent=primary_intent,
            recall_hint=recall_hint,
            sections=sections,
        )
        selected_event_ids = [item["event_id"] for item in event_evidence]

        # Block: Result
        return {
            **sections,
            "event_evidence": event_evidence,
            "selected_memory_ids": selected_memory_ids,
            "selected_episode_digest_ids": selected_episode_digest_ids,
            "association_selected_memory_ids": association_selected_memory_ids,
            "association_selected_episode_digest_ids": association_selected_episode_digest_ids,
            "selected_event_ids": selected_event_ids,
            "candidate_count": len(raw_candidate_ids),
        }

    def _build_scope_context(self, recall_hint: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
        # Block: FocusScopes
        focus_specs = self._parse_focus_scopes(recall_hint.get("focus_scopes", []))
        primary_intent = recall_hint["primary_intent"]

        # Block: BaseScopes
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

        # Block: Result
        return {
            "user_filters": user_filters,
            "self_filters": self_filters,
            "relationship_filters": relationship_filters,
            "topic_filters": topic_filters,
            "episode_scope_filters": episode_scope_filters,
        }

    def _build_active_commitments(self, *, memory_set_id: str) -> list[dict[str, Any]]:
        # Block: Query
        records = self.store.list_memory_units_for_recall(
            memory_set_id=memory_set_id,
            include_memory_types=["commitment"],
            statuses=list(ACTIVE_MEMORY_STATUSES),
            commitment_states=list(ACTIVE_COMMITMENT_STATES),
            limit=SECTION_LIMITS["active_commitments"] * 3,
        )

        # Block: Result
        return [self._to_memory_item(record) for record in records]

    def _build_scope_memory_section(
        self,
        *,
        memory_set_id: str,
        scope_filters: list[tuple[str, str]],
        limit: int,
        exclude_memory_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        # Block: Empty
        if not scope_filters:
            return []

        # Block: Query
        records = self.store.list_memory_units_for_recall(
            memory_set_id=memory_set_id,
            scope_filters=scope_filters,
            exclude_memory_types=exclude_memory_types,
            statuses=list(ACTIVE_MEMORY_STATUSES),
            limit=limit,
        )

        # Block: Result
        return [self._to_memory_item(record) for record in records]

    def _build_active_topics(
        self,
        *,
        memory_set_id: str,
        topic_scope_filters: list[tuple[str, str]],
    ) -> list[dict[str, Any]]:
        # Block: TopicMemory
        topic_records = self.store.list_memory_units_for_recall(
            memory_set_id=memory_set_id,
            scope_filters=topic_scope_filters or None,
            scope_types=["topic"],
            statuses=list(ACTIVE_MEMORY_STATUSES),
            limit=SECTION_LIMITS["active_topics"] * 2,
        )

        # Block: TopicItems
        items = [self._to_memory_item(record) for record in topic_records]

        # Block: OpenLoops
        digest_records = self.store.list_episode_digests_for_recall(
            memory_set_id=memory_set_id,
            scope_filters=[],
            require_open_loops=True,
            limit=SECTION_LIMITS["active_topics"] * 3,
        )
        items.extend(self._to_topic_digest_item(record) for record in digest_records)

        # Block: Result
        return items

    def _build_episodic_evidence(
        self,
        *,
        memory_set_id: str,
        scope_filters: list[tuple[str, str]],
        primary_intent: str,
    ) -> list[dict[str, Any]]:
        # Block: Query
        records = self.store.list_episode_digests_for_recall(
            memory_set_id=memory_set_id,
            scope_filters=scope_filters,
            require_open_loops=primary_intent == "commitment_check",
            limit=SECTION_LIMITS["episodic_evidence"] * 4,
        )

        # Block: Result
        return [self._to_digest_item(record) for record in records]

    def _build_association_sections(
        self,
        *,
        state: dict[str, Any],
        observation_text: str,
        recall_hint: dict[str, Any],
        scope_context: dict[str, list[tuple[str, str]]],
    ) -> dict[str, list[dict[str, Any]]]:
        # Block: QuerySpecs
        query_specs = self._association_query_specs(observation_text, recall_hint)
        if not query_specs:
            return self._empty_association_sections()

        # Block: EmbeddingContext
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        embedding_role = selected_preset["roles"]["embedding"]
        embedding_profile_id = embedding_role["model_profile_id"]
        embedding_profile = state["model_profiles"][embedding_profile_id]
        embedding_dimension = embedding_role["embedding_dimension"]
        embedding_preset = self._embedding_preset(embedding_profile_id, embedding_dimension)

        # Block: QueryEmbeddings
        query_embeddings = self.llm.generate_embeddings(
            profile=embedding_profile,
            role_settings=embedding_role,
            texts=[spec["text"] for spec in query_specs],
        )

        # Block: CandidateState
        memory_candidates: dict[str, dict[str, Any]] = {}
        digest_candidates: dict[str, dict[str, Any]] = {}

        # Block: QueryLoop
        for spec, query_embedding in zip(query_specs, query_embeddings, strict=True):
            # Block: MemoryHits
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

            # Block: MemoryMerge
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

            # Block: DigestHits
            digest_hits = self.store.search_episode_digest_vector_entries(
                memory_set_id=state["selected_memory_set_id"],
                embedding_preset=embedding_preset,
                query_embedding=query_embedding,
                embedding_dimension=embedding_dimension,
                limit=self._association_search_limit(
                    source_kind="episode_digest",
                    query_kind=spec["kind"],
                ),
                scope_filters=None,
                require_open_loops=recall_hint["primary_intent"] == "commitment_check",
            )

            # Block: DigestMerge
            for hit in digest_hits:
                item = self._to_digest_item(hit["record"])
                item["retrieval_lane"] = "association"
                item["association_score"] = self._association_score(
                    recall_hint=recall_hint,
                    distance=hit["distance"],
                    item=item,
                    query_kind=spec["kind"],
                    query_weight=float(spec["weight"]),
                )
                self._merge_association_candidate(
                    candidates=digest_candidates,
                    item=item,
                    query_kind=spec["kind"],
                )

        # Block: Sections
        sections = self._empty_association_sections()
        for item in self._finalize_association_candidates(memory_candidates):
            section_name = self._section_name_for_memory_item(item)
            if section_name is None:
                continue
            sections[section_name].append(item)
        sections["episodic_evidence"].extend(self._finalize_association_candidates(digest_candidates))

        # Block: Sort
        for section_name, items in sections.items():
            items.sort(
                key=lambda item: (
                    float(item.get("association_score", 0.0)),
                    float(item.get("salience", 0.0)),
                ),
                reverse=True,
            )

        # Block: Result
        return sections

    def _association_query_specs(
        self,
        observation_text: str,
        recall_hint: dict[str, Any],
    ) -> list[dict[str, Any]]:
        # Block: BaseState
        specs: list[dict[str, Any]] = []
        normalized_observation = observation_text.strip()

        # Block: ObservationQuery
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

        # Block: EntityQuery
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

        # Block: TopicQuery
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

        # Block: Result
        return specs

    def _association_observation_query_text(
        self,
        observation_text: str,
        recall_hint: dict[str, Any],
    ) -> str:
        # Block: Empty
        if not observation_text:
            return ""

        # Block: Parts
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

        # Block: Result
        return "\n".join(parts)

    def _association_hint_terms(self, values: list[Any]) -> list[str]:
        # Block: State
        expanded: list[str] = []

        # Block: Expand
        for value in normalized_text_list(values, limit=4):
            expanded.extend(self._expanded_association_terms(value))

        # Block: Result
        return normalized_text_list(expanded, limit=8)

    def _expanded_association_terms(self, value: str) -> list[str]:
        # Block: State
        terms = [value]

        # Block: TaggedValue
        prefix, separator, suffix = value.partition(":")
        if separator and suffix:
            cleaned_suffix = suffix.strip().strip("<>").replace("|", " ")
            if cleaned_suffix:
                terms.append(cleaned_suffix)
                terms.append(f"{prefix} {cleaned_suffix}")

        # Block: Result
        return normalized_text_list(terms, limit=3)

    def _association_query_weight(
        self,
        *,
        query_kind: str,
        recall_hint: dict[str, Any],
    ) -> float:
        # Block: Base
        weight = ASSOCIATION_QUERY_KIND_WEIGHTS.get(query_kind, 1.0)
        primary_intent = recall_hint["primary_intent"]
        secondary_intents = set(self._secondary_intents(recall_hint))
        time_reference = recall_hint.get("time_reference")

        # Block: IntentAdjust
        if primary_intent == "reminisce" and query_kind == "observation":
            weight += 0.08
        if primary_intent in {"commitment_check", "meta_relationship"} and query_kind == "entity":
            weight += 0.12
        if primary_intent in {"consult", "check_state", "preference_query"} and query_kind == "topic":
            weight += 0.12

        # Block: SecondaryAdjust
        if "reminisce" in secondary_intents and query_kind == "observation":
            weight += 0.04
        if "meta_relationship" in secondary_intents and query_kind == "entity":
            weight += 0.05
        if {"consult", "check_state", "preference_query"} & secondary_intents and query_kind == "topic":
            weight += 0.04

        # Block: TimeAdjust
        if time_reference == "past" and query_kind == "observation":
            weight += 0.04
        if time_reference == "future" and query_kind == "entity":
            weight += 0.04
        if time_reference == "persistent" and query_kind == "topic":
            weight += 0.04

        # Block: Result
        return weight

    def _association_search_limit(
        self,
        *,
        source_kind: str,
        query_kind: str,
    ) -> int:
        # Block: Base
        if source_kind == "memory_unit":
            base_limit = ASSOCIATION_MEMORY_LIMIT
        else:
            base_limit = ASSOCIATION_DIGEST_LIMIT

        # Block: QueryAdjust
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
        # Block: Lookup
        record_id = self._record_id(item)
        existing = candidates.get(record_id)
        if existing is None:
            item["association_query_kinds"] = [query_kind]
            item["association_match_count"] = 1
            candidates[record_id] = item
            return

        # Block: Score
        existing["association_score"] = max(
            float(existing.get("association_score", 0.0)),
            float(item.get("association_score", 0.0)),
        )

        # Block: QueryKinds
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
        # Block: Finalize
        finalized: list[dict[str, Any]] = []
        for item in candidates.values():
            match_count = int(item.get("association_match_count", 1))
            if match_count > 1:
                item["association_score"] = float(item.get("association_score", 0.0)) + 0.03 * (match_count - 1)
            finalized.append(item)

        # Block: Result
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
        # Block: Base
        score = query_weight * (1.0 / (1.0 + max(distance, 0.0)))
        primary_intent = recall_hint["primary_intent"]
        secondary_intents = set(self._secondary_intents(recall_hint))

        # Block: QueryKindBoost
        item_scope_type = self._association_item_scope_type(item)
        if query_kind == "entity" and item_scope_type in {"user", "relationship"}:
            score += 0.05
        if query_kind == "topic" and item_scope_type == "topic":
            score += 0.05

        # Block: FocusBoost
        if self._focus_scope_matches(recall_hint.get("focus_scopes", []), item):
            score += 0.08

        # Block: IntentBoost
        if primary_intent == "reminisce" and item["source_kind"] == "episode_digest":
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
        if recall_hint.get("time_reference") == "past" and item["source_kind"] == "episode_digest":
            score += 0.05

        # Block: SecondaryBoost
        if "reminisce" in secondary_intents and item["source_kind"] == "episode_digest":
            score += 0.04
        if "meta_relationship" in secondary_intents and item_scope_type == "relationship":
            score += 0.04
        if {"consult", "check_state"} & secondary_intents and item_scope_type in {"user", "topic"}:
            score += 0.03
        if "preference_query" in secondary_intents and item.get("memory_type") == "preference":
            score += 0.03

        # Block: Result
        return score

    def _association_item_scope_type(self, item: dict[str, Any]) -> str:
        # Block: EpisodeDigest
        if item["source_kind"] == "episode_digest":
            return str(item.get("primary_scope_type", ""))

        # Block: MemoryUnit
        return str(item.get("scope_type", ""))

    def _focus_scope_matches(self, focus_scopes: list[Any], item: dict[str, Any]) -> bool:
        # Block: Parse
        focus_specs = self._parse_focus_scopes(focus_scopes)
        if item["source_kind"] == "episode_digest":
            scope_type = item["primary_scope_type"]
            scope_key = item["primary_scope_key"]
        else:
            scope_type = item["scope_type"]
            scope_key = item["scope_key"]

        # Block: Match
        return (scope_type, scope_key) in focus_specs

    def _section_name_for_memory_item(self, item: dict[str, Any]) -> str | None:
        # Block: Mapping
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
        # Block: State
        conflicts: list[dict[str, Any]] = []
        seen_conflict_keys: set[tuple[str, str, str, str, str]] = set()

        # Block: Scan
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

            # Block: ConflictEntry
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

        # Block: Result
        return conflicts

    def _build_event_evidence(
        self,
        *,
        memory_set_id: str,
        primary_intent: str,
        recall_hint: dict[str, Any],
        sections: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        # Block: Guard
        if not self._should_load_event_evidence(
            primary_intent=primary_intent,
            recall_hint=recall_hint,
            sections=sections,
        ):
            return []

        # Block: SelectedIds
        selected_event_ids = self._select_event_evidence_ids(
            primary_intent=primary_intent,
            sections=sections,
        )
        if not selected_event_ids:
            return []

        # Block: Load
        records = self.store.load_events_for_evidence(
            memory_set_id=memory_set_id,
            event_ids=selected_event_ids,
            limit=EVENT_EVIDENCE_LIMIT,
        )

        # Block: Result
        return [self._to_event_evidence_item(record) for record in records]

    def _should_load_event_evidence(
        self,
        *,
        primary_intent: str,
        recall_hint: dict[str, Any],
        sections: dict[str, list[dict[str, Any]]],
    ) -> bool:
        # Block: SourceGuard
        if not self._has_event_evidence_sources(primary_intent=primary_intent, sections=sections):
            return False

        # Block: IntentGuard
        if primary_intent in EVENT_EVIDENCE_INTENTS:
            return True

        # Block: TimeGuard
        return recall_hint.get("time_reference") == "past"

    def _has_event_evidence_sources(
        self,
        *,
        primary_intent: str,
        sections: dict[str, list[dict[str, Any]]],
    ) -> bool:
        # Block: Scan
        for section_name in self._event_evidence_section_priority(primary_intent):
            for item in sections.get(section_name, []):
                if self._prioritized_event_ids_for_item(item):
                    return True

        # Block: Result
        return False

    def _select_event_evidence_ids(
        self,
        *,
        primary_intent: str,
        sections: dict[str, list[dict[str, Any]]],
    ) -> list[str]:
        # Block: Sources
        sources = self._event_evidence_sources(
            primary_intent=primary_intent,
            sections=sections,
        )
        if not sources:
            return []

        # Block: RoundRobin
        selected: list[str] = []
        seen: set[str] = set()
        offset = 0
        while len(selected) < EVENT_EVIDENCE_LIMIT:
            added_in_round = False
            for event_ids in sources:
                if offset >= len(event_ids):
                    continue
                event_id = event_ids[offset]
                if event_id in seen:
                    continue
                selected.append(event_id)
                seen.add(event_id)
                added_in_round = True
                if len(selected) >= EVENT_EVIDENCE_LIMIT:
                    break
            if not added_in_round:
                break
            offset += 1

        # Block: Result
        return selected

    def _event_evidence_sources(
        self,
        *,
        primary_intent: str,
        sections: dict[str, list[dict[str, Any]]],
    ) -> list[list[str]]:
        # Block: State
        sources: list[list[str]] = []

        # Block: Collect
        for section_name in self._event_evidence_section_priority(primary_intent):
            for item in sections.get(section_name, []):
                prioritized_event_ids = self._prioritized_event_ids_for_item(item)
                if not prioritized_event_ids:
                    continue
                sources.append(prioritized_event_ids)

        # Block: Result
        return sources

    def _event_evidence_section_priority(self, primary_intent: str) -> list[str]:
        # Block: BaseOrder
        ordered = ["episodic_evidence"]
        recall_hint = {
            "primary_intent": primary_intent,
            "secondary_intents": [],
        }
        for section_name in self._section_priority(recall_hint):
            if section_name in {"episodic_evidence", "conflicts"}:
                continue
            ordered.append(section_name)

        # Block: Result
        return ordered

    def _prioritized_event_ids_for_item(self, item: dict[str, Any]) -> list[str]:
        # Block: EventIds
        if item["source_kind"] == "episode_digest":
            event_ids = item.get("linked_event_ids", [])
        else:
            event_ids = item.get("evidence_event_ids", [])

        # Block: Result
        return self._prioritized_event_ids(event_ids)

    def _prioritized_event_ids(self, event_ids: list[Any]) -> list[str]:
        # Block: Collect
        ordered: list[str] = []
        seen: set[str] = set()
        preferred_indexes = (1, 0, 2)
        for index in preferred_indexes:
            if index >= len(event_ids):
                continue
            value = event_ids[index]
            if not isinstance(value, str) or value in seen:
                continue
            ordered.append(value)
            seen.add(value)
        for value in event_ids:
            if not isinstance(value, str) or value in seen:
                continue
            ordered.append(value)
            seen.add(value)

        # Block: Result
        return ordered

    def _apply_global_limit(
        self,
        *,
        recall_hint: dict[str, Any],
        sections: dict[str, list[dict[str, Any]]],
    ) -> dict[str, list[dict[str, Any]]]:
        # Block: InitialState
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

        # Block: Order
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

        # Block: Result
        return trimmed

    def _section_priority(self, recall_hint: dict[str, Any]) -> list[str]:
        # Block: PrimaryOrder
        primary_intent = recall_hint["primary_intent"]
        ordered = self._primary_section_priority(primary_intent)

        # Block: SecondaryBoosts
        boosted_sections = self._secondary_section_boosts(
            self._secondary_intents(recall_hint),
        )

        # Block: Merge
        merged: list[str] = []
        if ordered:
            merged.append(ordered[0])
        for section_name in boosted_sections:
            if section_name not in merged:
                merged.append(section_name)
        for section_name in ordered:
            if section_name not in merged:
                merged.append(section_name)

        # Block: Result
        return merged

    def _primary_section_priority(self, primary_intent: str) -> list[str]:
        # Block: Mapping
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
        # Block: State
        boosted: list[str] = []

        # Block: Collect
        for intent in secondary_intents:
            for section_name in self._primary_section_priority(intent)[:2]:
                if section_name not in boosted:
                    boosted.append(section_name)

        # Block: Result
        return boosted

    def _secondary_intents(self, recall_hint: dict[str, Any]) -> list[str]:
        # Block: Normalize
        secondary_intents = normalized_text_list(
            recall_hint.get("secondary_intents", []),
            limit=2,
        )

        # Block: Result
        return secondary_intents

    def _limit_memory_section(
        self,
        *,
        raw_items: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        # Block: Selection
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

        # Block: Result
        return selected

    def _limit_digest_section(
        self,
        *,
        raw_items: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        # Block: Selection
        selected: list[dict[str, Any]] = []
        seen_record_ids: set[str] = set()
        for item in raw_items:
            record_id = item["episode_digest_id"]
            if record_id in seen_record_ids:
                continue
            selected.append(item)
            seen_record_ids.add(record_id)
            if len(selected) >= limit:
                break

        # Block: Result
        return selected

    def _limit_mixed_section(
        self,
        *,
        raw_items: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        # Block: Selection
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

        # Block: Result
        return selected

    def _parse_focus_scopes(self, scopes: list[Any]) -> list[tuple[str, str]]:
        # Block: State
        parsed: list[tuple[str, str]] = []

        # Block: Parse
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

        # Block: Result
        return parsed

    def _merged_scope_filters(
        self,
        defaults: list[tuple[str, str]],
        focus_specs: list[tuple[str, str]],
        *,
        allowed_scope_type: str | None,
    ) -> list[tuple[str, str]]:
        # Block: State
        merged: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        # Block: Defaults
        for scope_filter in defaults:
            if scope_filter in seen:
                continue
            merged.append(scope_filter)
            seen.add(scope_filter)

        # Block: FocusSpecs
        for scope_filter in focus_specs:
            if allowed_scope_type is not None and scope_filter[0] != allowed_scope_type:
                continue
            if scope_filter in seen:
                continue
            merged.append(scope_filter)
            seen.add(scope_filter)

        # Block: Result
        return merged

    def _empty_association_sections(self) -> dict[str, list[dict[str, Any]]]:
        # Block: Result
        return {
            "self_model": [],
            "user_model": [],
            "relationship_model": [],
            "active_topics": [],
            "episodic_evidence": [],
        }

    def _empty_recall_pack(self) -> dict[str, Any]:
        # Block: Result
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
            "selected_episode_digest_ids": [],
            "association_selected_memory_ids": [],
            "association_selected_episode_digest_ids": [],
            "selected_event_ids": [],
            "candidate_count": 0,
        }

    def _embedding_preset(self, embedding_profile_id: str, embedding_dimension: int) -> str:
        # Block: Identifier
        return f"{embedding_profile_id}:dim{embedding_dimension}"

    def _collect_raw_candidate_ids(self, raw_candidate_ids: set[str], items: list[dict[str, Any]]) -> None:
        # Block: Collect
        for item in items:
            raw_candidate_ids.add(self._record_id(item))

    def _collect_selected_ids(
        self,
        sections: dict[str, list[dict[str, Any]]],
        *,
        key: str,
        retrieval_lane: str | None = None,
    ) -> list[str]:
        # Block: State
        selected: list[str] = []
        seen: set[str] = set()

        # Block: Collect
        for section_items in sections.values():
            for item in section_items:
                if retrieval_lane is not None and item.get("retrieval_lane") != retrieval_lane:
                    continue
                value = item.get(key)
                if not isinstance(value, str) or value in seen:
                    continue
                selected.append(value)
                seen.add(value)

        # Block: Result
        return selected

    def _record_id(self, item: dict[str, Any]) -> str:
        # Block: MemoryUnit
        if "memory_unit_id" in item:
            return item["memory_unit_id"]

        # Block: EpisodeDigest
        return item["episode_digest_id"]

    def _to_memory_item(self, record: dict[str, Any]) -> dict[str, Any]:
        # Block: Item
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

    def _to_digest_item(self, record: dict[str, Any]) -> dict[str, Any]:
        # Block: Item
        return {
            "source_kind": "episode_digest",
            "episode_digest_id": record["episode_digest_id"],
            "episode_type": record["episode_type"],
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

    def _to_topic_digest_item(self, record: dict[str, Any]) -> dict[str, Any]:
        # Block: OpenLoopSummary
        open_loops = record.get("open_loops", [])
        summary_text = open_loops[0] if open_loops else record["summary_text"]

        # Block: Item
        return {
            "source_kind": "episode_digest",
            "episode_digest_id": record["episode_digest_id"],
            "episode_type": record["episode_type"],
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

    def _to_event_evidence_item(self, record: dict[str, Any]) -> dict[str, Any]:
        # Block: Base
        kind = str(record.get("kind", "event")).strip() or "event"
        item = {
            "event_id": record["event_id"],
            "kind": kind,
        }

        # Block: Slots
        anchor = self._event_evidence_anchor(record)
        topic = self._event_evidence_topic(record)
        decision_or_result = self._event_evidence_decision_or_result(record)
        tone_or_note = self._event_evidence_tone_or_note(record)
        if anchor is not None:
            item["anchor"] = anchor
        if topic is not None:
            item["topic"] = topic
        if decision_or_result is not None:
            item["decision_or_result"] = decision_or_result
        if tone_or_note is not None:
            item["tone_or_note"] = tone_or_note

        # Block: Result
        return item

    def _event_evidence_anchor(self, record: dict[str, Any]) -> str | None:
        # Block: Label
        kind = str(record.get("kind", "")).strip()
        label = {
            "decision": "判断",
            "observation": "会話",
            "reply": "返答",
        }.get(kind, "出来事")

        # Block: Timestamp
        created_at = str(record.get("created_at", "")).strip()
        if not created_at:
            return label
        normalized = created_at.replace("T", " ")
        return f"{normalized[:16]} の{label}"

    def _event_evidence_topic(self, record: dict[str, Any]) -> str | None:
        # Block: KindSwitch
        kind = str(record.get("kind", "")).strip()
        if kind not in {"observation", "reply"}:
            return None

        # Block: Result
        return self._short_event_text(record.get("text"))

    def _event_evidence_decision_or_result(self, record: dict[str, Any]) -> str | None:
        # Block: KindGuard
        kind = str(record.get("kind", "")).strip()
        if kind != "decision":
            return None

        # Block: ResultKind
        result_kind = str(record.get("result_kind", "")).strip()
        if result_kind:
            return f"{result_kind} を選んだ"

        # Block: Fallback
        return "応答方針を決めた"

    def _event_evidence_tone_or_note(self, record: dict[str, Any]) -> str | None:
        # Block: KindSwitch
        kind = str(record.get("kind", "")).strip()
        if kind == "decision":
            reason_code = str(record.get("reason_code", "")).strip()
            return f"reason={reason_code}" if reason_code else None
        if kind == "reply":
            return "assistant_reply"
        if kind == "observation":
            return "user_message"
        return None

    def _short_event_text(self, value: Any) -> str | None:
        # Block: Normalize
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.split()).strip()
        if not normalized:
            return None

        # Block: Result
        if len(normalized) <= 56:
            return normalized
        return normalized[:56].rstrip() + "..."
