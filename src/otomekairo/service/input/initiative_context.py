from __future__ import annotations

from typing import Any

from otomekairo.llm.contexts import InitiativeContext
from otomekairo.llm.contracts import INITIATIVE_ENTRY_ENTER_BASIS_VALUES
from otomekairo.service.common import debug_log
from otomekairo.world_state.models import WorldStateTrace


class ServiceInputInitiativeContextMixin:
    def _build_initiative_context(
        self,
        *,
        state: dict[str, Any],
        persona: dict[str, Any],
        persona_context_summary: dict[str, Any],
        current_time: str,
        time_context: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        trigger_kind: str,
        client_context: dict[str, Any],
        drive_state_summary: list[dict[str, Any]] | None,
        foreground_world_state: list[dict[str, Any]] | None,
        activity_context: dict[str, Any] | None,
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
        initiative_entry_summary = self._initiative_entry_summary(client_context)
        intervention_state = self._initiative_intervention_state(
            current_time=current_time,
            trigger_kind=trigger_kind,
            selected_candidate=selected_candidate,
        )
        capability_summary = self._initiative_capability_summary(capability_decision_view)
        intervention_risk_summary = self._initiative_intervention_risk_summary(
            initiative_baseline=initiative_baseline,
            intervention_state=intervention_state,
            ongoing_action_summary=ongoing_action_summary,
            capability_summary=capability_summary,
        )
        suppression_summary = self._initiative_suppression_summary(
            drive_summaries=drive_summaries,
            foreground_signal_summary=foreground_signal_summary,
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
            initiative_entry_summary=initiative_entry_summary,
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
        visual_signals = self._initiative_visual_observation_signals(foreground_signal_summary)
        visual_debug = "-"
        if visual_signals:
            visual_debug = ",".join(
                str(signal.get("change_state", "-"))
                for signal in visual_signals[:3]
            )
        debug_log(
            "Initiative",
            (
                f"trigger={trigger_kind} selected={selected_candidate_family or '-'} "
                f"preferred={selected_family_entry.preferred_result_kind if selected_family_entry is not None else '-'} "
                f"suppression={suppression_summary.get('suppression_level', '-')} "
                f"foreground={foreground_signal_summary.get('foreground_thinness', '-')} "
                f"visual={visual_debug}"
            ),
        )
        return InitiativeContext(
            trigger_kind=trigger_kind,
            opportunity_summary=self._initiative_opportunity_summary(
                trigger_kind=trigger_kind,
                client_context=client_context,
                selected_candidate=selected_candidate,
                initiative_entry_summary=initiative_entry_summary,
            ),
            initiative_entry_summary=initiative_entry_summary,
            time_context_summary=self._initiative_time_context_summary(time_context=time_context),
            foreground_signal_summary=foreground_signal_summary,
            activity_context=self._initiative_activity_context(activity_context),
            initiative_baseline=initiative_baseline,
            persona_context_summary=persona_context_summary,
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
            speech_frequency_level=state["background_wake_speech_frequency_level"],
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
            return self._merge_foreground_world_state_for_reuse(foreground_world_state, previous)
        return foreground_world_state or []

    def _initiative_opportunity_summary(
        self,
        *,
        trigger_kind: str,
        client_context: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
        initiative_entry_summary: dict[str, Any] | None,
    ) -> str:
        _ = trigger_kind, client_context
        if isinstance(selected_candidate, dict):
            return "自律判断の評価機会があり、保留候補をいま扱うか、保留を続けるか、見送るかを選ぶ。"
        if (
            isinstance(initiative_entry_summary, dict)
            and initiative_entry_summary.get("entry_kind") == "enter"
            and initiative_entry_summary.get("entry_basis") in INITIATIVE_ENTRY_ENTER_BASIS_VALUES
        ):
            return "自律判断の評価対象が前景化しており、関わる、保留する、見送るのどれが自然かを見直す。"
        return "自律判断の評価機会があり、関わる、保留する、見送るのどれが自然かを見直す。"

    def _initiative_entry_summary(self, client_context: dict[str, Any]) -> dict[str, Any] | None:
        entry_check = client_context.get("initiative_entry_check")
        if not isinstance(entry_check, dict):
            return None
        entry_kind = self._client_context_text(entry_check.get("entry_kind"), limit=24)
        entry_basis = self._client_context_text(entry_check.get("entry_basis"), limit=48)
        reason_summary = self._client_context_text(entry_check.get("reason_summary"), limit=180)
        if entry_kind not in {"enter", "skip"} or entry_basis is None or reason_summary is None:
            return None
        return {
            "entry_kind": entry_kind,
            "entry_basis": entry_basis,
            "reason_summary": reason_summary,
        }

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
            return "朝の立ち上がり帯で、軽い関わり、保留、見送りを比べたい時間帯。"
        if part_of_day == "daytime":
            return "日中の活動帯で、前景理由と控える理由を比べて選びやすい時間帯。"
        if part_of_day == "evening":
            return "夕方から夜への移行帯で、流れの整理、保留、見送りを比べたい時間帯。"
        return "夜間で、静かな見送り、保留、短い関わりを慎重に比べたい時間帯。"

    def _initiative_foreground_signal_summary(
        self,
        *,
        trigger_kind: str,
        client_context: dict[str, Any],
        world_state_summary: list[dict[str, Any]],
    ) -> dict[str, Any]:
        _ = trigger_kind
        visual_signals = self._compact_visual_observation_signals(
            client_context.get("visual_observation_signals")
        )
        state_types = sorted(
            {
                item.get("state_type")
                for item in world_state_summary
                if isinstance(item, dict) and isinstance(item.get("state_type"), str)
            }
        )
        if not world_state_summary:
            payload = {
                "foreground_thinness": "thin",
                "reason_summary": "前景 world_state がまだ薄く、視覚や周辺状況の追加観測が欲しい。",
                "world_state_count": 0,
            }
            if visual_signals:
                payload["visual_observations"] = visual_signals
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
        if visual_signals:
            payload["visual_observations"] = visual_signals
        return payload

    def _initiative_foreground_thinness(self, foreground_signal_summary: dict[str, Any] | None) -> str | None:
        if not isinstance(foreground_signal_summary, dict):
            return None
        return self._client_context_text(foreground_signal_summary.get("foreground_thinness"), limit=16)

    def _initiative_activity_context(self, activity_context: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(activity_context, dict):
            return None
        payload: dict[str, Any] = {}
        current_activity = self._initiative_activity_summary(activity_context.get("current_activity"))
        previous_activity = self._initiative_activity_summary(activity_context.get("previous_activity"))
        if current_activity:
            payload["current_activity"] = current_activity
        if previous_activity:
            payload["previous_activity"] = previous_activity
        return payload or None

    def _initiative_activity_summary(self, activity: Any) -> dict[str, Any]:
        if not isinstance(activity, dict):
            return {}
        payload: dict[str, Any] = {}
        for key, limit in (
            ("label", 120),
            ("actor", 32),
            ("target", 120),
            ("age_label", 40),
            ("ended_age_label", 40),
            ("reason_summary", 160),
        ):
            value = self._client_context_text(activity.get(key), limit=limit)
            if value is not None:
                payload[key] = value
        for key in ("confidence", "salience"):
            value = activity.get(key)
            if isinstance(value, (int, float)):
                payload[key] = round(max(0.0, min(float(value), 1.0)), 3)
        return payload

    def _initiative_suppression_level(self, suppression_summary: dict[str, Any] | None) -> str | None:
        if not isinstance(suppression_summary, dict):
            return None
        return self._client_context_text(suppression_summary.get("suppression_level"), limit=16)

    def _initiative_baseline_summary(self, persona: dict[str, Any]) -> dict[str, Any]:
        level = self._client_context_text(persona.get("initiative_baseline"), limit=16)
        if level is None:
            return {}
        if level == "low":
            summary_text = "自発介入は控えめ寄りで、前景理由が弱ければ見送る。"
        elif level == "high":
            summary_text = "自発介入は強めで、前景理由が揃うと関わる判断を取りやすい。"
        else:
            summary_text = "自発介入は中庸で、関わる、保留する、見送るを文脈で選ぶ。"
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
                    "text": text.strip(),
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
                if capability_id in {"vision.capture", "camera.ptz"}:
                    compact_sources = self._compact_vision_sources_for_decision(item.get("vision_sources"))
                    available_item["vision_sources"] = compact_sources
                    if capability_id == "vision.capture":
                        vision_sources = compact_sources
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
            source_owner = self._client_context_text(source.get("source_owner"), limit=32)
            if source_owner is not None:
                payload["source_owner"] = source_owner
            supported_operations = [
                value
                for value in source.get("supported_operations", [])
                if isinstance(value, str) and value.strip()
            ][:6]
            supported_amounts = [
                value
                for value in source.get("supported_amounts", [])
                if isinstance(value, str) and value.strip()
            ][:2]
            if supported_operations:
                payload["supported_operations"] = supported_operations
            if supported_amounts:
                payload["supported_amounts"] = supported_amounts
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

    def _initiative_visual_observation_signals(
        self,
        foreground_signal_summary: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not isinstance(foreground_signal_summary, dict):
            return []
        return self._compact_visual_observation_signals(
            foreground_signal_summary.get("visual_observations")
        )

    def _initiative_primary_visual_observation_signal(
        self,
        foreground_signal_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        signals = self._initiative_visual_observation_signals(foreground_signal_summary)
        for signal in signals:
            if self._visual_observation_signal_is_judgable(signal):
                return signal
        return signals[0] if signals else None

    def _initiative_intervention_risk_summary(
        self,
        *,
        initiative_baseline: dict[str, Any],
        intervention_state: dict[str, Any],
        ongoing_action_summary: dict[str, Any] | None,
        capability_summary: dict[str, Any],
    ) -> str | None:
        reasons: list[str] = []
        baseline_level = self._client_context_text(initiative_baseline.get("level"), limit=16)
        if baseline_level == "low":
            reasons.append("initiative_baseline が low で、押し出しは控えめにしたい。")
        if intervention_state.get("same_dedupe_recently_replied") is True:
            reasons.append("同じ pending_intent 系統には最近 speech 済みで、連続介入は避けたい。")
        if isinstance(ongoing_action_summary, dict) and ongoing_action_summary.get("status") == "waiting_result":
            reasons.append("ongoing_action が結果待ちで、重複介入は抑えたい。")
        if int(capability_summary.get("available_count", 0)) == 0:
            reasons.append("現時点で使える capability が見当たらない。")
        if not reasons:
            return None
        return " / ".join(reasons)

    def _initiative_suppression_summary(
        self,
        *,
        drive_summaries: list[dict[str, Any]],
        foreground_signal_summary: dict[str, Any],
        intervention_state: dict[str, Any],
        intervention_risk_summary: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        visual_repetition = self._initiative_visual_repetition_summary(foreground_signal_summary)
        suppression_level = "low"
        foreground_drives = self._initiative_foreground_drive_summaries(drive_summaries)
        if intervention_state.get("same_dedupe_recently_replied") is True:
            suppression_level = "high"
        elif visual_repetition.get("all_visual_observations_repeated") is True and not foreground_drives:
            suppression_level = "high"
        payload["suppression_level"] = suppression_level
        reason_parts: list[str] = []
        if intervention_risk_summary is not None:
            reason_parts.append(intervention_risk_summary)
        if visual_repetition.get("all_visual_observations_repeated") is True:
            reason_parts.append("視覚観測は既に触れた内容または安定状態だけで、反復主題化を控える材料がある。")
        elif visual_repetition.get("visual_repetition_present") is True:
            reason_parts.append("一部の視覚観測に反復性があり、前へ出る理由との競合材料として扱う。")
        if reason_parts:
            payload["reason_summary"] = " / ".join(reason_parts)
        for key in ("background_trigger", "same_dedupe_recently_replied"):
            value = intervention_state.get(key)
            if isinstance(value, bool):
                payload[key] = value
        payload.update(visual_repetition)
        return payload

    def _initiative_visual_repetition_summary(
        self,
        foreground_signal_summary: dict[str, Any],
    ) -> dict[str, Any]:
        visual_signals = self._initiative_visual_observation_signals(foreground_signal_summary)
        if not visual_signals:
            return {
                "visual_repetition_present": False,
                "same_as_recent_speech_present": False,
                "all_visual_observations_repeated": False,
                "visual_observation_count": 0,
                "repeated_visual_observation_count": 0,
            }
        repeated_count = 0
        same_as_recent_speech_present = False
        for signal in visual_signals:
            change_state = signal.get("change_state")
            if change_state in {"same_as_recent_speech", "stable"}:
                repeated_count += 1
            if change_state == "same_as_recent_speech" or signal.get("same_as_recent_speech") is True:
                same_as_recent_speech_present = True
        return {
            "visual_repetition_present": repeated_count > 0,
            "same_as_recent_speech_present": same_as_recent_speech_present,
            "all_visual_observations_repeated": repeated_count == len(visual_signals),
            "visual_observation_count": len(visual_signals),
            "repeated_visual_observation_count": repeated_count,
        }
