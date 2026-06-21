from __future__ import annotations

from typing import Any

from otomekairo.llm.contexts import InitiativeCandidateFamily
from otomekairo.llm.contracts import INITIATIVE_ENTRY_ENTER_BASIS_VALUES


INITIATIVE_DRIVE_FOREGROUND_MIN_SUPPORT_COUNT = 2
INITIATIVE_DRIVE_FOREGROUND_MIN_SUPPORT_STRENGTH = 0.72
INITIATIVE_DRIVE_FOREGROUND_SINGLE_SUPPORT_STRENGTH = 0.82
INITIATIVE_DRIVE_FOREGROUND_SIGNAL_STRENGTH = 0.45


class ServiceInputInitiativeFamiliesMixin:
    def _initiative_candidate_families(
        self,
        *,
        trigger_kind: str,
        drive_summaries: list[dict[str, Any]],
        world_state_summary: list[dict[str, Any]],
        status_refresh_world_state_summary: list[dict[str, Any]],
        recent_turn_summary: list[dict[str, str]],
        foreground_signal_summary: dict[str, Any],
        initiative_entry_summary: dict[str, Any] | None,
        suppression_summary: dict[str, Any],
        ongoing_action_summary: dict[str, Any] | None,
        selected_candidate: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None,
        initiative_baseline: dict[str, Any],
        intervention_state: dict[str, Any],
        capability_summary: dict[str, Any],
    ) -> list[InitiativeCandidateFamily]:
        pending_pool_count = 0
        pending_eligible_count = 0
        selection_reason: str | None = None
        if isinstance(pending_intent_selection, dict):
            pending_pool_count = int(pending_intent_selection.get("candidate_pool_count", 0))
            pending_eligible_count = int(pending_intent_selection.get("eligible_candidate_count", 0))
            selection_reason = self._client_context_text(
                pending_intent_selection.get("selection_reason"),
                limit=160,
            )
        candidate_families = [
            self._initiative_ongoing_action_family(
                ongoing_action_summary=ongoing_action_summary,
                capability_summary=capability_summary,
            ),
            self._initiative_pending_intent_family(
                selected_candidate=selected_candidate,
                pool_count=pending_pool_count,
                eligible_count=pending_eligible_count,
                selection_reason=selection_reason,
            ),
            self._initiative_autonomous_family(
                trigger_kind=trigger_kind,
                drive_summaries=drive_summaries,
                world_state_summary=world_state_summary,
                status_refresh_world_state_summary=status_refresh_world_state_summary,
                recent_turn_summary=recent_turn_summary,
                foreground_signal_summary=foreground_signal_summary,
                initiative_entry_summary=initiative_entry_summary,
                suppression_summary=suppression_summary,
                initiative_baseline=initiative_baseline,
                intervention_state=intervention_state,
                capability_summary=capability_summary,
            ),
        ]
        selected_family = self._initiative_selected_candidate_family_name(candidate_families)
        return [
            family.with_selected(selected=family.family == selected_family and family.available is True)
            for family in candidate_families
        ]

    def _initiative_ongoing_action_family(
        self,
        *,
        ongoing_action_summary: dict[str, Any] | None,
        capability_summary: dict[str, Any],
    ) -> InitiativeCandidateFamily:
        if not isinstance(ongoing_action_summary, dict):
            return InitiativeCandidateFamily(
                family="ongoing_action",
                available=False,
                selected=False,
                priority_score=0.0,
                blocking_reason_summary="継続中の ongoing_action は無い。",
            )
        status = self._client_context_text(ongoing_action_summary.get("status"), limit=48)
        last_capability_id = self._client_context_text(ongoing_action_summary.get("last_capability_id"), limit=64)
        available_ids = capability_summary.get("available_ids", [])
        capability_available = isinstance(last_capability_id, str) and last_capability_id in available_ids
        preferred_result_kind: str | None = None
        preferred_result_reason: str | None = None
        blocking_reason: str | None = None
        if status == "waiting_result":
            blocking_reason = "ongoing_action が結果待ちで、今は新しい介入より待機を判断材料にする。"
        elif status in {"active", "continued"}:
            if capability_available:
                preferred_result_kind = "capability_request"
                if last_capability_id is not None:
                    preferred_result_reason = f"{last_capability_id} の follow-up を継続できる。"
                else:
                    preferred_result_reason = "利用可能な follow-up capability があり、そのまま継続できる。"
            elif last_capability_id is not None:
                blocking_reason = f"{last_capability_id} の follow-up を考えたいが、現時点では利用できない。"
        return InitiativeCandidateFamily(
            family="ongoing_action",
            available=True,
            selected=False,
            priority_score=1.0,
            reason_summary=self._initiative_ongoing_action_family_reason(
                ongoing_action_summary,
                capability_available=capability_available,
            ),
            preferred_result_kind=preferred_result_kind,
            preferred_result_reason_summary=preferred_result_reason,
            blocking_reason_summary=blocking_reason,
        )

    def _initiative_pending_intent_family(
        self,
        *,
        selected_candidate: dict[str, Any] | None,
        pool_count: int,
        eligible_count: int,
        selection_reason: str | None,
    ) -> InitiativeCandidateFamily:
        if isinstance(selected_candidate, dict):
            return InitiativeCandidateFamily(
                family="pending_intent",
                available=True,
                selected=False,
                priority_score=1.0,
                reason_summary=self._initiative_pending_intent_family_reason(
                    selected_candidate=selected_candidate,
                    pool_count=pool_count,
                    eligible_count=eligible_count,
                    selection_reason=selection_reason,
                ),
            )
        if eligible_count > 0:
            return InitiativeCandidateFamily(
                family="pending_intent",
                available=False,
                selected=False,
                priority_score=0.0,
                reason_summary=self._initiative_pending_intent_family_reason(
                    selected_candidate=None,
                    pool_count=pool_count,
                    eligible_count=eligible_count,
                    selection_reason=selection_reason,
                ),
                blocking_reason_summary="pending_intent 候補は due だが、今回の再評価対象には選ばれていない。",
            )
        if pool_count > 0:
            return InitiativeCandidateFamily(
                family="pending_intent",
                available=False,
                selected=False,
                priority_score=0.0,
                reason_summary=self._initiative_pending_intent_family_reason(
                    selected_candidate=None,
                    pool_count=pool_count,
                    eligible_count=eligible_count,
                    selection_reason=selection_reason,
                ),
                blocking_reason_summary="pending_intent 候補はあるが、まだ due ではない。",
            )
        return InitiativeCandidateFamily(
            family="pending_intent",
            available=False,
            selected=False,
            priority_score=0.0,
            blocking_reason_summary="前景に出す pending_intent 候補はまだ無い。",
        )

    def _initiative_autonomous_family(
        self,
        *,
        trigger_kind: str,
        drive_summaries: list[dict[str, Any]],
        world_state_summary: list[dict[str, Any]],
        status_refresh_world_state_summary: list[dict[str, Any]],
        recent_turn_summary: list[dict[str, str]],
        foreground_signal_summary: dict[str, Any],
        initiative_entry_summary: dict[str, Any] | None,
        suppression_summary: dict[str, Any],
        initiative_baseline: dict[str, Any],
        intervention_state: dict[str, Any],
        capability_summary: dict[str, Any],
    ) -> InitiativeCandidateFamily:
        _ = status_refresh_world_state_summary, foreground_signal_summary, initiative_baseline, intervention_state
        entry_kind = (
            initiative_entry_summary.get("entry_kind")
            if isinstance(initiative_entry_summary, dict)
            else None
        )
        entry_basis = (
            initiative_entry_summary.get("entry_basis")
            if isinstance(initiative_entry_summary, dict)
            else None
        )
        # autonomous family は構造値が強い drive_state または強い entry basis を候補材料にする。
        entry_is_strong = entry_kind == "enter" and entry_basis in INITIATIVE_ENTRY_ENTER_BASIS_VALUES
        foreground_drives = self._initiative_foreground_drive_summaries(drive_summaries)
        available = bool(foreground_drives or entry_is_strong)
        if not available:
            blocking_reason = "drive_state も外向きの自律判断入口もまだ無い。"
            if drive_summaries:
                blocking_reason = "drive_state はあるが、判断材料としては背景寄りで、他の前景材料と合わせて扱う。"
            if entry_kind == "enter":
                blocking_reason = (
                    "自律入口判定は enter だが、entry_basis が同一活動内の詳細変化または観測のみで、"
                    "判断材料としては背景寄りである。drive_state も他の前景材料と合わせて扱う。"
                )
            return InitiativeCandidateFamily(
                family="autonomous",
                available=False,
                selected=False,
                priority_score=0.0,
                reason_summary=self._initiative_autonomous_family_reason(
                    drive_summaries=drive_summaries,
                    foreground_drive_summaries=foreground_drives,
                    strongest_drive=drive_summaries[0] if drive_summaries else None,
                    world_state_summary=world_state_summary,
                    recent_turn_summary=recent_turn_summary,
                    initiative_entry_summary=initiative_entry_summary,
                    suppression_summary=suppression_summary,
                    capability_summary=capability_summary,
                ),
                blocking_reason_summary=blocking_reason,
            )
        strongest_drive = (
            foreground_drives[0]
            if foreground_drives
            else drive_summaries[0] if drive_summaries else None
        )
        preferred_result_kind: str | None = None
        preferred_result_reason: str | None = None
        preferred_capability_id: str | None = None
        preferred_capability_input: dict[str, Any] | None = None
        blocking_reason = None
        return InitiativeCandidateFamily(
            family="autonomous",
            available=True,
            selected=False,
            priority_score=1.0,
            reason_summary=self._initiative_autonomous_family_reason(
                drive_summaries=drive_summaries,
                foreground_drive_summaries=foreground_drives,
                strongest_drive=strongest_drive,
                world_state_summary=world_state_summary,
                recent_turn_summary=recent_turn_summary,
                initiative_entry_summary=initiative_entry_summary,
                suppression_summary=suppression_summary,
                capability_summary=capability_summary,
            ),
            preferred_result_kind=preferred_result_kind,
            preferred_result_reason_summary=preferred_result_reason,
            blocking_reason_summary=blocking_reason,
            preferred_capability_id=preferred_capability_id,
            preferred_capability_input=preferred_capability_input,
        )

    def _initiative_foreground_drive_summaries(
        self,
        drive_summaries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # drive_state は背景動機なので、本文ではなく構造化された強度だけで前景化を判定する。
        return [
            drive_summary
            for drive_summary in drive_summaries
            if self._initiative_drive_is_foregrounded(drive_summary)
        ]

    def _initiative_drive_is_foregrounded(self, drive_summary: dict[str, Any]) -> bool:
        freshness_hint = self._client_context_text(drive_summary.get("freshness_hint"), limit=16)
        stability_hint = self._client_context_text(drive_summary.get("stability_hint"), limit=16)
        if freshness_hint == "stale" or stability_hint == "weak":
            return False

        support_count = self._initiative_drive_support_count(drive_summary)
        support_strength = self._initiative_drive_score(drive_summary, "support_strength")
        signal_strength = self._initiative_drive_score(drive_summary, "signal_strength")

        if signal_strength >= INITIATIVE_DRIVE_FOREGROUND_SIGNAL_STRENGTH:
            return True
        if support_strength >= INITIATIVE_DRIVE_FOREGROUND_SINGLE_SUPPORT_STRENGTH:
            return True
        return (
            support_count >= INITIATIVE_DRIVE_FOREGROUND_MIN_SUPPORT_COUNT
            and support_strength >= INITIATIVE_DRIVE_FOREGROUND_MIN_SUPPORT_STRENGTH
        )

    def _initiative_drive_support_count(self, drive_summary: dict[str, Any]) -> int:
        support_count = drive_summary.get("support_count")
        if isinstance(support_count, int):
            return max(0, support_count)
        return 0

    def _initiative_drive_score(self, drive_summary: dict[str, Any], key: str) -> float:
        value = drive_summary.get(key)
        if isinstance(value, (int, float)):
            return max(0.0, min(float(value), 1.0))
        return 0.0

    def _initiative_selected_candidate_family_name(
        self,
        candidate_families: list[InitiativeCandidateFamily],
    ) -> str | None:
        for family_name in ("pending_intent", "ongoing_action", "autonomous"):
            for family in candidate_families:
                if family.available is True and family.family == family_name:
                    return family_name
        return None

    def _initiative_selected_candidate_family(
        self,
        candidate_families: list[InitiativeCandidateFamily],
    ) -> str | None:
        for family in candidate_families:
            if family.selected is not True:
                continue
            if family.family.strip():
                return family.family.strip()
        return None

    def _initiative_selected_family_entry(
        self,
        *,
        candidate_families: list[InitiativeCandidateFamily],
        selected_candidate_family: str | None,
    ) -> InitiativeCandidateFamily | None:
        for family in candidate_families:
            if family.selected is True:
                return family
            if (
                isinstance(selected_candidate_family, str)
                and family.family.strip() == selected_candidate_family
            ):
                return family
        return None

    def _initiative_has_ongoing_action_candidate(
        self,
        ongoing_action_summary: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(ongoing_action_summary, dict):
            return False
        status = ongoing_action_summary.get("status")
        return isinstance(status, str) and status.strip() != ""

    def _initiative_ongoing_action_family_reason(
        self,
        ongoing_action_summary: dict[str, Any] | None,
        *,
        capability_available: bool,
    ) -> str | None:
        if not isinstance(ongoing_action_summary, dict):
            return None
        status = self._client_context_text(ongoing_action_summary.get("status"), limit=48)
        step_summary = self._client_context_text(ongoing_action_summary.get("step_summary"), limit=120)
        last_capability_id = self._client_context_text(ongoing_action_summary.get("last_capability_id"), limit=64)
        parts: list[str] = []
        if status is not None:
            parts.append(f"status={status}")
        if step_summary is not None:
            parts.append(step_summary)
        if last_capability_id is not None:
            if capability_available:
                parts.append(f"{last_capability_id} の follow-up を続けられる")
            else:
                parts.append(f"{last_capability_id} の follow-up を見直したい")
        if not parts:
            return None
        return "ongoing_action は " + " / ".join(parts) + "。"

    def _initiative_pending_intent_family_reason(
        self,
        *,
        selected_candidate: dict[str, Any] | None,
        pool_count: int,
        eligible_count: int,
        selection_reason: str | None,
    ) -> str | None:
        if isinstance(selected_candidate, dict):
            intent_summary = self._client_context_text(selected_candidate.get("intent_summary"), limit=120)
            if intent_summary is not None and selection_reason is not None:
                return f"selected pending_intent は {intent_summary}。{selection_reason}"
            if intent_summary is not None:
                return f"selected pending_intent は {intent_summary}"
            if selection_reason is not None:
                return selection_reason
            return "selected pending_intent 候補が前景にある。"
        if eligible_count > 0:
            if selection_reason is not None:
                return f"再評価できる pending_intent 候補が {eligible_count} 件あり、{selection_reason}"
            return f"再評価できる pending_intent 候補が {eligible_count} 件ある。"
        if pool_count > 0:
            return f"pending_intent 候補は {pool_count} 件あるが、まだ due ではない。"
        return None

    def _initiative_autonomous_family_reason(
        self,
        *,
        drive_summaries: list[dict[str, Any]],
        foreground_drive_summaries: list[dict[str, Any]],
        strongest_drive: dict[str, Any] | None,
        world_state_summary: list[dict[str, Any]],
        recent_turn_summary: list[dict[str, str]],
        initiative_entry_summary: dict[str, Any] | None,
        suppression_summary: dict[str, Any],
        capability_summary: dict[str, Any],
    ) -> str | None:
        parts: list[str] = []
        if isinstance(initiative_entry_summary, dict):
            reason_summary = self._client_context_text(initiative_entry_summary.get("reason_summary"), limit=180)
            entry_basis = self._client_context_text(initiative_entry_summary.get("entry_basis"), limit=48)
            if entry_basis is not None:
                parts.append(f"自律入口basis={entry_basis}")
            if reason_summary is not None:
                parts.append(f"自律入口理由={reason_summary}")
        if drive_summaries:
            parts.append(f"drive_state {len(drive_summaries)} 件")
            if foreground_drive_summaries:
                parts.append(f"強く前景化した drive_state {len(foreground_drive_summaries)} 件")
            else:
                parts.append("drive_state は背景材料")
        if isinstance(strongest_drive, dict):
            strongest_summary = self._client_context_text(strongest_drive.get("summary_text"), limit=120)
            strongest_kind = self._client_context_text(strongest_drive.get("drive_kind"), limit=48)
            freshness_hint = self._client_context_text(strongest_drive.get("freshness_hint"), limit=16)
            stability_hint = self._client_context_text(strongest_drive.get("stability_hint"), limit=16)
            if strongest_summary is not None:
                if strongest_kind is not None:
                    parts.append(f"strongest drive={strongest_kind}:{strongest_summary}")
                else:
                    parts.append(f"strongest drive={strongest_summary}")
            if freshness_hint is not None:
                parts.append(f"drive freshness={freshness_hint}")
            if stability_hint is not None:
                parts.append(f"drive stability={stability_hint}")
        if world_state_summary:
            parts.append(f"foreground_world_state {len(world_state_summary)} 件")
        if recent_turn_summary:
            parts.append(f"recent_turn {len(recent_turn_summary)} 件")
        suppression_level = self._initiative_suppression_level(suppression_summary)
        if suppression_level == "high":
            parts.append(f"suppression={suppression_level}")
        available_count = int(capability_summary.get("available_count", 0))
        if available_count > 0:
            parts.append(f"available capability {available_count} 件")
        if not parts:
            return None
        return " / ".join(parts) + " が自律判断の材料にある。"
