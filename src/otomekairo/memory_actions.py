from __future__ import annotations

import uuid
from typing import Any

from otomekairo.memory_utils import clamp_score, merged_cycle_ids, merged_event_ids
from otomekairo.store import FileStore


# Block: Resolver
class MemoryActionResolver:
    def __init__(self, *, store: FileStore) -> None:
        # Block: Dependencies
        self.store = store

    def resolve_memory_actions(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        event_ids: list[str],
        cycle_ids: list[str],
        candidate: dict[str, Any],
    ) -> list[dict[str, Any]]:
        # Block: Lookup
        matches = self.store.find_memory_units_for_compare(
            memory_set_id=memory_set_id,
            memory_type=candidate["memory_type"],
            scope_type=candidate["scope_type"],
            scope_key=candidate["scope_key"],
            subject_ref=candidate["subject_ref"],
            predicate=candidate["predicate"],
        )

        # Block: CreatePath
        if not matches:
            new_unit = self.build_new_memory_unit(
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
                candidate=candidate,
            )
            return [
                self.build_memory_action(
                    operation="create",
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    memory_unit=new_unit,
                    related_memory_unit_ids=[],
                    before_snapshot=None,
                    after_snapshot=new_unit,
                    reason=candidate["reason"],
                    event_ids=event_ids,
                )
            ]

        # Block: PrimaryMatch
        existing = matches[0]

        # Block: SamePath
        if self.is_same_memory(existing, candidate):
            updated_unit = self.build_reinforced_memory_unit(
                existing=existing,
                candidate=candidate,
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
            )
            return [
                self.build_memory_action(
                    operation="reinforce",
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    memory_unit=updated_unit,
                    related_memory_unit_ids=[],
                    before_snapshot=existing,
                    after_snapshot=updated_unit,
                    reason=candidate["reason"],
                    event_ids=event_ids,
                )
            ]

        # Block: RefinePath
        if self.can_refine(existing, candidate):
            updated_unit = self.build_refined_memory_unit(
                existing=existing,
                candidate=candidate,
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
            )
            return [
                self.build_memory_action(
                    operation="refine",
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    memory_unit=updated_unit,
                    related_memory_unit_ids=[],
                    before_snapshot=existing,
                    after_snapshot=updated_unit,
                    reason=candidate["reason"],
                    event_ids=event_ids,
                )
            ]

        # Block: SupersedePath
        new_unit = self.build_new_memory_unit(
            memory_set_id=memory_set_id,
            finished_at=finished_at,
            event_ids=event_ids,
            cycle_ids=cycle_ids,
            candidate=candidate,
        )
        superseded_unit = {
            **existing,
            "status": "superseded",
            "salience": min(clamp_score(existing["salience"]), 0.2),
            "valid_to": finished_at,
        }
        return [
            self.build_memory_action(
                operation="supersede",
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                memory_unit=superseded_unit,
                related_memory_unit_ids=[new_unit["memory_unit_id"]],
                before_snapshot=existing,
                after_snapshot=superseded_unit,
                reason=candidate["reason"],
                event_ids=event_ids,
            ),
            self.build_memory_action(
                operation="create",
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                memory_unit=new_unit,
                related_memory_unit_ids=[existing["memory_unit_id"]],
                before_snapshot=None,
                after_snapshot=new_unit,
                reason=candidate["reason"],
                event_ids=event_ids,
            ),
        ]

    def build_new_memory_unit(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        event_ids: list[str],
        cycle_ids: list[str],
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        # Block: Record
        return {
            "memory_unit_id": f"memory_unit:{uuid.uuid4().hex}",
            "memory_set_id": memory_set_id,
            "memory_type": candidate["memory_type"],
            "scope_type": candidate["scope_type"],
            "scope_key": candidate["scope_key"],
            "subject_ref": candidate["subject_ref"],
            "predicate": candidate["predicate"],
            "object_ref_or_value": candidate.get("object_ref_or_value"),
            "summary_text": candidate["summary_text"].strip(),
            "status": candidate["status"],
            "commitment_state": candidate.get("commitment_state"),
            "confidence": clamp_score(candidate["confidence"]),
            "salience": clamp_score(candidate["salience"]),
            "formed_at": finished_at,
            "last_confirmed_at": finished_at if candidate["status"] == "confirmed" else None,
            "valid_from": candidate.get("valid_from"),
            "valid_to": candidate.get("valid_to"),
            "evidence_event_ids": event_ids,
            "evidence_cycle_ids": cycle_ids,
            "qualifiers": candidate.get("qualifiers", {}),
        }

    def build_reinforced_memory_unit(
        self,
        *,
        existing: dict[str, Any],
        candidate: dict[str, Any],
        finished_at: str,
        event_ids: list[str],
        cycle_ids: list[str],
    ) -> dict[str, Any]:
        # Block: Status
        next_status = existing["status"]
        if existing["status"] == "inferred" and candidate["status"] == "confirmed":
            next_status = "confirmed"
        if existing["status"] == "dormant":
            next_status = candidate["status"]

        # Block: Record
        return {
            **existing,
            "summary_text": existing["summary_text"],
            "status": next_status,
            "commitment_state": candidate.get("commitment_state") or existing.get("commitment_state"),
            "confidence": max(clamp_score(existing["confidence"]), clamp_score(candidate["confidence"])),
            "salience": max(clamp_score(existing["salience"]), clamp_score(candidate["salience"])),
            "last_confirmed_at": finished_at,
            "evidence_event_ids": merged_event_ids(existing.get("evidence_event_ids", []), event_ids),
            "evidence_cycle_ids": merged_cycle_ids(existing.get("evidence_cycle_ids", []), cycle_ids),
            "qualifiers": {
                **existing.get("qualifiers", {}),
                **candidate.get("qualifiers", {}),
            },
        }

    def build_refined_memory_unit(
        self,
        *,
        existing: dict[str, Any],
        candidate: dict[str, Any],
        finished_at: str,
        event_ids: list[str],
        cycle_ids: list[str],
    ) -> dict[str, Any]:
        # Block: Status
        next_status = existing["status"]
        if candidate["status"] == "confirmed":
            next_status = "confirmed"

        # Block: Record
        return {
            **existing,
            "object_ref_or_value": candidate.get("object_ref_or_value"),
            "summary_text": candidate["summary_text"].strip(),
            "status": next_status,
            "commitment_state": candidate.get("commitment_state") or existing.get("commitment_state"),
            "confidence": max(clamp_score(existing["confidence"]), clamp_score(candidate["confidence"])),
            "salience": max(clamp_score(existing["salience"]), clamp_score(candidate["salience"])),
            "last_confirmed_at": finished_at,
            "valid_from": candidate.get("valid_from") or existing.get("valid_from"),
            "valid_to": candidate.get("valid_to") or existing.get("valid_to"),
            "evidence_event_ids": merged_event_ids(existing.get("evidence_event_ids", []), event_ids),
            "evidence_cycle_ids": merged_cycle_ids(existing.get("evidence_cycle_ids", []), cycle_ids),
            "qualifiers": {
                **existing.get("qualifiers", {}),
                **candidate.get("qualifiers", {}),
            },
        }

    def build_memory_action(
        self,
        *,
        operation: str,
        memory_set_id: str,
        finished_at: str,
        memory_unit: dict[str, Any],
        related_memory_unit_ids: list[str],
        before_snapshot: dict[str, Any] | None,
        after_snapshot: dict[str, Any] | None,
        reason: str,
        event_ids: list[str],
    ) -> dict[str, Any]:
        # Block: Action
        return {
            "operation": operation,
            "revision_id": f"revision:{uuid.uuid4().hex}",
            "memory_set_id": memory_set_id,
            "memory_unit_id": memory_unit["memory_unit_id"],
            "occurred_at": finished_at,
            "related_memory_unit_ids": related_memory_unit_ids,
            "before_snapshot": before_snapshot,
            "after_snapshot": after_snapshot,
            "reason": reason,
            "evidence_event_ids": event_ids,
            "memory_unit": memory_unit,
        }

    def is_same_memory(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # Block: ObjectCompare
        if existing.get("object_ref_or_value") != candidate.get("object_ref_or_value"):
            return False

        # Block: CommitmentCompare
        if existing.get("commitment_state") != candidate.get("commitment_state"):
            return False

        # Block: QualifierCompare
        return existing.get("qualifiers", {}) == candidate.get("qualifiers", {})

    def can_refine(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # Block: PolarityGuard
        existing_polarity = existing.get("qualifiers", {}).get("polarity")
        candidate_polarity = candidate.get("qualifiers", {}).get("polarity")
        if existing_polarity is not None and candidate_polarity is not None and existing_polarity != candidate_polarity:
            return False

        # Block: ObjectGuard
        if existing.get("object_ref_or_value") != candidate.get("object_ref_or_value"):
            return False

        # Block: ContentCheck
        return existing.get("summary_text") != candidate["summary_text"].strip() or existing.get("qualifiers", {}) != candidate.get(
            "qualifiers",
            {},
        )
