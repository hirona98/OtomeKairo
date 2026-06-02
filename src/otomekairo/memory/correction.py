from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.memory.utils import action_counts

if TYPE_CHECKING:
    from otomekairo.llm.client import LLMClient
    from otomekairo.store.file_store import FileStore


# 訂正対象候補は直近だけを見る。
CORRECTION_CYCLE_LIMIT = 6
CORRECTION_TARGET_LIMIT = 12


class MemoryCorrectionReconciler:
    def __init__(self, *, store: "FileStore", action_resolver: MemoryActionResolver) -> None:
        # 依存関係
        self.store = store
        self.action_resolver = action_resolver

    def prepare(
        self,
        *,
        memory_set_id: str,
        cycle_id: str,
        finished_at: str,
    ) -> dict[str, Any]:
        # 直近候補
        targets = self.store.list_recent_memory_revision_targets_for_correction(
            memory_set_id=memory_set_id,
            before_finished_at=finished_at,
            exclude_cycle_id=cycle_id,
            cycle_limit=CORRECTION_CYCLE_LIMIT,
            limit=CORRECTION_TARGET_LIMIT,
        )
        return {
            "targets": targets,
            "trace": self.queued_trace(targets=targets),
        }

    def queued_trace(self, *, targets: list[dict[str, Any]]) -> dict[str, Any]:
        # 同期側では候補化だけ行い、判断は後段 worker に渡す。
        result_status = "queued" if targets else "skipped"
        selection_status = "queued" if targets else "not_requested"
        return {
            "result_status": result_status,
            "selection_status": selection_status,
            "target_candidate_count": len(targets),
            "selected_target_count": 0,
            "selected_revision_ids": [],
            "correction_group_ids": [],
            "action_count": 0,
            "operation_counts": {},
            "actions": [],
            "failure_reason": None,
        }

    def skipped_trace(self, *, reason: str) -> dict[str, Any]:
        # skipped trace
        return {
            "result_status": "skipped",
            "selection_status": "not_requested",
            "target_candidate_count": 0,
            "selected_target_count": 0,
            "selected_revision_ids": [],
            "correction_group_ids": [],
            "action_count": 0,
            "operation_counts": {},
            "actions": [],
            "failure_reason": reason,
        }

    def run(
        self,
        *,
        llm: "LLMClient",
        role_definition: dict[str, Any],
        context: dict[str, Any] | None,
        finished_at: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        # 入力なし
        if not isinstance(context, dict):
            return [], self.skipped_trace(reason="no_context")

        targets = context.get("targets")
        if not isinstance(targets, list) or not targets:
            return [], self.skipped_trace(reason="no_targets")

        # LLM選定
        source_pack = {
            "input_text": context.get("input_text"),
            "speech_text": context.get("speech_text"),
            "decision_summary": context.get("decision_summary"),
            "target_candidates": [
                self._compact_target(target)
                for target in targets
                if isinstance(target, dict)
            ],
        }
        selection = llm.generate_memory_correction_reconciliation(
            role_definition=role_definition,
            source_pack=source_pack,
        )

        # アクション作成
        event_ids = [
            value
            for value in context.get("event_ids", [])
            if isinstance(value, str) and value
        ]
        cycle_ids = [
            value
            for value in context.get("cycle_ids", [])
            if isinstance(value, str) and value
        ]
        target_by_revision_id = {
            target.get("revision", {}).get("revision_id"): target
            for target in targets
            if isinstance(target, dict)
        }
        actions: list[dict[str, Any]] = []
        selected_targets: list[dict[str, Any]] = []
        handled_revision_ids: set[str] = set()
        for item in selection.get("selected_targets", []):
            if not isinstance(item, dict):
                continue
            revision_id = item.get("revision_id")
            if not isinstance(revision_id, str) or revision_id in handled_revision_ids:
                continue
            target = target_by_revision_id.get(revision_id)
            if target is None:
                continue
            target_actions = self._build_actions_for_target(
                target=target,
                selected_memory_unit_id=item.get("memory_unit_id"),
                correction_kind=item.get("correction_kind"),
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
                reason=str(item.get("reason_summary") or "").strip(),
            )
            if not target_actions:
                continue
            actions.extend(target_actions)
            selected_targets.append(target)
            handled_revision_ids.add(revision_id)

        return actions, self._trace(
            selection=selection,
            targets=targets,
            selected_targets=selected_targets,
            actions=actions,
        )

    def _build_actions_for_target(
        self,
        *,
        target: dict[str, Any],
        selected_memory_unit_id: Any,
        correction_kind: Any,
        finished_at: str,
        event_ids: list[str],
        cycle_ids: list[str],
        reason: str,
    ) -> list[dict[str, Any]]:
        # 対象
        operation = target.get("operation")
        unit = target.get("memory_unit", {})
        revision = target.get("revision", {})
        if not isinstance(unit, dict) or not isinstance(revision, dict):
            return []
        if selected_memory_unit_id != unit.get("memory_unit_id"):
            return []

        # 種別
        normalized_kind = str(correction_kind or "").strip()
        if operation == "create":
            if normalized_kind != "revoke_created":
                return []
            return self._build_revoke_created_actions(
                target=target,
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
                reason=reason,
            )

        if operation in {"reinforce", "refine", "revoke", "dormant"}:
            if normalized_kind != "restore_previous":
                return []
            action = self._build_restore_previous_action(
                target=target,
                correction_kind=normalized_kind,
                finished_at=finished_at,
                event_ids=event_ids,
                reason=reason,
            )
            return [action] if action is not None else []

        if operation == "supersede":
            if normalized_kind != "supersede_compensation":
                return []
            return self._build_supersede_compensation_actions(
                target=target,
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
                reason=reason,
            )

        return []

    def _build_revoke_created_actions(
        self,
        *,
        target: dict[str, Any],
        finished_at: str,
        event_ids: list[str],
        cycle_ids: list[str],
        reason: str,
    ) -> list[dict[str, Any]]:
        # 新規誤記憶を補正 revision で無効化する。
        unit = target["memory_unit"]
        if unit.get("status") in {"revoked", "superseded"}:
            return []
        revoked_unit = self.action_resolver.build_revoked_memory_unit(
            existing=unit,
            finished_at=finished_at,
            event_ids=event_ids,
            cycle_ids=cycle_ids,
        )
        return [
            self._build_correct_action(
                target=target,
                memory_unit=revoked_unit,
                related_memory_unit_ids=[],
                before_snapshot=unit,
                after_snapshot=revoked_unit,
                correction_kind="revoke_created",
                reason=reason,
                finished_at=finished_at,
                event_ids=event_ids,
            )
        ]

    def _build_restore_previous_action(
        self,
        *,
        target: dict[str, Any],
        correction_kind: str,
        finished_at: str,
        event_ids: list[str],
        reason: str,
    ) -> dict[str, Any] | None:
        unit = target["memory_unit"]
        revision = target["revision"]
        before_snapshot = revision.get("before_snapshot")
        if not isinstance(before_snapshot, dict):
            return None
        if before_snapshot.get("memory_unit_id") != unit.get("memory_unit_id"):
            return None
        corrected_unit = self.action_resolver.build_corrected_memory_unit(
            corrected_snapshot=before_snapshot,
        )
        return self._build_correct_action(
            target=target,
            memory_unit=corrected_unit,
            related_memory_unit_ids=[],
            before_snapshot=unit,
            after_snapshot=corrected_unit,
            correction_kind=correction_kind,
            reason=reason,
            finished_at=finished_at,
            event_ids=event_ids,
        )

    def _build_supersede_compensation_actions(
        self,
        *,
        target: dict[str, Any],
        finished_at: str,
        event_ids: list[str],
        cycle_ids: list[str],
        reason: str,
    ) -> list[dict[str, Any]]:
        # 誤置換は、置換元を戻し、置換先として作られた related unit を無効化する。
        action = self._build_restore_previous_action(
            target=target,
            correction_kind="supersede_compensation",
            finished_at=finished_at,
            event_ids=event_ids,
            reason=reason,
        )
        if action is None:
            return []

        actions = [action]
        source_unit_id = target["memory_unit"].get("memory_unit_id")
        for related_unit in target.get("related_memory_units", []):
            if not isinstance(related_unit, dict):
                continue
            if related_unit.get("status") in {"revoked", "superseded"}:
                continue
            revoked_unit = self.action_resolver.build_revoked_memory_unit(
                existing=related_unit,
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
            )
            actions.append(
                self._build_correct_action(
                    target=target,
                    memory_unit=revoked_unit,
                    related_memory_unit_ids=[source_unit_id] if isinstance(source_unit_id, str) else [],
                    before_snapshot=related_unit,
                    after_snapshot=revoked_unit,
                    correction_kind="supersede_compensation",
                    reason=reason,
                    finished_at=finished_at,
                    event_ids=event_ids,
                    correction_group_id=action["correction"]["correction_group_id"],
                )
            )
        return actions

    def _build_correct_action(
        self,
        *,
        target: dict[str, Any],
        memory_unit: dict[str, Any],
        related_memory_unit_ids: list[str],
        before_snapshot: dict[str, Any] | None,
        after_snapshot: dict[str, Any] | None,
        correction_kind: str,
        reason: str,
        finished_at: str,
        event_ids: list[str],
        correction_group_id: str | None = None,
    ) -> dict[str, Any]:
        revision = target["revision"]
        action_reason = reason or "利用者の訂正により直近の記憶更新を補正したため。"
        group_id = correction_group_id or f"correction:{uuid.uuid4().hex}"
        return self.action_resolver.build_memory_action(
            operation="correct",
            memory_set_id=memory_unit["memory_set_id"],
            finished_at=finished_at,
            memory_unit=memory_unit,
            related_memory_unit_ids=related_memory_unit_ids,
            before_snapshot=before_snapshot,
            after_snapshot=after_snapshot,
            reason=action_reason,
            event_ids=event_ids,
            correction={
                "corrects_revision_id": revision.get("revision_id"),
                "correction_group_id": group_id,
                "correction_basis_event_ids": event_ids,
                "correction_reason": action_reason,
                "correction_kind": correction_kind,
                "corrected_operation": target.get("operation"),
            },
        )

    def _compact_target(self, target: dict[str, Any]) -> dict[str, Any]:
        # LLMに渡す候補は、対象選定に必要な最小情報に絞る。
        unit = target.get("memory_unit", {})
        revision = target.get("revision", {})
        before_snapshot = revision.get("before_snapshot")
        after_snapshot = revision.get("after_snapshot")
        return {
            "revision_id": revision.get("revision_id"),
            "memory_unit_id": unit.get("memory_unit_id"),
            "memory_type": unit.get("memory_type"),
            "scope_type": unit.get("scope_type"),
            "scope_key": unit.get("scope_key"),
            "subject_ref": unit.get("subject_ref"),
            "predicate": unit.get("predicate"),
            "object_ref_or_value": unit.get("object_ref_or_value"),
            "summary_text": unit.get("summary_text"),
            "status": unit.get("status"),
            "confidence": unit.get("confidence"),
            "salience": unit.get("salience"),
            "last_operation": target.get("operation"),
            "last_reason": revision.get("reason"),
            "before_summary_text": before_snapshot.get("summary_text") if isinstance(before_snapshot, dict) else None,
            "after_summary_text": after_snapshot.get("summary_text") if isinstance(after_snapshot, dict) else None,
            "related_memory_units": [
                {
                    "memory_unit_id": related_unit.get("memory_unit_id"),
                    "summary_text": related_unit.get("summary_text"),
                    "status": related_unit.get("status"),
                }
                for related_unit in target.get("related_memory_units", [])
                if isinstance(related_unit, dict)
            ],
            "source_cycle_ids": target.get("source_cycle_ids", []),
        }

    def _trace(
        self,
        *,
        selection: dict[str, Any],
        targets: list[dict[str, Any]],
        selected_targets: list[dict[str, Any]],
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # trace
        correction_group_ids: list[str] = []
        for action in actions:
            correction = action.get("correction", {})
            group_id = correction.get("correction_group_id") if isinstance(correction, dict) else None
            if isinstance(group_id, str) and group_id not in correction_group_ids:
                correction_group_ids.append(group_id)
        return {
            "result_status": "succeeded",
            "selection_status": selection.get("correction_status"),
            "target_candidate_count": len(targets),
            "selected_target_count": len(selected_targets),
            "selected_revision_ids": [
                target.get("revision", {}).get("revision_id")
                for target in selected_targets
                if target.get("revision", {}).get("revision_id")
            ],
            "correction_group_ids": correction_group_ids,
            "action_count": len(actions),
            "operation_counts": action_counts(actions),
            "actions": [
                {
                    "revision_id": action.get("revision_id"),
                    "memory_unit_id": action.get("memory_unit_id"),
                    "operation": action.get("operation"),
                    "corrects_revision_id": action.get("correction", {}).get("corrects_revision_id")
                    if isinstance(action.get("correction"), dict)
                    else None,
                    "correction_kind": action.get("correction", {}).get("correction_kind")
                    if isinstance(action.get("correction"), dict)
                    else None,
                }
                for action in actions
            ],
            "failure_reason": None,
        }
