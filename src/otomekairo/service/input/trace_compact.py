from __future__ import annotations

from typing import Any

from otomekairo.capabilities import capability_manifests
from otomekairo.llm.contexts import InitiativeContext


class ServiceInputTraceCompactMixin:
    def _build_trigger_compact_summary(
        self,
        *,
        trigger_kind: str,
        input_text: str,
        observation_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
        followup_capability_request_summary: dict[str, Any] | None,
        decision: dict[str, Any] | None,
        result_kind: str,
        speech_payload: dict[str, Any] | None,
        pending_intent_summary: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None,
        initiative_context: InitiativeContext | None,
        ongoing_action_transition_summary: dict[str, Any] | None,
        failure_reason: str | None = None,
    ) -> dict[str, Any]:
        dispatch_request_summary = (
            followup_capability_request_summary
            if trigger_kind == "capability_result"
            else capability_request_summary
        )
        return {
            "trigger_kind": trigger_kind,
            "trigger_family": self._trigger_compact_family(trigger_kind),
            "current_input_summary": self._build_current_input(
                input_text=input_text,
                trigger_kind=trigger_kind,
                capability_request_summary=capability_request_summary,
            ).to_prompt_payload(),
            "entry_summary": self._build_trigger_compact_entry_summary(
                trigger_kind=trigger_kind,
                input_text=input_text,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
                pending_intent_selection=pending_intent_selection,
                initiative_context=initiative_context,
            ),
            "decision_summary": self._compact_capability_followup_decision_summary(decision),
            "result_summary": self._compact_trigger_result_summary(
                result_kind=result_kind,
                speech_payload=speech_payload,
                pending_intent_summary=pending_intent_summary,
                capability_request_summary=dispatch_request_summary,
                failure_reason=failure_reason,
            ),
            "transition_summary": self._compact_capability_followup_transition_summary(
                ongoing_action_transition_summary,
            ),
        }

    def _trigger_compact_family(self, trigger_kind: str) -> str:
        if trigger_kind == "capability_result":
            return "capability_result_followup"
        if trigger_kind in {"wake", "background_wake"}:
            return "initiative"
        if trigger_kind == "user_message":
            return "conversation"
        return "system"

    def _build_trigger_compact_entry_summary(
        self,
        *,
        trigger_kind: str,
        input_text: str,
        observation_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None,
        initiative_context: InitiativeContext | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        normalized_input = self._clamp(input_text.strip(), limit=160)
        if normalized_input is not None:
            payload["input_summary"] = normalized_input
        compact_observation_summary = self._compact_capability_followup_observation_summary(observation_summary)
        if trigger_kind == "capability_result":
            payload["source_request_summary"] = self._compact_capability_request_summary(capability_request_summary)
            payload["observation_summary"] = compact_observation_summary
            return payload
        if trigger_kind in {"wake", "background_wake"}:
            payload.update(
                self._compact_initiative_entry_summary(
                    initiative_context=initiative_context,
                    pending_intent_selection=pending_intent_selection,
                )
            )
            if isinstance(compact_observation_summary, dict):
                payload["observation_summary"] = compact_observation_summary
            return payload
        if isinstance(compact_observation_summary, dict):
            payload["observation_summary"] = compact_observation_summary
        return payload

    def _compact_initiative_entry_summary(
        self,
        *,
        initiative_context: InitiativeContext | None,
        pending_intent_selection: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return self._compact_initiative_context_summary(
            initiative_context=initiative_context,
            pending_intent_selection=pending_intent_selection,
        )

    def _compact_initiative_context_summary(
        self,
        *,
        initiative_context: InitiativeContext | None,
        pending_intent_selection: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        initiative_payload = initiative_context.to_prompt_payload() if initiative_context is not None else None
        if isinstance(initiative_payload, dict):
            trigger_kind = initiative_payload.get("trigger_kind")
            if isinstance(trigger_kind, str) and trigger_kind.strip():
                payload["trigger_kind"] = trigger_kind.strip()
            opportunity_summary = initiative_payload.get("opportunity_summary")
            if isinstance(opportunity_summary, str) and opportunity_summary.strip():
                payload["opportunity_summary"] = self._clamp(opportunity_summary.strip(), limit=160)
            initiative_entry_summary = initiative_payload.get("initiative_entry_summary")
            if isinstance(initiative_entry_summary, dict):
                compact_entry: dict[str, Any] = {}
                for key in ("entry_kind", "reason_summary"):
                    value = initiative_entry_summary.get(key)
                    if isinstance(value, str) and value.strip():
                        compact_entry[key] = self._clamp(value.strip(), limit=180)
                if compact_entry:
                    payload["initiative_entry_summary"] = compact_entry
            time_context_summary = initiative_payload.get("time_context_summary")
            if isinstance(time_context_summary, dict):
                compact_time_context: dict[str, Any] = {}
                for key in ("current_time_text", "part_of_day", "weekday", "time_band_summary"):
                    value = time_context_summary.get(key)
                    if isinstance(value, str) and value.strip():
                        compact_time_context[key] = self._clamp(value.strip(), limit=120)
                if compact_time_context:
                    payload["time_context_summary"] = compact_time_context
            foreground_signal_summary = initiative_payload.get("foreground_signal_summary")
            if isinstance(foreground_signal_summary, dict):
                compact_foreground_signal: dict[str, Any] = {}
                for key in ("foreground_thinness", "reason_summary"):
                    value = foreground_signal_summary.get(key)
                    if isinstance(value, str) and value.strip():
                        compact_foreground_signal[key] = self._clamp(value.strip(), limit=120)
                world_state_count = foreground_signal_summary.get("world_state_count")
                if isinstance(world_state_count, int):
                    compact_foreground_signal["world_state_count"] = world_state_count
                state_types = foreground_signal_summary.get("state_types")
                if isinstance(state_types, list):
                    compact_foreground_signal["state_types"] = [
                        value.strip()
                        for value in state_types
                        if isinstance(value, str) and value.strip()
                    ][:4]
                visual_signals = self._compact_visual_observation_signals(
                    foreground_signal_summary.get("visual_observations")
                )
                if visual_signals:
                    compact_foreground_signal["visual_observations"] = visual_signals
                if compact_foreground_signal:
                    payload["foreground_signal_summary"] = compact_foreground_signal
            selected_candidate_family = initiative_payload.get("selected_candidate_family")
            if isinstance(selected_candidate_family, str) and selected_candidate_family.strip():
                payload["selected_candidate_family"] = selected_candidate_family.strip()
            initiative_baseline = initiative_payload.get("initiative_baseline")
            if isinstance(initiative_baseline, dict):
                baseline_level = initiative_baseline.get("level")
                if isinstance(baseline_level, str) and baseline_level.strip():
                    payload["initiative_baseline"] = baseline_level.strip()
            compact_pending_intent_summaries = self._compact_initiative_pending_intent_summaries(
                initiative_payload.get("pending_intent_summaries")
            )
            if compact_pending_intent_summaries:
                payload["pending_intent_summaries"] = compact_pending_intent_summaries
            compact_candidate_families = self._compact_initiative_candidate_families(
                initiative_payload.get("candidate_families")
            )
            if compact_candidate_families:
                payload["candidate_families"] = compact_candidate_families
            runtime_state_summary = initiative_payload.get("runtime_state_summary")
            if isinstance(runtime_state_summary, dict):
                payload["runtime_state_summary"] = {
                    "wake_scheduler_active": runtime_state_summary.get("wake_scheduler_active"),
                    "ongoing_action_exists": runtime_state_summary.get("ongoing_action_exists"),
                    "pending_memory_job_count": runtime_state_summary.get("pending_memory_job_count"),
                }
            compact_drive_summaries = self._compact_initiative_drive_summaries(
                initiative_payload.get("drive_summaries")
            )
            if compact_drive_summaries:
                payload["drive_summaries"] = compact_drive_summaries
            compact_recent_turn_summary = self._compact_initiative_recent_turn_summary(
                initiative_payload.get("recent_turn_summary")
            )
            if compact_recent_turn_summary:
                payload["recent_turn_summary"] = compact_recent_turn_summary
            compact_world_state_summaries = self._compact_initiative_world_state_summaries(
                initiative_payload.get("world_state_summary")
            )
            if compact_world_state_summaries:
                payload["world_state_summaries"] = compact_world_state_summaries
            compact_intervention_state = self._compact_initiative_intervention_state(
                initiative_payload.get("intervention_state")
            )
            if compact_intervention_state:
                payload["intervention_state"] = compact_intervention_state
            suppression_summary = initiative_payload.get("suppression_summary")
            if isinstance(suppression_summary, dict):
                compact_suppression: dict[str, Any] = {}
                for key in (
                    "suppression_level",
                    "reason_summary",
                ):
                    value = suppression_summary.get(key)
                    if isinstance(value, str) and value.strip():
                        compact_suppression[key] = self._clamp(value.strip(), limit=160)
                for key in ("background_trigger", "same_dedupe_recently_replied"):
                    value = suppression_summary.get(key)
                    if isinstance(value, bool):
                        compact_suppression[key] = value
                if compact_suppression:
                    payload["suppression_summary"] = compact_suppression
            intervention_risk_summary = initiative_payload.get("intervention_risk_summary")
            if isinstance(intervention_risk_summary, str) and intervention_risk_summary.strip():
                payload["intervention_risk_summary"] = self._clamp(intervention_risk_summary.strip(), limit=160)
        compact_pending_intent_selection = self._compact_pending_intent_selection_summary(
            pending_intent_selection
        )
        if isinstance(compact_pending_intent_selection, dict):
            payload["pending_intent_selection_summary"] = compact_pending_intent_selection
        return payload

    def _compact_initiative_drive_summaries(self, summaries: Any) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        if not isinstance(summaries, list):
            return payload
        for summary in summaries[:2]:
            if not isinstance(summary, dict):
                continue
            item: dict[str, Any] = {}
            for key in ("drive_kind", "summary_text", "freshness_hint", "stability_hint"):
                value = summary.get(key)
                if not isinstance(value, str) or not value.strip():
                    continue
                item[key] = self._clamp(value.strip(), limit=160)
            salience = summary.get("salience")
            if isinstance(salience, (int, float)):
                item["salience"] = round(float(salience), 2)
            support_count = summary.get("support_count")
            if isinstance(support_count, int) and support_count > 0:
                item["support_count"] = support_count
            for key in ("support_strength", "scope_alignment", "signal_strength", "persona_alignment"):
                value = summary.get(key)
                if isinstance(value, (int, float)):
                    item[key] = round(max(0.0, min(float(value), 1.0)), 2)
            if item:
                payload.append(item)
        return payload

    def _compact_initiative_pending_intent_summaries(self, summaries: Any) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        if not isinstance(summaries, list):
            return payload
        for summary in summaries[:3]:
            if not isinstance(summary, dict):
                continue
            item: dict[str, Any] = {}
            for key in ("intent_kind", "intent_summary", "reason_summary"):
                value = summary.get(key)
                if not isinstance(value, str) or not value.strip():
                    continue
                item[key] = self._clamp(value.strip(), limit=160)
            if item:
                payload.append(item)
        return payload

    def _compact_initiative_candidate_families(self, candidate_families: Any) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        if not isinstance(candidate_families, list):
            return payload
        for family in candidate_families[:3]:
            if not isinstance(family, dict):
                continue
            item: dict[str, Any] = {}
            family_name = family.get("family")
            if isinstance(family_name, str) and family_name.strip():
                item["family"] = family_name.strip()
            for key in ("available", "selected"):
                value = family.get(key)
                if isinstance(value, bool):
                    item[key] = value
            reason_summary = family.get("reason_summary")
            if isinstance(reason_summary, str) and reason_summary.strip():
                item["reason_summary"] = self._clamp(reason_summary.strip(), limit=160)
            preferred_result_kind = family.get("preferred_result_kind")
            if isinstance(preferred_result_kind, str) and preferred_result_kind.strip():
                item["preferred_result_kind"] = preferred_result_kind.strip()
            preferred_result_reason_summary = family.get("preferred_result_reason_summary")
            if isinstance(preferred_result_reason_summary, str) and preferred_result_reason_summary.strip():
                item["preferred_result_reason_summary"] = self._clamp(
                    preferred_result_reason_summary.strip(),
                    limit=160,
                )
            preferred_capability_id = family.get("preferred_capability_id")
            if isinstance(preferred_capability_id, str) and preferred_capability_id.strip():
                item["preferred_capability_id"] = preferred_capability_id.strip()
            blocking_reason_summary = family.get("blocking_reason_summary")
            if isinstance(blocking_reason_summary, str) and blocking_reason_summary.strip():
                item["blocking_reason_summary"] = self._clamp(blocking_reason_summary.strip(), limit=160)
            preferred_capability_input = family.get("preferred_capability_input")
            if isinstance(preferred_capability_input, dict) and preferred_capability_input:
                item["preferred_capability_input"] = preferred_capability_input
            if item:
                payload.append(item)
        return payload

    def _compact_initiative_recent_turn_summary(self, recent_turn_summary: Any) -> list[dict[str, str]]:
        payload: list[dict[str, str]] = []
        if not isinstance(recent_turn_summary, list):
            return payload
        for item in recent_turn_summary[:2]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            text = item.get("text")
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

    def _compact_initiative_intervention_state(self, intervention_state: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if not isinstance(intervention_state, dict):
            return payload
        for key in ("background_trigger", "same_dedupe_recently_replied"):
            value = intervention_state.get(key)
            if isinstance(value, bool):
                payload[key] = value
        return payload

    def _compact_initiative_world_state_summaries(self, summaries: Any) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        if not isinstance(summaries, list):
            return payload
        for summary in summaries[:3]:
            if not isinstance(summary, dict):
                continue
            item: dict[str, Any] = {}
            state_type = summary.get("state_type")
            summary_text = summary.get("summary_text")
            if isinstance(state_type, str) and state_type.strip():
                item["state_type"] = state_type.strip()
            if isinstance(summary_text, str) and summary_text.strip():
                item["summary_text"] = self._clamp(summary_text.strip(), limit=160)
            if item:
                payload.append(item)
        return payload

    def _compact_wake_observations(self, observations: Any) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        if not isinstance(observations, list):
            return payload
        for observation in observations[:6]:
            if not isinstance(observation, dict):
                continue
            item: dict[str, Any] = {}
            for key in (
                "observation_id",
                "capability_id",
                "status",
                "vision_source_id",
                "source_kind",
                "source_label",
                "visual_summary_text",
                "reason_summary",
                "error",
                "request_id",
            ):
                value = observation.get(key)
                if isinstance(value, str) and value.strip():
                    item[key] = self._clamp(value.strip(), limit=160)
            image_count = observation.get("image_count")
            if isinstance(image_count, int):
                item["image_count"] = image_count
            capability_request_summary = observation.get("capability_request_summary")
            if isinstance(capability_request_summary, dict):
                item["capability_request_summary"] = self._compact_capability_request_summary(
                    capability_request_summary
                )
            visual_signal = self._compact_visual_observation_signal(
                observation.get("visual_observation_signal")
            )
            if visual_signal:
                item["visual_observation_signal"] = visual_signal
            if item:
                payload.append(item)
        return payload

    def _compact_pending_intent_selection_summary(
        self,
        pending_intent_selection: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(pending_intent_selection, dict):
            return None
        payload: dict[str, Any] = {}
        for key in (
            "candidate_pool_count",
            "eligible_candidate_count",
            "selected_candidate_ref",
            "selected_candidate_id",
            "result_status",
        ):
            value = pending_intent_selection.get(key)
            if value is None:
                continue
            payload[key] = value
        for key in ("selection_reason", "failure_reason"):
            value = pending_intent_selection.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            payload[key] = self._clamp(value.strip(), limit=160)
        if not payload:
            return None
        return payload

    def _build_capability_dispatch_summary(
        self,
        *,
        trigger_kind: str,
        capability_request_summary: dict[str, Any] | None,
        followup_capability_request_summary: dict[str, Any] | None,
        decision: dict[str, Any] | None,
        ongoing_action_transition_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        dispatch_request_summary = followup_capability_request_summary if trigger_kind == "capability_result" else capability_request_summary
        compact_request_summary = self._compact_capability_request_summary(dispatch_request_summary)
        if not isinstance(compact_request_summary, dict):
            return None
        capability_id = compact_request_summary.get("capability_id")
        if not isinstance(capability_id, str) or not capability_id.strip():
            return None
        payload: dict[str, Any] = {
            "capability_id": capability_id.strip(),
            "capability_kind": self._capability_followup_capability_kind(capability_id.strip()),
            "request_summary": compact_request_summary,
            "transition_summary": self._compact_capability_followup_transition_summary(
                ongoing_action_transition_summary,
            ),
        }
        decision_summary = self._compact_capability_dispatch_decision_summary(decision)
        if isinstance(decision_summary, dict):
            payload["decision_summary"] = decision_summary
        return payload

    def _build_capability_result_followup_summary(
        self,
        *,
        trigger_kind: str,
        observation_summary: dict[str, Any] | None,
        source_capability_request_summary: dict[str, Any] | None,
        followup_capability_request_summary: dict[str, Any] | None,
        decision: dict[str, Any] | None,
        result_kind: str,
        speech_payload: dict[str, Any] | None,
        pending_intent_summary: dict[str, Any] | None,
        ongoing_action_transition_summary: dict[str, Any] | None,
        failure_reason: str | None = None,
    ) -> dict[str, Any] | None:
        if trigger_kind != "capability_result":
            return None
        capability_id = self._capability_followup_capability_id(
            observation_summary=observation_summary,
            source_capability_request_summary=source_capability_request_summary,
            ongoing_action_transition_summary=ongoing_action_transition_summary,
        )
        if capability_id is None:
            return None
        payload: dict[str, Any] = {
            "capability_id": capability_id,
            "capability_kind": self._capability_followup_capability_kind(capability_id),
            "source_request_summary": self._compact_capability_request_summary(source_capability_request_summary),
            "observation_summary": self._compact_capability_followup_observation_summary(observation_summary),
            "decision_summary": self._compact_capability_followup_decision_summary(decision),
            "followup_result_summary": self._compact_capability_followup_result_summary(
                result_kind=result_kind,
                speech_payload=speech_payload,
                pending_intent_summary=pending_intent_summary,
                followup_capability_request_summary=followup_capability_request_summary,
                failure_reason=failure_reason,
            ),
            "transition_summary": self._compact_capability_followup_transition_summary(
                ongoing_action_transition_summary,
            ),
        }
        return payload

    def _capability_followup_capability_id(
        self,
        *,
        observation_summary: dict[str, Any] | None,
        source_capability_request_summary: dict[str, Any] | None,
        ongoing_action_transition_summary: dict[str, Any] | None,
    ) -> str | None:
        for value in (
            observation_summary.get("capability_id") if isinstance(observation_summary, dict) else None,
            source_capability_request_summary.get("capability_id")
            if isinstance(source_capability_request_summary, dict)
            else None,
            ongoing_action_transition_summary.get("last_capability_id")
            if isinstance(ongoing_action_transition_summary, dict)
            else None,
        ):
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _capability_followup_capability_kind(self, capability_id: str) -> str | None:
        manifest = capability_manifests().get(capability_id, {})
        capability_kind = manifest.get("kind")
        if isinstance(capability_kind, str) and capability_kind.strip():
            return capability_kind.strip()
        return None

    def _compact_capability_request_summary(self, summary: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(summary, dict):
            return None
        payload: dict[str, Any] = {}
        for key in ("request_id", "capability_id", "status", "timeout_ms"):
            value = summary.get(key)
            if value is None:
                continue
            payload[key] = value
        readiness_digest = summary.get("readiness_digest")
        if isinstance(readiness_digest, dict):
            payload["readiness_digest"] = readiness_digest
        if not payload:
            return None
        return payload

    def _compact_capability_dispatch_decision_summary(
        self,
        decision: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        compact = self._compact_capability_followup_decision_summary(decision)
        if not isinstance(compact, dict):
            return None
        if compact.get("kind") != "capability_request":
            return None
        return compact

    def _compact_capability_followup_observation_summary(
        self,
        observation_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(observation_summary, dict):
            return None
        payload: dict[str, Any] = {}
        for key, value in observation_summary.items():
            if value is None:
                continue
            if isinstance(value, str):
                normalized = value.strip()
                if not normalized:
                    continue
                payload[key] = self._clamp(normalized, limit=160)
                continue
            if isinstance(value, (int, float, bool)):
                payload[key] = value
                continue
            if key == "readiness_digest" and isinstance(value, dict):
                payload[key] = value
        if not payload:
            return None
        return payload

    def _compact_capability_followup_decision_summary(
        self,
        decision: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(decision, dict):
            return None
        payload: dict[str, Any] = {}
        for key in ("kind", "reason_code", "reason_summary"):
            value = decision.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            payload[key] = self._clamp(value.strip(), limit=160)
        if not payload:
            return None
        return payload

    def _compact_trigger_result_summary(
        self,
        *,
        result_kind: str,
        speech_payload: dict[str, Any] | None,
        pending_intent_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
        failure_reason: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "result_kind": result_kind,
        }
        if isinstance(speech_payload, dict) and isinstance(speech_payload.get("speech_text"), str):
            payload["speech_summary"] = self._clamp(speech_payload["speech_text"].strip(), limit=160)
        if isinstance(pending_intent_summary, dict):
            payload["pending_intent_summary"] = pending_intent_summary
        compact_capability_request = self._compact_capability_request_summary(capability_request_summary)
        if isinstance(compact_capability_request, dict):
            payload["capability_request_summary"] = compact_capability_request
        if isinstance(failure_reason, str) and failure_reason.strip():
            payload["internal_failure_summary"] = self._clamp(failure_reason.strip(), limit=160)
        return payload

    def _compact_capability_followup_result_summary(
        self,
        *,
        result_kind: str,
        speech_payload: dict[str, Any] | None,
        pending_intent_summary: dict[str, Any] | None,
        followup_capability_request_summary: dict[str, Any] | None,
        failure_reason: str | None,
    ) -> dict[str, Any]:
        payload = self._compact_trigger_result_summary(
            result_kind=result_kind,
            speech_payload=speech_payload,
            pending_intent_summary=pending_intent_summary,
            capability_request_summary=followup_capability_request_summary,
            failure_reason=failure_reason,
        )
        compact_followup_request = payload.pop("capability_request_summary", None)
        if isinstance(compact_followup_request, dict):
            payload["followup_capability_request_summary"] = compact_followup_request
        return payload

    def _compact_capability_followup_transition_summary(
        self,
        ongoing_action_transition_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(ongoing_action_transition_summary, dict):
            return None
        payload: dict[str, Any] = {}
        for key in (
            "transition_sequence",
            "transition_kind",
            "final_state",
            "reason_code",
            "reason_summary",
            "transition_source",
            "decision_kind",
            "result_error",
            "detail_summary",
        ):
            value = ongoing_action_transition_summary.get(key)
            if value is None:
                continue
            payload[key] = value
        if not payload:
            return None
        return payload
