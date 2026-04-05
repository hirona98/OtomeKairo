from __future__ import annotations

import json
from typing import Any

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


# Block: RecallBuilder
class RecallBuilder:
    def __init__(self, *, store: FileStore) -> None:
        # Block: Dependencies
        self.store = store

    def build_recall_pack(
        self,
        *,
        state: dict[str, Any],
        recall_hint: dict[str, Any],
    ) -> dict[str, Any]:
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

        # Block: ConflictSource
        selected_memory_items = active_commitments + relationship_model + user_model + self_model

        # Block: Conflicts
        conflicts = self._build_conflicts(
            memory_set_id=memory_set_id,
            selected_memory_items=selected_memory_items,
        )

        # Block: GlobalTrim
        sections = self._apply_global_limit(
            primary_intent=primary_intent,
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

        # Block: Result
        return {
            **sections,
            "event_evidence": [],
            "selected_memory_ids": selected_memory_ids,
            "selected_episode_digest_ids": selected_episode_digest_ids,
            "selected_event_ids": [],
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

    def _apply_global_limit(
        self,
        *,
        primary_intent: str,
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
        for section_name in self._section_priority(primary_intent):
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

    def _section_priority(self, primary_intent: str) -> list[str]:
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

    def _collect_raw_candidate_ids(self, raw_candidate_ids: set[str], items: list[dict[str, Any]]) -> None:
        # Block: Collect
        for item in items:
            raw_candidate_ids.add(self._record_id(item))

    def _collect_selected_ids(self, sections: dict[str, list[dict[str, Any]]], *, key: str) -> list[str]:
        # Block: State
        selected: list[str] = []
        seen: set[str] = set()

        # Block: Collect
        for section_items in sections.values():
            for item in section_items:
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
            "salience": record["salience"],
            "formed_at": record["formed_at"],
            "linked_event_ids": record.get("linked_event_ids", []),
        }
