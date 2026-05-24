from __future__ import annotations

from typing import Any

from otomekairo.llm_contexts import InitiativeCandidateFamily, InitiativeContext
from otomekairo.service_common import debug_log
from otomekairo.service_input_constants import (
    INITIATIVE_AUTONOMOUS_PROBE_SCORE,
    INITIATIVE_AUTONOMOUS_PROBE_THRESHOLD,
    INITIATIVE_BASELINE_SCORES,
    INITIATIVE_DRIVE_FRESHNESS_ADJUSTMENTS,
    INITIATIVE_DRIVE_KIND_SCORES,
)
from otomekairo.world_state_models import WorldStateTrace


class ServiceInputInitiativeMixin:
    def _build_initiative_context(
        self,
        *,
        state: dict[str, Any],
        persona: dict[str, Any],
        current_time: str,
        time_context: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        trigger_kind: str,
        client_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        world_state_trace: WorldStateTrace | None,
        ongoing_action_summary: dict[str, Any] | None,
        capability_decision_view: list[dict[str, Any]] | None,
        selected_candidate: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None,
    ) -> InitiativeContext | None:
        if trigger_kind not in {"wake", "background_wake"}:
            return None
        drive_summaries = self._initiative_drive_summaries(drive_state_summary)
        pending_intent_summaries = self._initiative_pending_intent_summaries(selected_candidate)
        world_state_summary = foreground_world_state or []
        status_refresh_world_state_summary = self._initiative_status_refresh_world_state_summary(
            foreground_world_state=foreground_world_state,
            world_state_trace=world_state_trace,
            trigger_kind=trigger_kind,
        )
        initiative_baseline = self._initiative_baseline_summary(persona)
        runtime_state_summary = self._initiative_runtime_state_summary(
            state=state,
            ongoing_action_summary=ongoing_action_summary,
        )
        recent_turn_summary = self._initiative_recent_turn_summary(recent_turns)
        foreground_signal_summary = self._initiative_foreground_signal_summary(
            trigger_kind=trigger_kind,
            client_context=client_context,
            world_state_summary=world_state_summary,
        )
        intervention_state = self._initiative_intervention_state(
            current_time=current_time,
            trigger_kind=trigger_kind,
            selected_candidate=selected_candidate,
        )
        capability_summary = self._initiative_capability_summary(capability_decision_view)
        intervention_risk_summary = self._initiative_intervention_risk_summary(
            initiative_baseline=initiative_baseline,
            intervention_state=intervention_state,
            trigger_kind=trigger_kind,
            ongoing_action_summary=ongoing_action_summary,
            capability_summary=capability_summary,
            selected_candidate=selected_candidate,
            pending_intent_selection=pending_intent_selection,
        )
        suppression_summary = self._initiative_suppression_summary(
            intervention_state=intervention_state,
            intervention_risk_summary=intervention_risk_summary,
        )
        candidate_families = self._initiative_candidate_families(
            trigger_kind=trigger_kind,
            drive_summaries=drive_summaries,
            world_state_summary=world_state_summary,
            status_refresh_world_state_summary=status_refresh_world_state_summary,
            recent_turn_summary=recent_turn_summary,
            foreground_signal_summary=foreground_signal_summary,
            suppression_summary=suppression_summary,
            ongoing_action_summary=ongoing_action_summary,
            selected_candidate=selected_candidate,
            pending_intent_selection=pending_intent_selection,
            initiative_baseline=initiative_baseline,
            intervention_state=intervention_state,
            capability_summary=capability_summary,
        )
        selected_candidate_family = self._initiative_selected_candidate_family(candidate_families)
        selected_family_entry = self._initiative_selected_family_entry(
            candidate_families=candidate_families,
            selected_candidate_family=selected_candidate_family,
        )
        desktop_signal = self._initiative_desktop_observation_signal(foreground_signal_summary)
        desktop_debug = "-"
        if isinstance(desktop_signal, dict):
            desktop_debug = (
                f"novelty={desktop_signal.get('novelty_kind', '-')}"
                f" eligibility={desktop_signal.get('reply_eligibility', '-')}"
                f" cooldown={desktop_signal.get('cooldown_active', '-')}"
            )
        debug_log(
            "Initiative",
            (
                f"trigger={trigger_kind} selected={selected_candidate_family or '-'} "
                f"preferred={selected_family_entry.preferred_result_kind if selected_family_entry is not None else '-'} "
                f"suppression={suppression_summary.get('suppression_level', '-')} "
                f"foreground={foreground_signal_summary.get('foreground_thinness', '-')} "
                f"desktop={desktop_debug}"
            ),
        )
        return InitiativeContext(
            trigger_kind=trigger_kind,
            opportunity_summary=self._initiative_opportunity_summary(
                trigger_kind=trigger_kind,
                client_context=client_context,
                selected_candidate=selected_candidate,
            ),
            time_context_summary=self._initiative_time_context_summary(time_context=time_context),
            foreground_signal_summary=foreground_signal_summary,
            initiative_baseline=initiative_baseline,
            runtime_state_summary=runtime_state_summary,
            recent_turn_summary=recent_turn_summary,
            drive_summaries=drive_summaries,
            pending_intent_summaries=pending_intent_summaries,
            world_state_summary=world_state_summary,
            ongoing_action_summary=ongoing_action_summary,
            capability_summary=capability_summary,
            candidate_families=candidate_families,
            selected_candidate_family=selected_candidate_family,
            intervention_state=intervention_state,
            suppression_summary=suppression_summary,
            intervention_risk_summary=intervention_risk_summary,
        )

    def _initiative_status_refresh_world_state_summary(
        self,
        *,
        foreground_world_state: list[dict[str, Any]] | None,
        world_state_trace: WorldStateTrace | None,
        trigger_kind: str,
    ) -> list[dict[str, Any]]:
        if trigger_kind in {"wake", "background_wake"}:
            previous = world_state_trace.previous_foreground_world_state if world_state_trace is not None else None
            if isinstance(previous, list):
                return [item for item in previous if isinstance(item, dict)]
        return foreground_world_state or []

    def _initiative_opportunity_summary(
        self,
        *,
        trigger_kind: str,
        client_context: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
    ) -> str:
        if trigger_kind == "background_wake":
            if isinstance(selected_candidate, dict):
                return "background wake が来ており、保留中の候補を再評価する機会がある。"
            return "background wake が来ており、直近入力なしで前進可否を見直す機会がある。"
        if trigger_kind == "wake":
            if isinstance(selected_candidate, dict):
                return "manual wake が呼ばれ、保留中の候補を再評価する機会がある。"
            return "manual wake が呼ばれ、今の前進可否を見直す機会がある。"
        return "自律判断の機会があり、今の前進可否を見直す。"

    def _initiative_time_context_summary(self, *, time_context: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        current_time_text = self._client_context_text(time_context.get("current_time_text"), limit=120)
        if current_time_text is not None:
            payload["current_time_text"] = current_time_text
        part_of_day = self._client_context_text(time_context.get("part_of_day"), limit=16)
        if part_of_day is not None:
            payload["part_of_day"] = part_of_day
            payload["time_band_summary"] = self._initiative_time_band_summary(part_of_day=part_of_day)
        weekday = self._client_context_text(time_context.get("weekday"), limit=16)
        if weekday is not None:
            payload["weekday"] = weekday
        return payload

    def _initiative_time_band_summary(self, *, part_of_day: str) -> str:
        if part_of_day == "morning":
            return "朝の立ち上がり帯で、軽い前進か様子見かを決めたい時間帯。"
        if part_of_day == "daytime":
            return "日中の活動帯で、前景理由があれば動きやすい時間帯。"
        if part_of_day == "evening":
            return "夕方から夜への移行帯で、流れの整理や軽い声かけが自然な時間帯。"
        return "夜間で、押し出しすぎず静かな前進可否を見たい時間帯。"

    def _initiative_foreground_signal_summary(
        self,
        *,
        trigger_kind: str,
        client_context: dict[str, Any],
        world_state_summary: list[dict[str, Any]],
    ) -> dict[str, Any]:
        desktop_signal = self._compact_desktop_observation_signal(
            client_context.get("desktop_observation_signal")
        )
        state_types = sorted(
            {
                item.get("state_type")
                for item in world_state_summary
                if isinstance(item, dict) and isinstance(item.get("state_type"), str)
            }
        )
        if self._desktop_observation_signal_is_judgable(desktop_signal):
            payload = {
                "foreground_thinness": "ready",
                "reason_summary": "desktop wake observation に未発話の新しい前景があり、短い自発 reply の候補になる。",
                "world_state_count": len(world_state_summary),
                "desktop_observation_signal": desktop_signal,
            }
            if state_types:
                payload["state_types"] = state_types[:4]
            return payload
        if not world_state_summary:
            payload = {
                "foreground_thinness": "thin",
                "reason_summary": "前景 world_state がまだ薄く、視覚や周辺状況の追加観測が欲しい。",
                "world_state_count": 0,
            }
            if desktop_signal:
                payload["desktop_observation_signal"] = desktop_signal
            return payload

        grounded_types = {"schedule", "social_context", "body"}
        if grounded_types.intersection(state_types):
            thinness = "grounded"
            reason_summary = "予定・対人・身体の前景があり、いまの状況は比較的具体的に見えている。"
        elif set(state_types).issubset({"visual_context", "external_service", "device"}):
            thinness = "thin"
            reason_summary = "視覚前景や外部状態は見えているが、生活文脈や対人文脈はまだ薄い。"
        else:
            thinness = "mixed"
            reason_summary = "前景 world はあるが、視覚中心の信号と生活文脈が混在している。"
        payload = {
            "foreground_thinness": thinness,
            "reason_summary": reason_summary,
            "world_state_count": len(world_state_summary),
        }
        if state_types:
            payload["state_types"] = state_types[:4]
        if desktop_signal:
            payload["desktop_observation_signal"] = desktop_signal
        return payload

    def _initiative_foreground_thinness(self, foreground_signal_summary: dict[str, Any] | None) -> str | None:
        if not isinstance(foreground_signal_summary, dict):
            return None
        return self._client_context_text(foreground_signal_summary.get("foreground_thinness"), limit=16)

    def _initiative_suppression_level(self, suppression_summary: dict[str, Any] | None) -> str | None:
        if not isinstance(suppression_summary, dict):
            return None
        return self._client_context_text(suppression_summary.get("suppression_level"), limit=16)

    def _initiative_drive_summaries(
        self,
        drive_state_summary: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for drive_state in drive_state_summary or []:
            if not isinstance(drive_state, dict):
                continue
            item: dict[str, Any] = {
                "drive_id": drive_state.get("drive_id"),
                "summary_text": drive_state.get("summary_text"),
                "salience": drive_state.get("salience"),
            }
            for key in ("drive_kind", "focus_scope_type", "focus_scope_key", "freshness_hint", "source_updated_at", "stability_hint"):
                value = drive_state.get(key)
                if isinstance(value, str) and value.strip():
                    item[key] = value.strip()
            support_count = drive_state.get("support_count")
            if isinstance(support_count, int) and support_count > 0:
                item["support_count"] = support_count
            for key in ("support_strength", "scope_alignment", "signal_strength", "persona_alignment"):
                value = drive_state.get(key)
                if isinstance(value, (int, float)):
                    item[key] = round(max(0.0, min(float(value), 1.0)), 2)
            supporting_memory_types = drive_state.get("supporting_memory_types")
            if isinstance(supporting_memory_types, list):
                item["supporting_memory_types"] = [
                    value.strip()
                    for value in supporting_memory_types
                    if isinstance(value, str) and value.strip()
                ][:4]
            scope_support_kinds = drive_state.get("scope_support_kinds")
            if isinstance(scope_support_kinds, list):
                item["scope_support_kinds"] = [
                    value.strip()
                    for value in scope_support_kinds
                    if isinstance(value, str) and value.strip()
                ][:5]
            summaries.append(item)
        return summaries

    def _initiative_drive_priority_score(self, drive_summary: dict[str, Any]) -> float:
        drive_kind = self._client_context_text(drive_summary.get("drive_kind"), limit=48)
        salience = drive_summary.get("salience")
        support_count = drive_summary.get("support_count")
        support_strength = drive_summary.get("support_strength")
        scope_alignment = drive_summary.get("scope_alignment")
        freshness_hint = self._client_context_text(drive_summary.get("freshness_hint"), limit=16)
        signal_strength = drive_summary.get("signal_strength")
        persona_alignment = drive_summary.get("persona_alignment")
        stability_hint = self._client_context_text(drive_summary.get("stability_hint"), limit=16)
        score = INITIATIVE_DRIVE_KIND_SCORES.get(drive_kind or "", 0.08)
        if isinstance(salience, (int, float)):
            score += max(0.0, min(float(salience), 1.0)) * 0.18
        if isinstance(support_count, int) and support_count > 1:
            score += min(0.04, 0.01 * (support_count - 1))
        if isinstance(support_strength, (int, float)):
            score += min(0.08, max(0.0, min(float(support_strength), 1.0)) * 0.08)
        if isinstance(scope_alignment, (int, float)):
            score += max(0.0, min(float(scope_alignment), 1.0) - 0.5) * 0.08
        if freshness_hint is not None:
            score += INITIATIVE_DRIVE_FRESHNESS_ADJUSTMENTS.get(freshness_hint, 0.0)
        if isinstance(signal_strength, (int, float)):
            score += min(0.08, max(0.0, min(float(signal_strength), 1.0)) * 0.08)
        if isinstance(persona_alignment, (int, float)):
            score += (max(0.0, min(float(persona_alignment), 1.0)) - 0.5) * 0.06
        if stability_hint == "stable":
            score += 0.04
        elif stability_hint == "mixed":
            score -= 0.03
        elif stability_hint == "weak":
            score -= 0.07
        return max(0.0, score)

    def _initiative_strongest_drive_summary(
        self,
        drive_summaries: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        strongest: dict[str, Any] | None = None
        strongest_score = -1.0
        for drive_summary in drive_summaries:
            if not isinstance(drive_summary, dict):
                continue
            score = self._initiative_drive_priority_score(drive_summary)
            if score <= strongest_score:
                continue
            strongest = drive_summary
            strongest_score = score
        return strongest

    def _initiative_drive_signal_score(
        self,
        drive_summaries: list[dict[str, Any]],
    ) -> float:
        score = 0.0
        for drive_summary in drive_summaries[:3]:
            if not isinstance(drive_summary, dict):
                continue
            score += min(0.18, self._initiative_drive_priority_score(drive_summary))
        return min(score, 0.34)

    def _initiative_drive_world_alignment_bonus(
        self,
        *,
        strongest_drive: dict[str, Any] | None,
        world_state_summary: list[dict[str, Any]],
    ) -> float:
        if not isinstance(strongest_drive, dict) or not world_state_summary:
            return 0.0
        drive_kind = self._client_context_text(strongest_drive.get("drive_kind"), limit=48)
        state_types = {
            item.get("state_type")
            for item in world_state_summary
            if isinstance(item, dict) and isinstance(item.get("state_type"), str)
        }
        if drive_kind == "follow_through" and "schedule" in state_types:
            return 0.06
        if drive_kind in {"relationship_attunement", "user_attention"} and state_types.intersection(
            {"social_context", "visual_context", "external_service"}
        ):
            return 0.05
        if drive_kind == "self_regulation" and "body" in state_types:
            return 0.05
        if drive_kind == "topic_continuation" and state_types.intersection({"visual_context", "external_service"}):
            return 0.04
        return 0.0

    def _initiative_world_state_is_weak_foreground(self, world_state_summary: list[dict[str, Any]]) -> bool:
        state_types = {
            item.get("state_type")
            for item in world_state_summary
            if isinstance(item, dict) and isinstance(item.get("state_type"), str)
        }
        return bool(state_types) and state_types.issubset({"visual_context", "external_service", "device"})

    def _initiative_autonomous_probe_preference(
        self,
        *,
        trigger_kind: str,
        drive_summaries: list[dict[str, Any]],
        world_state_summary: list[dict[str, Any]],
        status_refresh_world_state_summary: list[dict[str, Any]],
        foreground_signal_summary: dict[str, Any],
        initiative_baseline: dict[str, Any],
        capability_summary: dict[str, Any],
    ) -> dict[str, Any] | None:
        if trigger_kind not in {"wake", "background_wake"}:
            return None
        strongest_drive = self._initiative_strongest_drive_summary(drive_summaries)
        if not isinstance(strongest_drive, dict):
            return None
        status_preference = self._initiative_autonomous_status_refresh_preference(
            strongest_drive=strongest_drive,
            world_state_summary=status_refresh_world_state_summary,
            capability_summary=capability_summary,
        )
        if isinstance(status_preference, dict):
            return status_preference
        status_target = self._initiative_status_refresh_target(strongest_drive)
        if isinstance(status_target, dict) and self._initiative_status_refresh_target_has_fresh_world_state(
            target=status_target,
            world_state_summary=status_refresh_world_state_summary,
        ):
            return None
        if self._initiative_foreground_thinness(foreground_signal_summary) != "thin":
            return None
        available_ids = capability_summary.get("available_ids", [])
        if not isinstance(available_ids, list) or "vision.capture" not in available_ids:
            return None
        vision_source_id = self._initiative_default_vision_source_id(capability_summary)
        if vision_source_id is None:
            return None
        if self._initiative_vision_source_has_fresh_world_state(
            vision_source_id=vision_source_id,
            world_state_summary=status_refresh_world_state_summary,
        ):
            return None
        if self._initiative_drive_priority_score(strongest_drive) < INITIATIVE_AUTONOMOUS_PROBE_THRESHOLD:
            return None
        level = self._client_context_text(initiative_baseline.get("level"), limit=16) or "medium"
        if trigger_kind == "background_wake" and level == "low":
            return None
        state_types = {
            item.get("state_type")
            for item in world_state_summary
            if isinstance(item, dict) and isinstance(item.get("state_type"), str)
        }
        if state_types.intersection({"body", "schedule", "social_context"}):
            return None
        drive_kind = self._client_context_text(strongest_drive.get("drive_kind"), limit=48)
        if drive_kind not in {"follow_through", "relationship_attunement", "user_attention", "topic_continuation"}:
            return None
        return {
            "capability_id": "vision.capture",
            "input": {
                "vision_source_id": vision_source_id,
                "mode": "still",
            },
            "reason_summary": "強い drive はあるが現在の前景観測が薄いため、先に画面観測を当てたい。",
        }

    def _initiative_vision_source_has_fresh_world_state(
        self,
        *,
        vision_source_id: str,
        world_state_summary: list[dict[str, Any]],
    ) -> bool:
        source_key = self._world_state_vision_source_key({"vision_source_id": vision_source_id})
        if source_key is None:
            return False
        target_integration_key = f"visual_context:{source_key}"
        return any(
            isinstance(item, dict)
            and item.get("state_type") == "visual_context"
            and item.get("integration_key") == target_integration_key
            and self._foreground_world_state_is_fresh(item)
            for item in world_state_summary
        )

    def _initiative_autonomous_status_refresh_preference(
        self,
        *,
        strongest_drive: dict[str, Any],
        world_state_summary: list[dict[str, Any]],
        capability_summary: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self._initiative_drive_priority_score(strongest_drive) < INITIATIVE_AUTONOMOUS_PROBE_THRESHOLD:
            return None
        target = self._initiative_status_refresh_target(strongest_drive)
        if target is None:
            return None
        available_ids = capability_summary.get("available_ids", [])
        capability_id = target["capability_id"]
        if not isinstance(available_ids, list) or capability_id not in available_ids:
            return None
        state_type = target["state_type"]
        matching_states = [
            item
            for item in world_state_summary
            if isinstance(item, dict) and item.get("state_type") == state_type
        ]
        if self._initiative_status_refresh_target_has_fresh_world_state(
            target=target,
            world_state_summary=world_state_summary,
        ):
            return None
        if matching_states:
            reason_summary = f"{target['label']}の前景 world_state はあるが新鮮ではないため、現在状態を確認する。"
        else:
            reason_summary = f"{target['label']}の前景 world_state が不足しているため、現在状態を確認する。"
        return {
            "capability_id": capability_id,
            "input": target["input"],
            "reason_summary": reason_summary,
        }

    def _initiative_status_refresh_target_has_fresh_world_state(
        self,
        *,
        target: dict[str, Any],
        world_state_summary: list[dict[str, Any]],
    ) -> bool:
        state_type = self._client_context_text(target.get("state_type"), limit=48)
        if state_type is None:
            return False
        return any(
            isinstance(item, dict)
            and item.get("state_type") == state_type
            and self._foreground_world_state_is_fresh(item)
            for item in world_state_summary
        )

    def _initiative_status_refresh_target(self, strongest_drive: dict[str, Any]) -> dict[str, Any] | None:
        drive_kind = self._client_context_text(strongest_drive.get("drive_kind"), limit=48)
        summary_text = self._client_context_text(strongest_drive.get("summary_text"), limit=240) or ""
        if drive_kind == "relationship_attunement":
            return {
                "capability_id": "social.status",
                "state_type": "social_context",
                "input": {"scope": "social_context"},
                "label": "対人文脈",
            }
        if drive_kind in {"topic_continuation", "follow_through", "user_attention"} and self._contains_any_text(
            summary_text,
            ("外部サービス", "GitHub", "github", "レビュー", "issue", "Issue", "PR", "pull request"),
        ):
            return {
                "capability_id": "external.status",
                "state_type": "external_service",
                "input": {"service": "github"},
                "label": "外部サービス",
            }
        if drive_kind in {"user_attention", "topic_continuation"} and self._contains_any_text(
            summary_text,
            ("端末", "デバイス", "接続", "電源", "バッテリー"),
        ):
            return {
                "capability_id": "device.status",
                "state_type": "device",
                "input": {"scope": "device"},
                "label": "端末状態",
            }
        if drive_kind in {"self_regulation", "user_attention", "topic_continuation"} and self._contains_any_text(
            summary_text,
            ("作業環境", "周囲", "部屋", "騒音", "明るさ", "環境"),
        ):
            return {
                "capability_id": "environment.status",
                "state_type": "environment",
                "input": {"scope": "environment"},
                "label": "周囲環境",
            }
        if drive_kind in {"follow_through", "user_attention", "topic_continuation"} and self._contains_any_text(
            summary_text,
            ("場所", "居場所", "移動", "作業場所", "出先"),
        ):
            return {
                "capability_id": "location.status",
                "state_type": "location",
                "input": {"scope": "location"},
                "label": "場所状態",
            }
        if drive_kind == "user_attention" and self._contains_any_text(
            summary_text,
            ("対人", "会話", "連絡", "通知", "会議", "やり取り"),
        ):
            return {
                "capability_id": "social.status",
                "state_type": "social_context",
                "input": {"scope": "social_context"},
                "label": "対人文脈",
            }
        if drive_kind == "follow_through" and self._contains_any_text(
            summary_text,
            ("予定", "スケジュール", "カレンダー", "このあと", "近日"),
        ):
            return {
                "capability_id": "schedule.status",
                "state_type": "schedule",
                "input": {"range": "near_term"},
                "label": "予定",
            }
        if drive_kind == "self_regulation":
            return {
                "capability_id": "body.status",
                "state_type": "body",
                "input": {"scope": "body"},
                "label": "身体状態",
            }
        return None

    def _initiative_baseline_summary(self, persona: dict[str, Any]) -> dict[str, Any]:
        level = self._client_context_text(persona.get("initiative_baseline"), limit=16)
        if level is None:
            return {}
        if level == "low":
            summary_text = "自発介入は控えめ寄りで、前景理由が弱ければ見送る。"
        elif level == "high":
            summary_text = "自発介入は強めで、前景理由が揃えば一歩前へ出る。"
        else:
            summary_text = "自発介入は中庸で、前景理由と抑制要因の両方を見る。"
        return {
            "level": level,
            "summary_text": summary_text,
        }

    def _initiative_runtime_state_summary(
        self,
        *,
        state: dict[str, Any],
        ongoing_action_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        with self._runtime_state_lock:
            memory_job_in_progress = self._memory_postprocess_runtime_state.get("current_cycle_id") is not None
        return {
            "wake_scheduler_active": self._background_wake_scheduler_active() and state["wake_policy"]["mode"] == "interval",
            "ongoing_action_exists": isinstance(ongoing_action_summary, dict),
            "memory_job_worker_active": self._background_memory_postprocess_worker_active(),
            "pending_memory_job_count": self.store.count_memory_postprocess_jobs(
                result_statuses=["queued", "running"],
            ),
            "memory_job_in_progress": memory_job_in_progress,
        }

    def _initiative_recent_turn_summary(
        self,
        recent_turns: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        payload: list[dict[str, str]] = []
        for turn in recent_turns[-3:]:
            if not isinstance(turn, dict):
                continue
            role = turn.get("role")
            text = turn.get("text")
            if not isinstance(role, str) or not role.strip():
                continue
            if not isinstance(text, str) or not text.strip():
                continue
            payload.append(
                {
                    "role": role.strip(),
                    "text": self._clamp(text.strip(), limit=80) or "",
                }
            )
        return payload

    def _initiative_intervention_state(
        self,
        *,
        current_time: str,
        trigger_kind: str,
        selected_candidate: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "background_trigger": trigger_kind == "background_wake",
        }
        cooldown_reason = self._wake_cooldown_reason(current_time=current_time)
        if cooldown_reason is not None:
            payload["cooldown_active"] = True
            payload["cooldown_reason"] = cooldown_reason
        with self._runtime_state_lock:
            last_spontaneous_at = self._wake_runtime_state.get("last_spontaneous_at")
        if isinstance(last_spontaneous_at, str) and last_spontaneous_at:
            age_label = self._world_state_age_label(
                reference_time=current_time,
                observed_at=last_spontaneous_at,
                updated_at=None,
            )
            if age_label is not None:
                payload["last_spontaneous_reply_age_label"] = age_label
        if isinstance(selected_candidate, dict):
            dedupe_key = selected_candidate.get("dedupe_key")
            if isinstance(dedupe_key, str) and dedupe_key:
                payload["same_dedupe_recently_replied"] = self._was_recently_replied(
                    dedupe_key=dedupe_key,
                    current_time=current_time,
                )
        return payload

    def _initiative_pending_intent_summaries(self, selected_candidate: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(selected_candidate, dict):
            return []
        return [
            {
                "intent_kind": selected_candidate.get("intent_kind"),
                "intent_summary": selected_candidate.get("intent_summary"),
                "reason_summary": selected_candidate.get("reason_summary"),
            }
        ]

    def _initiative_capability_summary(
        self,
        capability_decision_view: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        available_ids: list[str] = []
        available_items: list[dict[str, Any]] = []
        unavailable_items: list[dict[str, Any]] = []
        vision_sources: list[dict[str, Any]] = []
        for item in capability_decision_view or []:
            if not isinstance(item, dict):
                continue
            capability_id = item.get("id")
            if not isinstance(capability_id, str) or not capability_id:
                continue
            if item.get("available"):
                available_ids.append(capability_id)
                available_item = {
                    "id": capability_id,
                    "what_it_does": item.get("what_it_does"),
                    "required_input": item.get("required_input"),
                }
                if capability_id == "vision.capture":
                    vision_sources = self._compact_vision_sources_for_decision(item.get("vision_sources"))
                    available_item["vision_sources"] = vision_sources
                available_items.append(available_item)
                continue
            unavailable_items.append(
                {
                    "id": capability_id,
                    "reason": item.get("unavailable_reason"),
                }
            )
        return {
            "available_count": len(available_ids),
            "available_ids": available_ids,
            "available_items": available_items[:3],
            "unavailable_count": len(unavailable_items),
            "unavailable_items": unavailable_items[:3],
            "vision_sources": vision_sources,
        }

    def _compact_vision_sources_for_decision(self, value: Any) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        if not isinstance(value, list):
            return sources
        for source in value:
            if not isinstance(source, dict):
                continue
            source_id = self._client_context_text(source.get("vision_source_id"), limit=96)
            kind = self._client_context_text(source.get("kind"), limit=32)
            label = self._client_context_text(source.get("label"), limit=80)
            if source_id is None or kind is None or label is None:
                continue
            payload: dict[str, Any] = {
                "vision_source_id": source_id,
                "kind": kind,
                "label": label,
            }
            default_for = [
                value
                for value in source.get("default_for", [])
                if isinstance(value, str) and value.strip()
            ][:6]
            aliases = [
                value
                for value in source.get("aliases", [])
                if isinstance(value, str) and value.strip()
            ][:6]
            if aliases:
                payload["aliases"] = aliases
            if default_for:
                payload["default_for"] = default_for
            sources.append(payload)
        return sources[:6]

    def _initiative_default_vision_source_id(self, capability_summary: dict[str, Any]) -> str | None:
        top_level_sources = capability_summary.get("vision_sources")
        source_id = self._default_vision_source_id_from_sources(top_level_sources)
        if source_id is not None:
            return source_id
        available_items = capability_summary.get("available_items")
        if not isinstance(available_items, list):
            return None
        for item in available_items:
            if not isinstance(item, dict) or item.get("id") != "vision.capture":
                continue
            return self._default_vision_source_id_from_sources(item.get("vision_sources"))
        return None

    def _default_vision_source_id_from_sources(self, value: Any) -> str | None:
        if not isinstance(value, list):
            return None
        for default_name in ("visual", "desktop", "camera"):
            for source in value:
                if not isinstance(source, dict):
                    continue
                default_for = source.get("default_for")
                source_id = source.get("vision_source_id")
                if (
                    isinstance(default_for, list)
                    and default_name in default_for
                    and isinstance(source_id, str)
                    and source_id.strip()
                ):
                    return source_id.strip()
        for source in value:
            if not isinstance(source, dict):
                continue
            source_id = source.get("vision_source_id")
            if isinstance(source_id, str) and source_id.strip():
                return source_id.strip()
        return None

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
        preferred_result_kind = "reply"
        preferred_result_reason: str | None = None
        priority_score = 0.56
        blocking_reason: str | None = None
        if status == "waiting_result":
            priority_score = 0.74
            preferred_result_kind = "noop"
            preferred_result_reason = "ongoing_action が結果待ちで、今は新しい介入より待機を優先する。"
            blocking_reason = preferred_result_reason
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
                preferred_result_kind = "noop"
                preferred_result_reason = f"{last_capability_id} の follow-up を考えたいが、現時点では利用できない。"
                blocking_reason = preferred_result_reason
            else:
                priority_score = 0.68
                preferred_result_reason = "継続中の流れが残っており、短い reply で続きを整えられる。"
        elif status == "on_hold":
            priority_score = 0.42
            preferred_result_kind = "pending_intent"
            preferred_result_reason = "進行中の流れはいったん保留扱いで、pending_intent として持つのが自然。"
        else:
            preferred_result_reason = "継続中の流れが残っており、状況に応じて続きを選びたい。"
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
                preferred_result_kind="reply",
                preferred_result_reason_summary="due になった pending_intent 候補があり、今回は表に出してよい。",
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
                preferred_result_kind="pending_intent",
                preferred_result_reason_summary="再評価対象はあるが、今回は pending_intent として保持するのが自然。",
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
        desktop_signal = self._initiative_desktop_observation_signal(foreground_signal_summary)
        available = bool(drive_summaries or world_state_summary or recent_turn_summary or desktop_signal)
        if not available:
            return InitiativeCandidateFamily(
                family="autonomous",
                available=False,
                selected=False,
                priority_score=0.0,
                blocking_reason_summary="drive_state / world_state / 直近会話の前景がまだ弱い。",
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
        if desktop_signal:
            priority_score += 0.18
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
        preferred_result_kind = "reply"
        preferred_result_reason = self._initiative_autonomous_preferred_result_reason(
            strongest_drive=strongest_drive,
            world_state_summary=world_state_summary,
            recent_turn_summary=recent_turn_summary,
            desktop_signal=desktop_signal,
        )
        desktop_cooldown_novelty = (
            isinstance(desktop_signal, dict)
            and desktop_signal.get("reply_eligibility") == "eligible"
            and desktop_signal.get("cooldown_active") is True
            and desktop_signal.get("novelty_kind") in {"first_success", "changed"}
        )
        preferred_capability_id: str | None = None
        preferred_capability_input: dict[str, Any] | None = None
        if isinstance(probe_preference, dict):
            preferred_result_kind = "capability_request"
            preferred_result_reason = self._client_context_text(probe_preference.get("reason_summary"), limit=160)
            priority_score += INITIATIVE_AUTONOMOUS_PROBE_SCORE
            preferred_capability_id = probe_preference["capability_id"]
            preferred_capability_input = probe_preference["input"]
        elif suppression_level == "high" and not desktop_cooldown_novelty:
            preferred_result_kind = "noop"
            preferred_result_reason = "suppression が high で、今回は押し出さず見送るほうが自然。"
        elif (
            trigger_kind == "background_wake"
            and foreground_thinness == "thin"
            and not drive_summaries
            and self._initiative_world_state_is_weak_foreground(world_state_summary)
        ):
            preferred_result_kind = "noop"
            preferred_result_reason = "background wake で画面や外部状態だけが薄く見えており、drive なしでは見送るほうが自然。"
        elif foreground_thinness == "thin" and not world_state_summary and not recent_turn_summary:
            preferred_result_kind = "noop"
            preferred_result_reason = "前景文脈が薄く、いまは reply より様子見を優先したい。"
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
                desktop_signal=desktop_signal,
            ),
            preferred_result_kind=preferred_result_kind,
            preferred_result_reason_summary=preferred_result_reason,
            blocking_reason_summary=blocking_reason,
            preferred_capability_id=preferred_capability_id,
            preferred_capability_input=preferred_capability_input,
        )

    def _initiative_world_state_signal_score(
        self,
        world_state_summary: list[dict[str, Any]],
    ) -> float:
        weights = {
            "schedule": 0.12,
            "social_context": 0.1,
            "body": 0.08,
            "external_service": 0.08,
            "visual_context": 0.06,
            "device": 0.05,
            "environment": 0.05,
            "location": 0.05,
        }
        score = 0.0
        for item in world_state_summary[:3]:
            if not isinstance(item, dict):
                continue
            state_type = item.get("state_type")
            if not isinstance(state_type, str):
                continue
            weight = weights.get(state_type, 0.04)
            salience = item.get("salience")
            if isinstance(salience, (int, float)):
                weight *= 0.7 + min(max(float(salience), 0.0), 1.0) * 0.5
            score += weight
        return min(score, 0.24)

    def _initiative_autonomous_blocking_reason(
        self,
        *,
        trigger_kind: str,
        drive_summaries: list[dict[str, Any]],
        strongest_drive: dict[str, Any] | None,
        world_state_summary: list[dict[str, Any]],
        foreground_signal_summary: dict[str, Any],
        suppression_summary: dict[str, Any],
        initiative_baseline: dict[str, Any],
        capability_summary: dict[str, Any],
    ) -> str | None:
        desktop_signal = self._initiative_desktop_observation_signal(foreground_signal_summary)
        if desktop_signal:
            return None
        reasons: list[str] = []
        level = self._client_context_text(initiative_baseline.get("level"), limit=16)
        if level == "low":
            reasons.append("initiative_baseline が low")
        if trigger_kind == "background_wake":
            reasons.append("background wake")
        foreground_thinness = self._initiative_foreground_thinness(foreground_signal_summary)
        if foreground_thinness == "thin":
            reasons.append("前景文脈が thin")
        suppression_level = self._initiative_suppression_level(suppression_summary)
        if suppression_level == "high":
            reasons.append("suppression が high")
        elif suppression_level == "medium":
            reasons.append("抑制要因が残る")
        if not drive_summaries and world_state_summary:
            state_types = {
                item.get("state_type")
                for item in world_state_summary
                if isinstance(item, dict) and isinstance(item.get("state_type"), str)
            }
            if state_types and state_types.issubset({"visual_context", "external_service", "device"}):
                reasons.append("前景が視覚や外部状態中心")
        if int(capability_summary.get("available_count", 0)) == 0:
            reasons.append("使える capability が見当たらない")
        freshness_hint = self._client_context_text(
            strongest_drive.get("freshness_hint") if isinstance(strongest_drive, dict) else None,
            limit=16,
        )
        if freshness_hint == "stale":
            reasons.append("前景に出る drive が stale")
        if not reasons:
            return None
        return " / ".join(reasons) + " ため、押し出しは慎重にする。"

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
        desktop_signal: dict[str, Any] | None,
    ) -> str | None:
        parts: list[str] = []
        if desktop_signal:
            novelty_kind = self._client_context_text(desktop_signal.get("novelty_kind"), limit=48)
            summary_text = self._client_context_text(desktop_signal.get("summary_text"), limit=120)
            if novelty_kind is not None and summary_text is not None:
                parts.append(f"desktop observation {novelty_kind}:{summary_text}")
            elif novelty_kind is not None:
                parts.append(f"desktop observation {novelty_kind}")
            else:
                parts.append("desktop observation に未発話の新しい前景")
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

    def _initiative_autonomous_preferred_result_reason(
        self,
        *,
        strongest_drive: dict[str, Any] | None,
        world_state_summary: list[dict[str, Any]],
        recent_turn_summary: list[dict[str, str]],
        desktop_signal: dict[str, Any] | None,
    ) -> str:
        if desktop_signal:
            novelty_kind = self._client_context_text(desktop_signal.get("novelty_kind"), limit=48)
            if novelty_kind in {"first_success", "changed", "pending_after_cooldown"}:
                return "desktop wake observation に未発話の新しい前景があり、短い reply で触れるのが自然。"
            return "desktop wake observation の前景を見て、必要なら短い reply を返せる。"
        if isinstance(strongest_drive, dict) and world_state_summary:
            return "strongest drive と前景 world が噛み合っており、短い reply が自然。"
        if isinstance(strongest_drive, dict) and recent_turn_summary:
            return "strongest drive と直近文脈がつながっており、短い reply が自然。"
        if world_state_summary:
            return "前景 world が見えており、短い reply で触れられる。"
        if recent_turn_summary:
            return "直近文脈が残っており、軽い reply で前へ出られる。"
        return "自発判断の前景が残っており、短い reply を返せる。"

    def _initiative_desktop_observation_signal(
        self,
        foreground_signal_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(foreground_signal_summary, dict):
            return None
        signal = self._compact_desktop_observation_signal(
            foreground_signal_summary.get("desktop_observation_signal")
        )
        if not self._desktop_observation_signal_is_judgable(signal):
            return None
        return signal

    def _initiative_intervention_risk_summary(
        self,
        *,
        initiative_baseline: dict[str, Any],
        intervention_state: dict[str, Any],
        trigger_kind: str,
        ongoing_action_summary: dict[str, Any] | None,
        capability_summary: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None,
    ) -> str | None:
        reasons: list[str] = []
        baseline_level = self._client_context_text(initiative_baseline.get("level"), limit=16)
        if trigger_kind == "background_wake":
            reasons.append("直近入力のない定期 wake なので、過剰介入は避けたい。")
        if baseline_level == "low":
            reasons.append("initiative_baseline が low で、押し出しは控えめにしたい。")
        if intervention_state.get("cooldown_active") is True:
            cooldown_reason = intervention_state.get("cooldown_reason")
            if isinstance(cooldown_reason, str) and cooldown_reason.strip():
                reasons.append(cooldown_reason.strip())
        if intervention_state.get("same_dedupe_recently_replied") is True:
            reasons.append("同じ pending_intent 系統には最近 reply 済みで、連続介入は避けたい。")
        if isinstance(ongoing_action_summary, dict) and ongoing_action_summary.get("status") == "waiting_result":
            reasons.append("ongoing_action が結果待ちで、重複介入は抑えたい。")
        if int(capability_summary.get("available_count", 0)) == 0:
            reasons.append("現時点で使える capability が見当たらない。")
        if not isinstance(selected_candidate, dict):
            pool_count = 0
            if isinstance(pending_intent_selection, dict):
                pool_count = int(pending_intent_selection.get("candidate_pool_count", 0))
            if pool_count == 0:
                reasons.append("前景に出す pending_intent 候補はまだ見当たらない。")
        if not reasons:
            return None
        return " / ".join(reasons)

    def _initiative_suppression_summary(
        self,
        *,
        intervention_state: dict[str, Any],
        intervention_risk_summary: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        suppression_level = "low"
        if intervention_state.get("same_dedupe_recently_replied") is True:
            suppression_level = "high"
        elif (
            intervention_state.get("cooldown_active") is True
            or intervention_state.get("background_trigger") is True
            or intervention_risk_summary is not None
        ):
            suppression_level = "medium"
        payload["suppression_level"] = suppression_level
        if intervention_risk_summary is not None:
            payload["reason_summary"] = intervention_risk_summary
        for key in ("background_trigger", "cooldown_active", "same_dedupe_recently_replied"):
            value = intervention_state.get(key)
            if isinstance(value, bool):
                payload[key] = value
        return payload
