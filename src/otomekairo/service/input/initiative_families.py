from __future__ import annotations

from typing import Any

from otomekairo.llm.contexts import InitiativeCandidateFamily
from otomekairo.service.input.constants import (
    INITIATIVE_AUTONOMOUS_PROBE_SCORE,
    INITIATIVE_BASELINE_SCORES,
)


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
        priority_score = 0.56
        blocking_reason: str | None = None
        if status == "waiting_result":
            priority_score = 0.74
            blocking_reason = "ongoing_action が結果待ちで、今は新しい介入より待機を判断材料にする。"
        elif status in {"active", "continued"}:
            if capability_available:
                priority_score = 0.82
                preferred_result_kind = "capability_request"
                if last_capability_id is not None:
                    preferred_result_reason = f"{last_capability_id} の follow-up を継続できる。"
                else:
                    preferred_result_reason = "利用可能な follow-up capability があり、そのまま継続できる。"
            elif last_capability_id is not None:
                priority_score = 0.48
                blocking_reason = f"{last_capability_id} の follow-up を考えたいが、現時点では利用できない。"
            else:
                priority_score = 0.68
        elif status == "on_hold":
            priority_score = 0.42
        return InitiativeCandidateFamily(
            family="ongoing_action",
            available=True,
            selected=False,
            priority_score=round(priority_score, 2),
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
                priority_score=0.95,
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
                available=True,
                selected=False,
                priority_score=0.52,
                reason_summary=self._initiative_pending_intent_family_reason(
                    selected_candidate=None,
                    pool_count=pool_count,
                    eligible_count=eligible_count,
                    selection_reason=selection_reason,
                ),
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
        suppression_summary: dict[str, Any],
        initiative_baseline: dict[str, Any],
        intervention_state: dict[str, Any],
        capability_summary: dict[str, Any],
    ) -> InitiativeCandidateFamily:
        visual_signal = self._initiative_primary_visual_observation_signal(foreground_signal_summary)
        available = bool(drive_summaries or world_state_summary or recent_turn_summary or visual_signal)
        if not available:
            return InitiativeCandidateFamily(
                family="autonomous",
                available=False,
                selected=False,
                priority_score=0.0,
                blocking_reason_summary="drive_state / world_state / 直近会話 / 視覚観測の前景がまだ弱い。",
            )
        strongest_drive = self._initiative_strongest_drive_summary(drive_summaries)
        level = self._client_context_text(initiative_baseline.get("level"), limit=16) or "medium"
        foreground_thinness = self._initiative_foreground_thinness(foreground_signal_summary)
        suppression_level = self._initiative_suppression_level(suppression_summary)
        priority_score = INITIATIVE_BASELINE_SCORES.get(level, INITIATIVE_BASELINE_SCORES["medium"])
        priority_score += self._initiative_drive_signal_score(drive_summaries)
        priority_score += self._initiative_world_state_signal_score(world_state_summary)
        priority_score += self._initiative_drive_world_alignment_bonus(
            strongest_drive=strongest_drive,
            world_state_summary=world_state_summary,
        )
        visual_change_state = (
            self._client_context_text(visual_signal.get("change_state"), limit=32)
            if isinstance(visual_signal, dict)
            else None
        )
        if visual_change_state in {"first_seen", "changed"}:
            priority_score += 0.08
        elif visual_signal:
            priority_score += 0.02
        if foreground_thinness == "ready":
            priority_score += 0.04
        elif foreground_thinness == "thin":
            priority_score -= 0.08
        if recent_turn_summary:
            priority_score += 0.08
        if int(capability_summary.get("available_count", 0)) > 0:
            priority_score += 0.06
        if trigger_kind == "background_wake":
            priority_score -= 0.06
        if suppression_level == "high":
            priority_score -= 0.18
        elif suppression_level == "medium":
            priority_score -= 0.08
        probe_preference = self._initiative_autonomous_probe_preference(
            trigger_kind=trigger_kind,
            drive_summaries=drive_summaries,
            world_state_summary=world_state_summary,
            status_refresh_world_state_summary=status_refresh_world_state_summary,
            foreground_signal_summary=foreground_signal_summary,
            initiative_baseline=initiative_baseline,
            capability_summary=capability_summary,
        )
        preferred_result_kind: str | None = None
        preferred_result_reason: str | None = None
        preferred_capability_id: str | None = None
        preferred_capability_input: dict[str, Any] | None = None
        if isinstance(probe_preference, dict):
            preferred_result_kind = "capability_request"
            preferred_result_reason = self._client_context_text(probe_preference.get("reason_summary"), limit=160)
            priority_score += INITIATIVE_AUTONOMOUS_PROBE_SCORE
            preferred_capability_id = probe_preference["capability_id"]
            preferred_capability_input = probe_preference["input"]
        blocking_reason = self._initiative_autonomous_blocking_reason(
            trigger_kind=trigger_kind,
            drive_summaries=drive_summaries,
            strongest_drive=strongest_drive,
            world_state_summary=world_state_summary,
            foreground_signal_summary=foreground_signal_summary,
            suppression_summary=suppression_summary,
            initiative_baseline=initiative_baseline,
            capability_summary=capability_summary,
        )
        return InitiativeCandidateFamily(
            family="autonomous",
            available=True,
            selected=False,
            priority_score=round(max(0.0, min(priority_score, 0.9)), 2),
            reason_summary=self._initiative_autonomous_family_reason(
                drive_summaries=drive_summaries,
                strongest_drive=strongest_drive,
                world_state_summary=world_state_summary,
                recent_turn_summary=recent_turn_summary,
                foreground_signal_summary=foreground_signal_summary,
                suppression_summary=suppression_summary,
                initiative_baseline=initiative_baseline,
                capability_summary=capability_summary,
                probe_preference=probe_preference,
                visual_signal=visual_signal,
            ),
            preferred_result_kind=preferred_result_kind,
            preferred_result_reason_summary=preferred_result_reason,
            blocking_reason_summary=blocking_reason,
            preferred_capability_id=preferred_capability_id,
            preferred_capability_input=preferred_capability_input,
        )

    def _initiative_selected_candidate_family_name(
        self,
        candidate_families: list[InitiativeCandidateFamily],
    ) -> str | None:
        selected_family: str | None = None
        selected_score = -1.0
        for family in candidate_families:
            if family.available is not True:
                continue
            family_name = family.family
            if not family_name.strip():
                continue
            if float(family.priority_score) <= selected_score:
                continue
            selected_family = family_name.strip()
            selected_score = float(family.priority_score)
        return selected_family

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
        strongest_drive: dict[str, Any] | None,
        world_state_summary: list[dict[str, Any]],
        recent_turn_summary: list[dict[str, str]],
        foreground_signal_summary: dict[str, Any],
        suppression_summary: dict[str, Any],
        initiative_baseline: dict[str, Any],
        capability_summary: dict[str, Any],
        probe_preference: dict[str, Any] | None,
        visual_signal: dict[str, Any] | None,
    ) -> str | None:
        parts: list[str] = []
        if visual_signal:
            change_state = self._client_context_text(visual_signal.get("change_state"), limit=48)
            source_kind = self._client_context_text(visual_signal.get("source_kind"), limit=32)
            summary_text = self._client_context_text(visual_signal.get("summary_text"), limit=120)
            source_label = source_kind or "visual"
            if change_state is not None and summary_text is not None:
                parts.append(f"{source_label} observation {change_state}:{summary_text}")
            elif change_state is not None:
                parts.append(f"{source_label} observation {change_state}")
            else:
                parts.append("視覚観測が自発判断の前景候補にある")
        if drive_summaries:
            parts.append(f"drive_state {len(drive_summaries)} 件")
        if isinstance(strongest_drive, dict):
            strongest_summary = self._client_context_text(strongest_drive.get("summary_text"), limit=120)
            strongest_kind = self._client_context_text(strongest_drive.get("drive_kind"), limit=48)
            freshness_hint = self._client_context_text(strongest_drive.get("freshness_hint"), limit=16)
            stability_hint = self._client_context_text(strongest_drive.get("stability_hint"), limit=16)
            support_strength = strongest_drive.get("support_strength")
            signal_strength = strongest_drive.get("signal_strength")
            if strongest_summary is not None:
                if strongest_kind is not None:
                    parts.append(f"strongest drive={strongest_kind}:{strongest_summary}")
                else:
                    parts.append(f"strongest drive={strongest_summary}")
            if freshness_hint is not None:
                parts.append(f"drive freshness={freshness_hint}")
            if stability_hint is not None:
                parts.append(f"drive stability={stability_hint}")
            if isinstance(support_strength, (int, float)):
                parts.append(f"drive support={round(max(0.0, min(float(support_strength), 1.0)), 2)}")
            if isinstance(signal_strength, (int, float)) and float(signal_strength) > 0.0:
                parts.append(f"drive signal={round(max(0.0, min(float(signal_strength), 1.0)), 2)}")
        if world_state_summary:
            parts.append(f"foreground_world_state {len(world_state_summary)} 件")
        if recent_turn_summary:
            parts.append(f"recent_turn {len(recent_turn_summary)} 件")
        foreground_thinness = self._initiative_foreground_thinness(foreground_signal_summary)
        if foreground_thinness is not None:
            parts.append(f"foreground={foreground_thinness}")
        suppression_level = self._initiative_suppression_level(suppression_summary)
        if suppression_level in {"medium", "high"}:
            parts.append(f"suppression={suppression_level}")
        available_count = int(capability_summary.get("available_count", 0))
        if available_count > 0:
            parts.append(f"available capability {available_count} 件")
        if isinstance(probe_preference, dict):
            capability_id = self._client_context_text(probe_preference.get("capability_id"), limit=64)
            if capability_id is not None:
                parts.append(f"{capability_id} で前景確認したい")
        baseline_level = self._client_context_text(initiative_baseline.get("level"), limit=16)
        if baseline_level is not None:
            parts.append(f"initiative_baseline={baseline_level}")
        if not parts:
            return None
        return " / ".join(parts) + " が自発判断の前景候補にある。"
