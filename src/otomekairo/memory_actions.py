from __future__ import annotations

import math
import uuid
from typing import TYPE_CHECKING, Any

from otomekairo.memory_utils import (
    NON_SEMANTIC_QUALIFIER_KEYS,
    build_memory_unit_semantic_text,
    clamp_score,
    merged_cycle_ids,
    merged_event_ids,
    optional_text,
    semantic_qualifiers,
    timestamp_sort_key,
)
from otomekairo.store import FileStore

if TYPE_CHECKING:
    from otomekairo.llm import LLMClient


# 定数
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
SEMANTIC_EXCLUDED_QUALIFIER_KEYS = NON_SEMANTIC_QUALIFIER_KEYS
PARALLEL_MEMORY_TYPES = {
    "commitment",
    "interpretation",
    "preference",
    "relation",
}
SEMANTIC_REFINE_THRESHOLD = 0.9
SEMANTIC_REFINE_MEMORY_TYPES = {"interpretation"}


# 解決器
class MemoryActionResolver:
    def __init__(self, *, store: FileStore, llm: "LLMClient | None" = None) -> None:
        # 依存関係
        self.store = store
        self.llm = llm

    def resolve_memory_actions(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        event_ids: list[str],
        cycle_ids: list[str],
        candidate: dict[str, Any],
        embedding_definition: dict[str, Any] | None = None,
        allow_summary: bool = False,
    ) -> list[dict[str, Any]]:
        # 候補
        normalized_candidate = self._normalized_candidate(candidate)
        if self._should_noop_candidate(normalized_candidate, allow_summary=allow_summary):
            return []

        # 検索
        matches = self._annotate_semantic_matches(
            matches=self.store.find_memory_units_for_compare(
                memory_set_id=memory_set_id,
                memory_type=normalized_candidate["memory_type"],
                scope_type=normalized_candidate["scope_type"],
                scope_key=normalized_candidate["scope_key"],
                subject_ref=normalized_candidate["subject_ref"],
                predicate=normalized_candidate["predicate"],
            ),
            candidate=normalized_candidate,
            embedding_definition=embedding_definition,
        )
        matches = self._ordered_matches(matches)

        # 特別なstatus
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

        # 一致選択
        same_memory_match = self._same_memory_match(matches, normalized_candidate)
        primary_match = self._primary_match(matches, normalized_candidate)
        semantic_refine_match: dict[str, Any] | None = None
        if primary_match is None or not self._same_object(primary_match, normalized_candidate):
            semantic_refine_match = self._semantic_refine_match(matches, normalized_candidate)

        # create経路
        if same_memory_match is None and primary_match is None and semantic_refine_match is None:
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

        # reinforce経路
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

        # refine経路
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

        # 置換付きrevoke
        if primary_match is not None and self._should_revoke_with_replacement(primary_match, normalized_candidate):
            return self._build_revoke_and_create_actions(
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
                candidate=normalized_candidate,
                matches=matches,
            )

        # parallel経路
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

        # supersede経路
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

        # semantic refine経路
        if semantic_refine_match is not None:
            updated_unit = self.build_refined_memory_unit(
                existing=semantic_refine_match,
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
                    before_snapshot=semantic_refine_match,
                    after_snapshot=updated_unit,
                    reason=normalized_candidate["reason"],
                    event_ids=event_ids,
                )
            ]

        # 代替
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
        # 記録
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
        # status決定
        next_status = existing["status"]
        if existing["status"] == "dormant":
            next_status = "confirmed" if self._candidate_confirms_memory(candidate) else candidate["status"]
        elif existing["status"] == "inferred" and self._candidate_confirms_memory(candidate):
            next_status = "confirmed"

        # 確認済みAt
        last_confirmed_at = existing.get("last_confirmed_at")
        if self._candidate_confirms_memory(candidate):
            last_confirmed_at = finished_at

        # 記録
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
        # status決定
        next_status = existing["status"]
        if self._candidate_confirms_memory(candidate):
            next_status = "confirmed"
        elif existing["status"] == "dormant":
            next_status = "inferred"

        # 確認済みAt
        last_confirmed_at = existing.get("last_confirmed_at")
        if self._candidate_confirms_memory(candidate):
            last_confirmed_at = finished_at

        # 記録
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
        # 記録
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
        # 記録
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
        # 記録
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
        # アクション
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
        # object比較
        if existing.get("object_ref_or_value") != candidate.get("object_ref_or_value"):
            return False

        # commitment比較
        if existing.get("commitment_state") != candidate.get("commitment_state"):
            return False

        # qualifier比較
        return self._semantic_qualifiers(existing.get("qualifiers", {})) == self._semantic_qualifiers(
            candidate.get("qualifiers", {})
        )

    def can_refine(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # 極性確認
        if self._polarity_conflicts(existing, candidate):
            return False

        # exact object が一致しない更新は semantic refine 側でのみ扱う。
        if not self._same_object(existing, candidate):
            return False

        # 内容確認
        return (
            existing.get("summary_text") != candidate["summary_text"].strip()
            or self._semantic_qualifiers(existing.get("qualifiers", {}))
            != self._semantic_qualifiers(candidate.get("qualifiers", {}))
            or existing.get("commitment_state") != candidate.get("commitment_state")
            or existing.get("valid_from") != candidate.get("valid_from")
            or existing.get("valid_to") != candidate.get("valid_to")
        )

    def _normalized_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        # 記録
        return {
            **candidate,
            "summary_text": candidate["summary_text"].strip(),
            "reason": candidate["reason"].strip(),
            "confidence": clamp_score(candidate["confidence"]),
            "salience": clamp_score(candidate["salience"]),
            "qualifiers": dict(candidate.get("qualifiers", {})),
        }

    def _ordered_matches(self, matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # 結果
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
        # マッピング
        if status == "confirmed":
            return 3
        if status == "inferred":
            return 2
        if status == "dormant":
            return 1
        return 0

    def _same_memory_match(self, matches: list[dict[str, Any]], candidate: dict[str, Any]) -> dict[str, Any] | None:
        # 走査
        for match in matches:
            if match.get("status") not in REVIVABLE_MEMORY_STATUSES:
                continue
            if self.is_same_memory(match, candidate):
                return match

        # 結果
        return None

    def _primary_match(self, matches: list[dict[str, Any]], candidate: dict[str, Any]) -> dict[str, Any] | None:
        # 同一object
        same_object_matches = [
            match
            for match in matches
            if match.get("status") in REVIVABLE_MEMORY_STATUSES
            and self._same_object(match, candidate)
        ]
        if same_object_matches:
            return same_object_matches[0]

        # 有効
        active_matches = [
            match
            for match in matches
            if match.get("status") in ACTIVE_MEMORY_STATUSES
        ]
        if active_matches:
            return active_matches[0]

        # 休眠
        dormant_matches = [
            match
            for match in matches
            if match.get("status") == "dormant"
        ]
        if dormant_matches:
            return dormant_matches[0]

        # 結果
        return None

    def _semantic_refine_match(self, matches: list[dict[str, Any]], candidate: dict[str, Any]) -> dict[str, Any] | None:
        semantic_matches = [
            match
            for match in matches
            if match.get("status") in REVIVABLE_MEMORY_STATUSES
            and not self._same_object(match, candidate)
        ]
        semantic_matches.sort(
            key=lambda match: (
                self._semantic_similarity(match),
                self._match_status_rank(match.get("status")),
                clamp_score(match.get("confidence")),
                clamp_score(match.get("salience")),
                timestamp_sort_key(match.get("last_confirmed_at") or match.get("formed_at")),
            ),
            reverse=True,
        )
        for match in semantic_matches:
            if self._can_refine_semantically(match, candidate):
                return match
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
        # 対象群
        targets = self._revocation_targets(matches, candidate)
        if not targets:
            return []

        # アクション群
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

        # 結果
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
        # 対象
        target = self._primary_match(matches, candidate)
        if target is None or target.get("status") == "dormant":
            return []

        # アクション
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
        # 対象群
        targets = self._revocation_targets(matches, candidate)
        if not targets:
            return []

        # New単位
        new_unit = self.build_new_memory_unit(
            memory_set_id=memory_set_id,
            finished_at=finished_at,
            event_ids=event_ids,
            cycle_ids=cycle_ids,
            candidate=candidate,
        )
        target_ids = [target["memory_unit_id"] for target in targets]

        # アクション群
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

        # 結果
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
        # 単位
        new_unit = self.build_new_memory_unit(
            memory_set_id=memory_set_id,
            finished_at=finished_at,
            event_ids=event_ids,
            cycle_ids=cycle_ids,
            candidate=candidate,
        )

        # 結果
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

    def _should_noop_candidate(self, candidate: dict[str, Any], *, allow_summary: bool) -> bool:
        # 非対応status群
        if candidate["status"] == "superseded":
            return True

        # 要約確認
        if candidate["memory_type"] == "summary":
            if allow_summary:
                return False
            return True

        # statusバイパス
        if candidate["status"] in {"revoked", "dormant"}:
            return False

        # 弱い確認
        if candidate["confidence"] < NO_WRITE_CONFIDENCE_FLOOR:
            return True
        if candidate["salience"] < NO_WRITE_SALIENCE_FLOOR:
            return True

        # 型別確認
        if candidate["memory_type"] == "interpretation":
            return not self._candidate_is_explicit(candidate) and candidate["confidence"] < INTERPRETATION_CONFIDENCE_FLOOR
        if candidate["memory_type"] == "relation":
            return not self._candidate_is_explicit(candidate) and candidate["confidence"] < RELATION_CONFIDENCE_FLOOR

        # 結果
        return False

    def _should_revoke_with_replacement(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # 同一object確認
        if not self._same_object(existing, candidate):
            return False

        # 明示確認
        if not self._candidate_is_explicit(candidate):
            return False

        # シグナル
        if candidate.get("qualifiers", {}).get("negates_previous") is True:
            return True
        return self._polarity_conflicts(existing, candidate)

    def _should_create_parallel(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # 置換確認
        qualifiers = candidate.get("qualifiers", {})
        if qualifiers.get("replace_prior") is True or qualifiers.get("negates_previous") is True:
            return False

        # 明示parallel
        if qualifiers.get("allow_parallel") is True:
            return True

        # 型確認
        if candidate["memory_type"] not in PARALLEL_MEMORY_TYPES:
            return False

        # object確認
        return not self._same_object(existing, candidate)

    def _should_supersede(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # 置換ヒント
        qualifiers = candidate.get("qualifiers", {})
        if qualifiers.get("replace_prior") is True:
            return True
        if qualifiers.get("negates_previous") is True and not self._same_object(existing, candidate):
            return True

        # 事実更新
        if candidate["memory_type"] == "fact":
            if self._same_object(existing, candidate):
                return False
            if candidate.get("valid_from") is not None or candidate.get("valid_to") is not None:
                return True
            return self._candidate_is_explicit(candidate)

        # 既定
        return candidate["memory_type"] not in PARALLEL_MEMORY_TYPES

    def _revocation_targets(self, matches: list[dict[str, Any]], candidate: dict[str, Any]) -> list[dict[str, Any]]:
        # 同一object対象群
        same_object_targets = [
            match
            for match in matches
            if match.get("status") in REVIVABLE_MEMORY_STATUSES
            and self._same_object(match, candidate)
        ]
        if same_object_targets:
            return same_object_targets

        # 広域対象
        if candidate["status"] == "revoked" and candidate.get("object_ref_or_value") is None:
            primary_match = self._primary_match(matches, candidate)
            if primary_match is not None:
                return [primary_match]

        # 結果
        return []

    def _candidate_confirms_memory(self, candidate: dict[str, Any]) -> bool:
        # status判定
        if candidate["status"] == "confirmed":
            return True

        # 明示
        return self._candidate_is_explicit(candidate) and candidate["memory_type"] != "interpretation"

    def _candidate_is_explicit(self, candidate: dict[str, Any]) -> bool:
        # source判定
        source = candidate.get("qualifiers", {}).get("source")
        if source in DIRECT_SOURCE_VALUES:
            return True

        # 代替
        return candidate["status"] == "confirmed" and candidate["memory_type"] in {"fact", "preference", "commitment"}

    def _same_object(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # 比較
        return existing.get("object_ref_or_value") == candidate.get("object_ref_or_value")

    def _can_refine_semantically(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # 適用範囲
        if candidate["memory_type"] not in SEMANTIC_REFINE_MEMORY_TYPES:
            return False
        if self._candidate_is_explicit(candidate) or self._memory_unit_is_explicit(existing):
            return False
        if self._has_structural_update_signal(candidate):
            return False

        existing_object = optional_text(existing.get("object_ref_or_value"))
        candidate_object = optional_text(candidate.get("object_ref_or_value"))
        if existing_object is None and candidate_object is None:
            return False
        if existing_object is not None and candidate_object is not None:
            return False

        # 意味補助は qualifiers と進行状態が同じ場合だけ使う。
        if existing.get("commitment_state") != candidate.get("commitment_state"):
            return False
        if self._semantic_qualifiers(existing.get("qualifiers", {})) != self._semantic_qualifiers(
            candidate.get("qualifiers", {})
        ):
            return False

        # 類似度
        return self._semantic_similarity(existing) >= SEMANTIC_REFINE_THRESHOLD

    def _polarity_conflicts(self, existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # 値群
        existing_polarity = existing.get("qualifiers", {}).get("polarity")
        candidate_polarity = candidate.get("qualifiers", {}).get("polarity")
        if existing_polarity is None or candidate_polarity is None:
            return False

        # 結果
        return existing_polarity != candidate_polarity

    def _semantic_qualifiers(self, qualifiers: dict[str, Any]) -> dict[str, Any]:
        # 絞り込み
        return semantic_qualifiers(
            qualifiers,
            exclude_keys=SEMANTIC_EXCLUDED_QUALIFIER_KEYS,
        )

    def _memory_unit_is_explicit(self, memory_unit: dict[str, Any]) -> bool:
        source = memory_unit.get("qualifiers", {}).get("source")
        return source in DIRECT_SOURCE_VALUES

    def _has_structural_update_signal(self, candidate: dict[str, Any]) -> bool:
        qualifiers = candidate.get("qualifiers", {})
        return any(
            qualifiers.get(key) is True
            for key in ("allow_parallel", "negates_previous", "replace_prior")
        )

    def _merged_qualifiers(self, existing: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        # 統合
        return {
            **existing.get("qualifiers", {}),
            **candidate.get("qualifiers", {}),
        }

    def _annotate_semantic_matches(
        self,
        *,
        matches: list[dict[str, Any]],
        candidate: dict[str, Any],
        embedding_definition: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        # 対象外
        if not matches or not self._should_compare_semantically(candidate, embedding_definition):
            return matches

        # compare text 群
        candidate_text = build_memory_unit_semantic_text(
            candidate,
            exclude_qualifier_keys=SEMANTIC_EXCLUDED_QUALIFIER_KEYS,
        )
        if not candidate_text:
            return matches
        match_texts = [
            build_memory_unit_semantic_text(
                match,
                exclude_qualifier_keys=SEMANTIC_EXCLUDED_QUALIFIER_KEYS,
            )
            for match in matches
        ]

        # 埋め込み比較
        embeddings = self.llm.generate_embeddings(
            role_definition=embedding_definition,
            texts=[candidate_text, *match_texts],
        )
        candidate_embedding = embeddings[0]

        # 注釈付与
        annotated_matches: list[dict[str, Any]] = []
        for match, match_embedding in zip(matches, embeddings[1:], strict=True):
            annotated_matches.append(
                {
                    **match,
                    "_semantic_similarity": self._cosine_similarity(candidate_embedding, match_embedding),
                }
            )
        return annotated_matches

    def _should_compare_semantically(
        self,
        candidate: dict[str, Any],
        embedding_definition: dict[str, Any] | None,
    ) -> bool:
        # 依存関係
        if self.llm is None or not isinstance(embedding_definition, dict):
            return False

        # 特殊状態と型
        if candidate["status"] in {"revoked", "dormant"}:
            return False
        return candidate["memory_type"] in SEMANTIC_REFINE_MEMORY_TYPES

    def _semantic_similarity(self, record: dict[str, Any]) -> float:
        value = record.get("_semantic_similarity")
        if not isinstance(value, (int, float)):
            return 0.0
        return float(value)

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        # 正規化済みでなくても使えるよう cosine を明示計算する。
        if not left or not right or len(left) != len(right):
            return 0.0
        dot = sum(left_value * right_value for left_value, right_value in zip(left, right, strict=True))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm <= 0.0 or right_norm <= 0.0:
            return 0.0
        return dot / (left_norm * right_norm)
