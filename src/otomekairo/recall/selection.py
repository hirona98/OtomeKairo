from __future__ import annotations

import json
from typing import Any

from otomekairo.llm.client import LLMContractError, LLMError
from otomekairo.llm.contexts import PersonaContext
from otomekairo.llm.contracts import RECALL_PACK_SECTION_NAMES
from otomekairo.memory.utils import normalized_text_list
from otomekairo.recall.association import ACTIVE_MEMORY_STATUSES


SECTION_LIMITS = {
    "self_model": 4,
    "person_model": 8,
    "relationship_model": 8,
    "active_topics": 4,
    "active_commitments": 8,
    "episodic_evidence": 6,
    "conflicts": 4,
}
GLOBAL_RECALL_LIMIT = 40


class RecallPackSelectionError(LLMError):
    def __init__(
        self,
        message: str,
        *,
        recall_hint_summary: dict[str, Any],
        recall_pack_selection: dict[str, Any],
        failure_stage: str,
    ) -> None:
        super().__init__(message)
        self.recall_hint_summary = recall_hint_summary
        self.recall_pack_selection = recall_pack_selection
        self.failure_stage = failure_stage


class RecallSelectionMixin:
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
                    "variant_summaries": normalized_text_list(
                        [
                            str(match["summary_text"]).strip()
                            for match in active_matches
                            if isinstance(match.get("summary_text"), str) and match["summary_text"].strip()
                        ],
                        limit=3,
                    ),
                    "summary_text": "同じ対象について異なる理解が併存している。",
                }
            )
            seen_conflict_keys.add(compare_key)
            if len(conflicts) >= SECTION_LIMITS["conflicts"]:
                break

        # 結果
        return conflicts

    def _select_recall_pack_sections(
        self,
        *,
        augmented_query_text: str,
        recall_hint: dict[str, Any],
        candidate_sections: dict[str, list[dict[str, Any]]],
        conflicts: list[dict[str, Any]],
        model_config: dict[str, Any],
        persona_context: PersonaContext,
    ) -> dict[str, Any]:
        # 初期状態
        trace = self._empty_recall_pack_selection()
        trace["candidate_section_counts"] = {
            section_name: len(candidate_sections.get(section_name, []))
            for section_name in RECALL_PACK_SECTION_NAMES
        }
        if not any(trace["candidate_section_counts"].values()) and not conflicts:
            return {
                "sections": self._empty_selected_sections(),
                "recall_pack_selection": trace,
            }

        # source pack
        try:
            source_pack = self._build_recall_pack_selection_source_pack(
                augmented_query_text=augmented_query_text,
                recall_hint=recall_hint,
                candidate_sections=candidate_sections,
                conflicts=conflicts,
                persona_context=persona_context,
            )
        except (KeyError, TypeError, ValueError) as exc:
            trace["result_status"] = "failed"
            trace["failure_reason"] = str(exc)
            raise RecallPackSelectionError(
                str(exc),
                recall_hint_summary=recall_hint,
                recall_pack_selection=trace,
                failure_stage="build_source_pack",
            ) from exc

        # selection
        try:
            payload = self.llm.generate_recall_pack_selection(
                model_config=model_config,
                persona_context=persona_context,
                source_pack=source_pack,
            )
        except LLMContractError as exc:
            trace["result_status"] = "failed"
            trace["failure_reason"] = str(exc)
            raise RecallPackSelectionError(
                str(exc),
                recall_hint_summary=recall_hint,
                recall_pack_selection=trace,
                failure_stage="contract_validation",
            ) from exc
        except LLMError as exc:
            trace["result_status"] = "failed"
            trace["failure_reason"] = str(exc)
            raise RecallPackSelectionError(
                str(exc),
                recall_hint_summary=recall_hint,
                recall_pack_selection=trace,
                failure_stage="llm_generation",
            ) from exc

        # 反映
        try:
            selection_result = self._apply_recall_pack_selection(
                payload=payload,
                source_pack=source_pack,
                candidate_sections=candidate_sections,
                conflicts=conflicts,
            )
        except (KeyError, TypeError, ValueError) as exc:
            trace["result_status"] = "failed"
            trace["failure_reason"] = str(exc)
            raise RecallPackSelectionError(
                str(exc),
                recall_hint_summary=recall_hint,
                recall_pack_selection=trace,
                failure_stage="contract_validation",
            ) from exc

        # 結果
        trace["selected_section_order"] = selection_result["selected_section_order"]
        trace["selected_candidate_refs"] = selection_result["selected_candidate_refs"]
        trace["dropped_candidate_refs"] = selection_result["dropped_candidate_refs"]
        trace["conflict_summary_count"] = selection_result["conflict_summary_count"]
        return {
            "sections": selection_result["sections"],
            "recall_pack_selection": trace,
        }

    def _build_recall_pack_selection_source_pack(
        self,
        *,
        augmented_query_text: str,
        recall_hint: dict[str, Any],
        candidate_sections: dict[str, list[dict[str, Any]]],
        conflicts: list[dict[str, Any]],
        persona_context: PersonaContext,
    ) -> dict[str, Any]:
        # section 群
        source_sections: list[dict[str, Any]] = []
        candidate_index = 0
        for section_name in RECALL_PACK_SECTION_NAMES:
            items = candidate_sections.get(section_name, [])
            if not items:
                continue
            section_payload: dict[str, Any] = {"section": section_name}
            for source_kind, key in (
                ("memory_unit", "memory_candidates"),
                ("episode", "episode_candidates"),
            ):
                compact_items: list[dict[str, Any]] = []
                for item in items:
                    if item.get("source_kind") != source_kind:
                        continue
                    candidate_index += 1
                    compact_items.append(
                        self._recall_pack_selection_candidate_source_item(
                            candidate_ref=f"c{candidate_index}",
                            item=item,
                        )
                    )
                if compact_items:
                    section_payload[key] = compact_items
            source_sections.append(section_payload)

        # conflict 群
        source_conflicts = [
            self._recall_pack_selection_conflict_source_item(
                conflict_ref=f"x{index}",
                item=item,
            )
            for index, item in enumerate(conflicts, start=1)
        ]

        # 結果
        return {
            "persona_context": persona_context.to_prompt_payload(),
            "augmented_query_text": augmented_query_text.strip(),
            "recall_hint": recall_hint,
            "constraints": {
                "global_recall_limit": GLOBAL_RECALL_LIMIT,
                "section_limits": dict(SECTION_LIMITS),
            },
            "candidate_sections": source_sections,
            "conflicts": source_conflicts,
        }

    def _recall_pack_selection_candidate_source_item(
        self,
        *,
        candidate_ref: str,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        # 共通項目
        payload = {
            "ref": candidate_ref,
            "summary": item["summary_text"],
            "salience": item["salience"],
        }
        retrieval_lane = item.get("retrieval_lane", "structured")
        if retrieval_lane != "structured":
            payload["lane"] = retrieval_lane
        if item.get("association_score") is not None:
            payload["score"] = round(float(item["association_score"]), 4)
        if item.get("retrieval_lane") == "relation_index":
            payload["relation"] = [
                item.get("relation_derived_status"),
                item.get("relation_source_ref"),
                item.get("relation_target_ref"),
                item.get("relation_predicate"),
            ]

        # 記憶単位
        if item["source_kind"] == "memory_unit":
            payload["memory_type"] = item["memory_type"]
            payload["scope"] = [item["scope_type"], item["scope_key"]]
            payload["status"] = item["status"]
            if item.get("commitment_state") is not None:
                payload["commitment_state"] = item["commitment_state"]
            if isinstance(item.get("memory_link_summary"), dict):
                payload["links"] = self._recall_pack_selection_link_summary(
                    item["memory_link_summary"]
                )
            return payload

        # Episode要約
        if item["source_kind"] == "episode":
            payload["primary_scope"] = [
                item["primary_scope_type"],
                item["primary_scope_key"],
            ]
            payload["open_loops"] = item.get("open_loops", [])
            if item.get("outcome_text") is not None:
                payload["outcome_text"] = item["outcome_text"]
            return payload

        raise ValueError(f"unsupported candidate source_kind: {item['source_kind']}")

    def _recall_pack_selection_link_summary(
        self,
        memory_link_summary: dict[str, Any],
    ) -> dict[str, Any]:
        examples = [
            [
                item.get("label"),
                item.get("direction"),
                item.get("related_summary_text") or item.get("summary_text"),
            ]
            for item in memory_link_summary.get("representative_links", [])
            if isinstance(item, dict)
        ]
        payload: dict[str, Any] = {
            "counts": memory_link_summary.get("label_counts", {}),
        }
        if examples:
            payload["examples"] = examples
        return payload

    def _recall_pack_selection_conflict_source_item(
        self,
        *,
        conflict_ref: str,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        # variant 群
        variant_summaries = normalized_text_list(
            item.get("variant_summaries", []),
            limit=3,
        )
        if not variant_summaries:
            raise ValueError("conflict source requires variant_summaries.")

        # 結果
        compare_key = item["compare_key"]
        return {
            "ref": conflict_ref,
            "compare": [
                compare_key["memory_type"],
                compare_key["scope_type"],
                compare_key["scope_key"],
                compare_key["subject_ref"],
                compare_key["predicate"],
            ],
            "variants": variant_summaries,
        }

    def _apply_recall_pack_selection(
        self,
        *,
        payload: dict[str, Any],
        source_pack: dict[str, Any],
        candidate_sections: dict[str, list[dict[str, Any]]],
        conflicts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # candidate lookup
        candidate_lookup: dict[str, dict[str, Any]] = {}
        for section in source_pack["candidate_sections"]:
            section_name = section["section"]
            items = candidate_sections[section_name]
            for source_kind, key in (
                ("memory_unit", "memory_candidates"),
                ("episode", "episode_candidates"),
            ):
                source_items = section.get(key, [])
                original_items = [
                    item for item in items if item.get("source_kind") == source_kind
                ]
                for candidate_source, item in zip(source_items, original_items, strict=True):
                    candidate_lookup[candidate_source["ref"]] = {
                        "section_name": section_name,
                        "item": item,
                    }

        # conflict lookup
        conflict_lookup: dict[str, dict[str, Any]] = {}
        for conflict_source, conflict in zip(source_pack["conflicts"], conflicts, strict=True):
            conflict_lookup[conflict_source["ref"]] = conflict

        # 初期状態
        selected_sections = self._empty_selected_sections(
            conflicts=self._selected_conflicts_from_payload(
                payload=payload,
                conflict_lookup=conflict_lookup,
            )
        )
        selected_section_order: list[str] = []
        selected_candidate_refs: list[str] = []
        dropped_candidate_refs: list[str] = []
        used_record_ids: set[str] = set()
        remaining = GLOBAL_RECALL_LIMIT - len(selected_sections["conflicts"])

        # 優先順に反映
        seen_sections: set[str] = set()
        for raw_candidate_ref in payload["selected_candidate_refs"]:
            candidate_ref = raw_candidate_ref.strip()
            candidate_entry = candidate_lookup[candidate_ref]
            section_name = candidate_entry["section_name"]
            if section_name not in seen_sections:
                selected_section_order.append(section_name)
                seen_sections.add(section_name)
            section_items = selected_sections[section_name]
            selected_candidate_refs.append(candidate_ref)
            item = candidate_entry["item"]
            record_id = self._record_id(item)
            if remaining <= 0:
                dropped_candidate_refs.append(candidate_ref)
                continue
            if len(section_items) >= SECTION_LIMITS[section_name]:
                dropped_candidate_refs.append(candidate_ref)
                continue
            if record_id in used_record_ids:
                dropped_candidate_refs.append(candidate_ref)
                continue
            section_items.append(item)
            used_record_ids.add(record_id)
            remaining -= 1

        # 結果
        return {
            "sections": selected_sections,
            "selected_section_order": selected_section_order,
            "selected_candidate_refs": selected_candidate_refs,
            "dropped_candidate_refs": dropped_candidate_refs,
            "conflict_summary_count": len(selected_sections["conflicts"]),
        }

    def _selected_conflicts_from_payload(
        self,
        *,
        payload: dict[str, Any],
        conflict_lookup: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # source 順維持
        summary_by_ref = {
            item["conflict_ref"]: item["summary_text"]
            for item in payload["conflict_summaries"]
        }
        return [
            {
                **conflict,
                "summary_text": summary_by_ref[conflict_ref],
            }
            for conflict_ref, conflict in conflict_lookup.items()
        ]

    def _empty_selected_sections(
        self,
        *,
        conflicts: list[dict[str, Any]] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        # 結果
        sections = {
            section_name: []
            for section_name in RECALL_PACK_SECTION_NAMES
        }
        sections["conflicts"] = conflicts or []
        return sections

    def _empty_recall_pack_selection(self) -> dict[str, Any]:
        return {
            "candidate_section_counts": {
                section_name: 0
                for section_name in RECALL_PACK_SECTION_NAMES
            },
            "selected_section_order": [],
            "selected_candidate_refs": [],
            "dropped_candidate_refs": [],
            "conflict_summary_count": 0,
            "memory_link_count": 0,
            "memory_link_label_counts": {},
            "memory_link_representative_links": [],
            "result_status": "succeeded",
            "failure_reason": None,
        }
