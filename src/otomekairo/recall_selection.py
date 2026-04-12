from __future__ import annotations

import json
from typing import Any

from otomekairo.llm import LLMContractError, LLMError
from otomekairo.llm_contracts import RECALL_PACK_SECTION_NAMES
from otomekairo.memory_utils import normalized_text_list
from otomekairo.recall_association import ACTIVE_MEMORY_STATUSES


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
        observation_text: str,
        recall_hint: dict[str, Any],
        candidate_sections: dict[str, list[dict[str, Any]]],
        conflicts: list[dict[str, Any]],
        role_definition: dict[str, Any],
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
                observation_text=observation_text,
                recall_hint=recall_hint,
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
                failure_stage="build_source_pack",
            ) from exc

        # selection
        try:
            payload = self.llm.generate_recall_pack_selection(
                role_definition=role_definition,
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
        observation_text: str,
        recall_hint: dict[str, Any],
        candidate_sections: dict[str, list[dict[str, Any]]],
        conflicts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # section 群
        source_sections: list[dict[str, Any]] = []
        for section_name in RECALL_PACK_SECTION_NAMES:
            items = candidate_sections.get(section_name, [])
            if not items:
                continue
            source_sections.append(
                {
                    "section_name": section_name,
                    "candidates": [
                        self._recall_pack_selection_candidate_source_item(
                            candidate_ref=f"candidate:{section_name}:{index}",
                            item=item,
                        )
                        for index, item in enumerate(items, start=1)
                    ],
                }
            )

        # conflict 群
        source_conflicts = [
            self._recall_pack_selection_conflict_source_item(
                conflict_ref=f"conflict:{index}",
                item=item,
            )
            for index, item in enumerate(conflicts, start=1)
        ]

        # 結果
        return {
            "observation_text": observation_text.strip(),
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
            "candidate_ref": candidate_ref,
            "source_kind": item["source_kind"],
            "retrieval_lane": item.get("retrieval_lane", "structured"),
            "summary_text": item["summary_text"],
            "salience": item["salience"],
        }
        if item.get("association_score") is not None:
            payload["association_score"] = round(float(item["association_score"]), 4)

        # 記憶単位
        if item["source_kind"] == "memory_unit":
            payload["memory_type"] = item["memory_type"]
            payload["scope_type"] = item["scope_type"]
            payload["scope_key"] = item["scope_key"]
            payload["status"] = item["status"]
            if item.get("commitment_state") is not None:
                payload["commitment_state"] = item["commitment_state"]
            return payload

        # Episode要約
        if item["source_kind"] == "episode":
            payload["primary_scope_type"] = item["primary_scope_type"]
            payload["primary_scope_key"] = item["primary_scope_key"]
            payload["open_loops"] = item.get("open_loops", [])
            if item.get("outcome_text") is not None:
                payload["outcome_text"] = item["outcome_text"]
            return payload

        raise ValueError(f"unsupported candidate source_kind: {item['source_kind']}")

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
        return {
            "conflict_ref": conflict_ref,
            "compare_key": item["compare_key"],
            "variant_summaries": variant_summaries,
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
            section_name = section["section_name"]
            items = candidate_sections[section_name]
            for candidate_source, item in zip(section["candidates"], items, strict=True):
                candidate_lookup[candidate_source["candidate_ref"]] = {
                    "section_name": section_name,
                    "item": item,
                }

        # conflict lookup
        conflict_lookup: dict[str, dict[str, Any]] = {}
        for conflict_source, conflict in zip(source_pack["conflicts"], conflicts, strict=True):
            conflict_lookup[conflict_source["conflict_ref"]] = conflict

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

        # section ごと反映
        for section_payload in payload["section_selection"]:
            section_name = section_payload["section_name"]
            selected_section_order.append(section_name)
            section_items = selected_sections[section_name]
            for candidate_ref in section_payload["candidate_refs"]:
                selected_candidate_refs.append(candidate_ref)
                item = candidate_lookup[candidate_ref]["item"]
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
            "result_status": "succeeded",
            "failure_reason": None,
        }
