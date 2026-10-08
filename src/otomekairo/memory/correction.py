from __future__ import annotations

import uuid
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.memory.utils import action_counts

if TYPE_CHECKING:
    from otomekairo.store.file_store import FileStore


# 直近の更新と、今回の入力に対して検索された記憶を訂正審査へ渡す。
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
        candidate_memory_unit_ids: list[str],
    ) -> dict[str, Any]:
        # 返答用の想起採否とは独立した候補
        targets = self.store.list_recent_memory_revision_targets_for_correction(
            memory_set_id=memory_set_id,
            before_finished_at=finished_at,
            exclude_cycle_id=cycle_id,
            cycle_limit=CORRECTION_CYCLE_LIMIT,
            limit=CORRECTION_TARGET_LIMIT,
            candidate_memory_unit_ids=candidate_memory_unit_ids,
        )
        event_ids = list(dict.fromkeys(
            event_id for target in targets for event_id in target["revision"].get("evidence_event_ids", [])
        ))
        evidence = self.store.load_events_for_evidence(
            memory_set_id=memory_set_id, event_ids=event_ids, limit=len(event_ids),
        ) if event_ids else []
        evidence_by_id = {event["event_id"]: event for event in evidence}
        if set(evidence_by_id) != set(event_ids):
            raise ValueError("訂正候補の根拠イベントを取得できません。")
        targets = [{**target, "source_evidence_events": [
            evidence_by_id[event_id] for event_id in dict.fromkeys(target["revision"].get("evidence_event_ids", []))
        ]} for target in targets]
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

    def compact_target(self, target: dict[str, Any]) -> dict[str, Any]:
        return self._compact_target(target)

    def run(
        self,
        *,
        context: dict[str, Any] | None,
        finished_at: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        # 入力なし
        if not isinstance(context, dict):
            return [], self.skipped_trace(reason="no_context")

        targets = context.get("targets")
        if context.get("selection_review_issue") == "review_found_unselected_correction":
            return [], {
                **self.skipped_trace(reason="review_found_unselected_correction"),
                "result_status": "failed",
                "selection_status": "failed",
                "target_candidate_count": len(targets) if isinstance(targets, list) else 0,
                "failure_reason": "記憶候補審査が過去の主張の誤りを認めましたが、記憶解釈は訂正対象を選定しませんでした。",
            }
        if not isinstance(targets, list) or not targets:
            return [], self.skipped_trace(reason="no_targets")

        selection = context.get("selection")
        if not isinstance(selection, dict):
            return [], {
                **self.skipped_trace(reason="no_selection"),
                "result_status": "failed",
                "selection_status": "failed",
                "target_candidate_count": len(targets),
                "failure_reason": "memory_interpretation の訂正選定がありません。",
            }
        try:
            from otomekairo.llm.contracts import validate_memory_correction_reconciliation_contract

            validate_memory_correction_reconciliation_contract(selection)
        except Exception as exc:  # noqa: BLE001
            return [], {
                "result_status": "failed",
                "selection_status": "failed",
                "target_candidate_count": len(targets),
                "selected_target_count": 0,
                "selected_revision_ids": [],
                "correction_group_ids": [],
                "action_count": 0,
                "operation_counts": {},
                "actions": [],
                "failure_reason": str(exc),
            }

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
                raise ValueError("訂正選定のrevision_idが提示候補にありません。")
            target_actions = self._build_actions_for_target(
                target=target,
                selected_memory_unit_id=item.get("memory_unit_id"),
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
                reason=str(item.get("reason_summary") or "").strip(),
            )
            if not target_actions:
                raise ValueError("選定済みの訂正対象から補正actionを作れません。")
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
        if operation == "create":
            return self._build_revoke_created_actions(
                target=target,
                finished_at=finished_at,
                event_ids=event_ids,
                cycle_ids=cycle_ids,
                reason=reason,
            )

        if operation in {"reinforce", "refine", "revoke", "dormant"}:
            action = self._build_restore_previous_action(
                target=target,
                correction_kind="restore_previous",
                finished_at=finished_at,
                event_ids=event_ids,
                reason=reason,
            )
            return [action] if action is not None else []

        if operation == "supersede":
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
        snapshot_keys = (
            "memory_type", "scope_type", "scope_key", "subject_ref", "predicate",
            "object_ref_or_value", "summary_text", "status", "commitment_state",
            "qualifiers", "confidence", "salience", "formed_at", "last_confirmed_at",
            "valid_from", "valid_to", "evidence_event_ids", "evidence_cycle_ids",
        )

        def compact_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
            if snapshot is None:
                return None
            return {key: deepcopy(snapshot[key]) for key in snapshot_keys if key in snapshot}

        return {
            "revision_id": revision.get("revision_id"),
            "memory_unit_id": unit.get("memory_unit_id"),
            "last_operation": target.get("operation"),
            "last_reason": revision.get("reason"),
            "occurred_at": target.get("occurred_at"),
            "before_snapshot": compact_snapshot(revision.get("before_snapshot")),
            "after_snapshot": compact_snapshot(revision.get("after_snapshot")),
            "current_memory_unit": compact_snapshot(unit),
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
            "source_evidence_events": [{key: deepcopy(event[key]) for key in (
                "event_id", "cycle_id", "created_at", "kind", "role", "source_kind", "run_id",
                "terminal_status", "interaction_ref", "speaker_ref", "participant_refs", "text",
                "objective_summary", "history_summary", "observed_result_summaries",
            ) if key in event} for event in target.get("source_evidence_events", [])],
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
