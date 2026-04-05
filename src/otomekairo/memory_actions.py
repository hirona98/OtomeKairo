from __future__ import annotations

import uuid
from typing import Any

from otomekairo.memory_utils import (
    clamp_score,
    merged_cycle_ids,
    merged_event_ids,
    timestamp_sort_key,
)
from otomekairo.store import FileStore


# Block: Constants
NO_WRITE_CONFIDENCE_FLOOR = 0.35
NO_WRITE_SALIENCE_FLOOR = 0.2
INTERPRETATION_CONFIDENCE_FLOOR = 0.58
RELATION_CONFIDENCE_FLOOR = 0.6

ACTIVE_MEMORY_STATUSES = {"inferred", "confirmed"}
REVIVABLE_MEMORY_STATUSES = {"inferred", "confirmed", "dormant"}
DIRECT_SOURCE_VALUES = {
    "explicit_statement",
    "explicit_confirmation",
    "explicit_correction",
}
CONTROL_QUALIFIER_KEYS = {
    "allow_parallel",
    "negates_previous",
    "replace_prior",
    "source",
}
PARALLEL_MEMORY_TYPES = {
    "commitment",
    "interpretation",
    "preference",
    "relation",
}


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
        # Block: Candidate
        normalized_candidate = self._normalized_candidate(candidate)
        if self._should_noop_candidate(normalized_candidate):
            return []

        # Block: Lookup
        matches = self._ordered_matches(
            self.store.find_memory_units_for_compare(
                memory_set_id=memory_set_id,
                memory_type=normalized_candidate["memory_type"],
                scope_type=normalized_candidate["scope_type"],
                scope_key=normalized_candidate["scope_key"],
                subject_ref=normalized_candidate["subject_ref"],
                predicate=normalized_candidate["predicate"],
            )
        )

        # Block: SpecialStatus
        if normalized_candidate["status"] == "revoked":
            return self._resolve_revoke_request(
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
                candidate=normalized_candidate,
                matches=matches,
            )
        if normalized_candidate["status"] == "dormant":
            return self._resolve_dormant_request(
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
                candidate=normalized_candidate,
                matches=matches,
            )

        # Block: MatchSelection
        same_memory_match = self._same_memory_match(matches, normalized_candidate)
        primary_match = self._primary_match(matches, normalized_candidate)

        # Block: CreatePath
        if same_memory_match is None and primary_match is None:
            return [
                self._build_create_action(
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    event_ids=event_ids,
                    cycle_ids=cycle_ids,
                    candidate=normalized_candidate,
                    related_memory_unit_ids=[],
                )
            ]

        # Block: ReinforcePath
        if same_memory_match is not None:
            updated_unit = self.build_reinforced_memory_unit(
                existing=same_memory_match,
                candidate=normalized_candidate,
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
                    before_snapshot=same_memory_match,
                    after_snapshot=updated_unit,
                    reason=normalized_candidate["reason"],
                    event_ids=event_ids,
                )
            ]

        # Block: RefinePath
        if primary_match is not None and self.can_refine(primary_match, normalized_candidate):
            updated_unit = self.build_refined_memory_unit(
                existing=primary_match,
                candidate=normalized_candidate,
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
                    before_snapshot=primary_match,
                    after_snapshot=updated_unit,
                    reason=normalized_candidate["reason"],
                    event_ids=event_ids,
                )
            ]

        # Block: RevokeWithReplacement
        if primary_match is not None and self._should_revoke_with_replacement(primary_match, normalized_candidate):
            return self._build_revoke_and_create_actions(
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
                candidate=normalized_candidate,
                matches=matches,
            )

        # Block: ParallelPath
        if primary_match is not None and self._should_create_parallel(primary_match, normalized_candidate):
            return [
                self._build_create_action(
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    event_ids=event_ids,
                    cycle_ids=cycle_ids,
                    candidate=normalized_candidate,
                    related_memory_unit_ids=[],
                )
            ]

        # Block: SupersedePath
        if primary_match is not None and self._should_supersede(primary_match, normalized_candidate):
            new_unit = self.build_new_memory_unit(
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
                candidate=normalized_candidate,
            )
            superseded_unit = self.build_superseded_memory_unit(
                existing=primary_match,
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
            )
            return [
                self.build_memory_action(
                    operation="supersede",
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    memory_unit=superseded_unit,
                    related_memory_unit_ids=[new_unit["memory_unit_id"]],
                    before_snapshot=primary_match,
                    after_snapshot=superseded_unit,
                    reason=normalized_candidate["reason"],
                    event_ids=event_ids,
                ),
                self.build_memory_action(
                    operation="create",
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    memory_unit=new_unit,
                    related_memory_unit_ids=[primary_match["memory_unit_id"]],
                    before_snapshot=None,
                    after_snapshot=new_unit,
                    reason=normalized_candidate["reason"],
                    event_ids=event_ids,
                ),
            ]

        # Block: Fallback
        return []

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
            "last_confirmed_at": finished_at if self._candidate_confirms_memory(candidate) else None,
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
        if existing["status"] == "dormant":
            next_status = "confirmed" if self._candidate_confirms_memory(candidate) else candidate["status"]
        elif existing["status"] == "inferred" and self._candidate_confirms_memory(candidate):
            next_status = "confirmed"

        # Block: ConfirmedAt
        last_confirmed_at = existing.get("last_confirmed_at")
        if self._candidate_confirms_memory(candidate):
            last_confirmed_at = finished_at

        # Block: Record
        return {
            **existing,
            "summary_text": existing["summary_text"],
            "status": next_status,
            "commitment_state": candidate.get("commitment_state") or existing.get("commitment_state"),
            "confidence": max(clamp_score(existing["confidence"]), clamp_score(candidate["confidence"])),
            "salience": max(clamp_score(existing["salience"]), clamp_score(candidate["salience"])),
            "last_confirmed_at": last_confirmed_at,
            "valid_from": candidate.get("valid_from") or existing.get("valid_from"),
            "valid_to": candidate.get("valid_to") or existing.get("valid_to"),
            "evidence_event_ids": merged_event_ids(existing.get("evidence_event_ids", []), event_ids),
            "evidence_cycle_ids": merged_cycle_ids(existing.get("evidence_cycle_ids", []), cycle_ids),
            "qualifiers": self._merged_qualifiers(existing, candidate),
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
        if self._candidate_confirms_memory(candidate):
            next_status = "confirmed"
        elif existing["status"] == "dormant":
            next_status = "inferred"

        # Block: ConfirmedAt
        last_confirmed_at = existing.get("last_confirmed_at")
        if self._candidate_confirms_memory(candidate):
            last_confirmed_at = finished_at

        # Block: Record
        return {
            **existing,
            "object_ref_or_value": candidate.get("object_ref_or_value"),
            "summary_text": candidate["summary_text"].strip(),
            "status": next_status,
            "commitment_state": candidate.get("commitment_state") or existing.get("commitment_state"),
            "confidence": max(clamp_score(existing["confidence"]), clamp_score(candidate["confidence"])),
            "salience": max(clamp_score(existing["salience"]), clamp_score(candidate["salience"])),
            "last_confirmed_at": last_confirmed_at,
            "valid_from": candidate.get("valid_from") or existing.get("valid_from"),
            "valid_to": candidate.get("valid_to") or existing.get("valid_to"),
            "evidence_event_ids": merged_event_ids(existing.get("evidence_event_ids", []), event_ids),
            "evidence_cycle_ids": merged_cycle_ids(existing.get("evidence_cycle_ids", []), cycle_ids),
            "qualifiers": self._merged_qualifiers(existing, candidate),
        }

    def build_superseded_memory_unit(
        self,
        *,
        existing: dict[str, Any],
        finished_at: str,
        event_ids: list[str],
        cycle_ids: list[str],
    ) -> dict[str, Any]:
        # Block: Record
        return {
            **existing,
            "status": "superseded",
            "salience": min(clamp_score(existing["salience"]), 0.2),
            "valid_to": finished_at,
            "evidence_event_ids": merged_event_ids(existing.get("evidence_event_ids", []), event_ids),
            "evidence_cycle_ids": merged_cycle_ids(existing.get("evidence_cycle_ids", []), cycle_ids),
        }

    def build_revoked_memory_unit(
        self,
        *,
        existing: dict[str, Any],
        finished_at: str,
        event_ids: list[str],
        cycle_ids: list[str],
    ) -> dict[str, Any]:
        # Block: Record
        return {
            **existing,
            "status": "revoked",
            "confidence": min(clamp_score(existing["confidence"]), 0.2),
            "salience": min(clamp_score(existing["salience"]), 0.1),
            "valid_to": finished_at,
            "evidence_event_ids": merged_event_ids(existing.get("evidence_event_ids", []), event_ids),
            "evidence_cycle_ids": merged_cycle_ids(existing.get("evidence_cycle_ids", []), cycle_ids),
        }

    def build_dormant_memory_unit(
        self,
        *,
        existing: dict[str, Any],
        event_ids: list[str],
        cycle_ids: list[str],
    ) -> dict[str, Any]:
        # Block: Record
        return {
            **existing,
            "status": "dormant",
            "salience": min(clamp_score(existing["salience"]), 0.15),
            "evidence_event_ids": merged_event_ids(existing.get("evidence_event_ids", []), event_ids),
            "evidence_cycle_ids": merged_cycle_ids(existing.get("evidence_cycle_ids", []), cycle_ids),
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
        return self._semantic_qualifiers(existing.get("qualifiers", {})) == self._semantic_qualifiers(
            candidate.get("qualifiers", {})
        )

    def can_refine(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # Block: PolarityGuard
        if self._polarity_conflicts(existing, candidate):
            return False

        # Block: ObjectGuard
        if existing.get("object_ref_or_value") != candidate.get("object_ref_or_value"):
            return False

        # Block: ContentCheck
        return (
            existing.get("summary_text") != candidate["summary_text"].strip()
            or self._semantic_qualifiers(existing.get("qualifiers", {}))
            != self._semantic_qualifiers(candidate.get("qualifiers", {}))
            or existing.get("commitment_state") != candidate.get("commitment_state")
            or existing.get("valid_from") != candidate.get("valid_from")
            or existing.get("valid_to") != candidate.get("valid_to")
        )

    def _normalized_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        # Block: Record
        return {
            **candidate,
            "summary_text": candidate["summary_text"].strip(),
            "reason": candidate["reason"].strip(),
            "confidence": clamp_score(candidate["confidence"]),
            "salience": clamp_score(candidate["salience"]),
            "qualifiers": dict(candidate.get("qualifiers", {})),
        }

    def _ordered_matches(self, matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Block: Result
        return sorted(
            matches,
            key=lambda match: (
                self._match_status_rank(match.get("status")),
                clamp_score(match.get("confidence")),
                clamp_score(match.get("salience")),
                timestamp_sort_key(match.get("last_confirmed_at") or match.get("formed_at")),
            ),
            reverse=True,
        )

    def _match_status_rank(self, status: Any) -> int:
        # Block: Mapping
        if status == "confirmed":
            return 3
        if status == "inferred":
            return 2
        if status == "dormant":
            return 1
        return 0

    def _same_memory_match(self, matches: list[dict[str, Any]], candidate: dict[str, Any]) -> dict[str, Any] | None:
        # Block: Scan
        for match in matches:
            if match.get("status") not in REVIVABLE_MEMORY_STATUSES:
                continue
            if self.is_same_memory(match, candidate):
                return match

        # Block: Result
        return None

    def _primary_match(self, matches: list[dict[str, Any]], candidate: dict[str, Any]) -> dict[str, Any] | None:
        # Block: SameObject
        same_object_matches = [
            match
            for match in matches
            if match.get("status") in REVIVABLE_MEMORY_STATUSES
            and self._same_object(match, candidate)
        ]
        if same_object_matches:
            return same_object_matches[0]

        # Block: Active
        active_matches = [
            match
            for match in matches
            if match.get("status") in ACTIVE_MEMORY_STATUSES
        ]
        if active_matches:
            return active_matches[0]

        # Block: Dormant
        dormant_matches = [
            match
            for match in matches
            if match.get("status") == "dormant"
        ]
        if dormant_matches:
            return dormant_matches[0]

        # Block: Result
        return None

    def _resolve_revoke_request(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        event_ids: list[str],
        cycle_ids: list[str],
        candidate: dict[str, Any],
        matches: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # Block: Targets
        targets = self._revocation_targets(matches, candidate)
        if not targets:
            return []

        # Block: Actions
        actions: list[dict[str, Any]] = []
        for target in targets:
            revoked_unit = self.build_revoked_memory_unit(
                existing=target,
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
            )
            actions.append(
                self.build_memory_action(
                    operation="revoke",
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    memory_unit=revoked_unit,
                    related_memory_unit_ids=[],
                    before_snapshot=target,
                    after_snapshot=revoked_unit,
                    reason=candidate["reason"],
                    event_ids=event_ids,
                )
            )

        # Block: Result
        return actions

    def _resolve_dormant_request(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        event_ids: list[str],
        cycle_ids: list[str],
        candidate: dict[str, Any],
        matches: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # Block: Target
        target = self._primary_match(matches, candidate)
        if target is None or target.get("status") == "dormant":
            return []

        # Block: Action
        dormant_unit = self.build_dormant_memory_unit(
            existing=target,
            event_ids=event_ids,
            cycle_ids=cycle_ids,
        )
        return [
            self.build_memory_action(
                operation="dormant",
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                memory_unit=dormant_unit,
                related_memory_unit_ids=[],
                before_snapshot=target,
                after_snapshot=dormant_unit,
                reason=candidate["reason"],
                event_ids=event_ids,
            )
        ]

    def _build_revoke_and_create_actions(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        event_ids: list[str],
        cycle_ids: list[str],
        candidate: dict[str, Any],
        matches: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # Block: Targets
        targets = self._revocation_targets(matches, candidate)
        if not targets:
            return []

        # Block: NewUnit
        new_unit = self.build_new_memory_unit(
            memory_set_id=memory_set_id,
            finished_at=finished_at,
            event_ids=event_ids,
            cycle_ids=cycle_ids,
            candidate=candidate,
        )
        target_ids = [target["memory_unit_id"] for target in targets]

        # Block: Actions
        actions: list[dict[str, Any]] = []
        for target in targets:
            revoked_unit = self.build_revoked_memory_unit(
                existing=target,
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
            )
            actions.append(
                self.build_memory_action(
                    operation="revoke",
                    memory_set_id=memory_set_id,
                    finished_at=finished_at,
                    memory_unit=revoked_unit,
                    related_memory_unit_ids=[new_unit["memory_unit_id"]],
                    before_snapshot=target,
                    after_snapshot=revoked_unit,
                    reason=candidate["reason"],
                    event_ids=event_ids,
                )
            )
        actions.append(
            self.build_memory_action(
                operation="create",
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                memory_unit=new_unit,
                related_memory_unit_ids=target_ids,
                before_snapshot=None,
                after_snapshot=new_unit,
                reason=candidate["reason"],
                event_ids=event_ids,
            )
        )

        # Block: Result
        return actions

    def _build_create_action(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        event_ids: list[str],
        cycle_ids: list[str],
        candidate: dict[str, Any],
        related_memory_unit_ids: list[str],
    ) -> dict[str, Any]:
        # Block: Unit
        new_unit = self.build_new_memory_unit(
            memory_set_id=memory_set_id,
            finished_at=finished_at,
            event_ids=event_ids,
            cycle_ids=cycle_ids,
            candidate=candidate,
        )

        # Block: Result
        return self.build_memory_action(
            operation="create",
            memory_set_id=memory_set_id,
            finished_at=finished_at,
            memory_unit=new_unit,
            related_memory_unit_ids=related_memory_unit_ids,
            before_snapshot=None,
            after_snapshot=new_unit,
            reason=candidate["reason"],
            event_ids=event_ids,
        )

    def _should_noop_candidate(self, candidate: dict[str, Any]) -> bool:
        # Block: UnsupportedStatuses
        if candidate["status"] == "superseded":
            return True

        # Block: SummaryGuard
        if candidate["memory_type"] == "summary":
            return True

        # Block: StatusBypass
        if candidate["status"] in {"revoked", "dormant"}:
            return False

        # Block: WeakGuard
        if candidate["confidence"] < NO_WRITE_CONFIDENCE_FLOOR:
            return True
        if candidate["salience"] < NO_WRITE_SALIENCE_FLOOR:
            return True

        # Block: TypeSpecificGuard
        if candidate["memory_type"] == "interpretation":
            return not self._candidate_is_explicit(candidate) and candidate["confidence"] < INTERPRETATION_CONFIDENCE_FLOOR
        if candidate["memory_type"] == "relation":
            return not self._candidate_is_explicit(candidate) and candidate["confidence"] < RELATION_CONFIDENCE_FLOOR

        # Block: Result
        return False

    def _should_revoke_with_replacement(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # Block: SameObjectGuard
        if not self._same_object(existing, candidate):
            return False

        # Block: ExplicitGuard
        if not self._candidate_is_explicit(candidate):
            return False

        # Block: Signal
        if candidate.get("qualifiers", {}).get("negates_previous") is True:
            return True
        return self._polarity_conflicts(existing, candidate)

    def _should_create_parallel(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # Block: ReplaceGuard
        qualifiers = candidate.get("qualifiers", {})
        if qualifiers.get("replace_prior") is True or qualifiers.get("negates_previous") is True:
            return False

        # Block: ExplicitParallel
        if qualifiers.get("allow_parallel") is True:
            return True

        # Block: TypeGuard
        if candidate["memory_type"] not in PARALLEL_MEMORY_TYPES:
            return False

        # Block: ObjectGuard
        return not self._same_object(existing, candidate)

    def _should_supersede(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # Block: ReplaceHint
        qualifiers = candidate.get("qualifiers", {})
        if qualifiers.get("replace_prior") is True:
            return True
        if qualifiers.get("negates_previous") is True and not self._same_object(existing, candidate):
            return True

        # Block: FactUpdate
        if candidate["memory_type"] == "fact":
            if self._same_object(existing, candidate):
                return False
            if candidate.get("valid_from") is not None or candidate.get("valid_to") is not None:
                return True
            return self._candidate_is_explicit(candidate)

        # Block: Default
        return candidate["memory_type"] not in PARALLEL_MEMORY_TYPES

    def _revocation_targets(self, matches: list[dict[str, Any]], candidate: dict[str, Any]) -> list[dict[str, Any]]:
        # Block: SameObjectTargets
        same_object_targets = [
            match
            for match in matches
            if match.get("status") in REVIVABLE_MEMORY_STATUSES
            and self._same_object(match, candidate)
        ]
        if same_object_targets:
            return same_object_targets

        # Block: BroadTarget
        if candidate["status"] == "revoked" and candidate.get("object_ref_or_value") is None:
            primary_match = self._primary_match(matches, candidate)
            if primary_match is not None:
                return [primary_match]

        # Block: Result
        return []

    def _candidate_confirms_memory(self, candidate: dict[str, Any]) -> bool:
        # Block: Status
        if candidate["status"] == "confirmed":
            return True

        # Block: Explicit
        return self._candidate_is_explicit(candidate) and candidate["memory_type"] != "interpretation"

    def _candidate_is_explicit(self, candidate: dict[str, Any]) -> bool:
        # Block: Source
        source = candidate.get("qualifiers", {}).get("source")
        if source in DIRECT_SOURCE_VALUES:
            return True

        # Block: Fallback
        return candidate["status"] == "confirmed" and candidate["memory_type"] in {"fact", "preference", "commitment"}

    def _same_object(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # Block: Compare
        return existing.get("object_ref_or_value") == candidate.get("object_ref_or_value")

    def _polarity_conflicts(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # Block: Values
        existing_polarity = existing.get("qualifiers", {}).get("polarity")
        candidate_polarity = candidate.get("qualifiers", {}).get("polarity")
        if existing_polarity is None or candidate_polarity is None:
            return False

        # Block: Result
        return existing_polarity != candidate_polarity

    def _semantic_qualifiers(self, qualifiers: dict[str, Any]) -> dict[str, Any]:
        # Block: Filter
        return {
            key: value
            for key, value in qualifiers.items()
            if key not in CONTROL_QUALIFIER_KEYS
        }

    def _merged_qualifiers(self, existing: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        # Block: Merge
        return {
            **existing.get("qualifiers", {}),
            **candidate.get("qualifiers", {}),
        }
