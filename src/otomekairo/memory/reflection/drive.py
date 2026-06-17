from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from otomekairo.llm.contexts import PersonaContext, build_persona_context
from otomekairo.memory.reflection.constants import (
    ACTIVE_MEMORY_STATUSES,
    DRIVE_CANDIDATE_FRESHNESS_WEIGHTS,
    DRIVE_COMMITMENT_STATE_WEIGHTS,
    DRIVE_FRESH_HOURS,
    DRIVE_FRESHNESS_SALIENCE_ADJUSTMENTS,
    DRIVE_KIND_EXPIRY_HOURS,
    DRIVE_MAX_ACTIVE,
    DRIVE_MAX_MIXED_PENALTY,
    DRIVE_MAX_SCOPE_SUPPORT_BONUS,
    DRIVE_MAX_SIGNAL_BONUS,
    DRIVE_MAX_SUPPORT_BONUS,
    DRIVE_MAX_SUPPORTING_EVENT_IDS,
    DRIVE_MAX_SUPPORTING_MEMORY_UNITS,
    DRIVE_MIN_SUMMARY_DRIVE_SALIENCE,
    DRIVE_MOOD_SIGNAL_HIGH,
    DRIVE_MOOD_SIGNAL_LOW,
    DRIVE_PERSONA_ALIGNMENT_BY_BASELINE,
    DRIVE_PERSONA_ALIGNMENT_SALIENCE_RANGE,
    DRIVE_RELATIONSHIP_SIGNAL_HIGH,
    DRIVE_RELATIONSHIP_SIGNAL_LOW,
    DRIVE_SCOPE_SALIENCE_BOOSTS,
    DRIVE_STALE_SUMMARY_SIGNAL_FLOOR,
    DRIVE_STALE_SUMMARY_SUPPORT_FLOOR,
    DRIVE_SUMMARY_MIN_SALIENCE,
    DRIVE_SUMMARY_STATUS_WEIGHTS,
    DRIVE_SUPPORT_SALIENCE_STEP,
    DRIVE_WARM_HOURS,
    DRIVE_WEAK_STABILITY_PENALTY,
)
from otomekairo.memory.utils import clamp_score, hours_since, local_datetime, optional_text, stable_json, timestamp_sort_key


class MemoryReflectionDriveMixin:
    def _empty_drive_state_update(self) -> dict[str, Any]:
        return {
            "result_status": "not_started",
            "active_drive_ids": [],
            "removed_drive_ids": [],
            "drive_summaries": [],
            "scope_supports": [],
        }

    def _refresh_drive_states(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        selected_persona: dict[str, Any],
        mood_state: dict[str, Any],
        affect_states: list[dict[str, Any]],
        scope_support_index: dict[tuple[str, str], dict[str, Any]],
        summary_update_index: dict[tuple[str, str], dict[str, Any]],
    ) -> dict[str, Any]:
        persona_context = build_persona_context(selected_persona, role="drive_state")
        existing_drive_states = self.store.list_drive_states(
            memory_set_id=memory_set_id,
            current_time=finished_at,
            limit=DRIVE_MAX_ACTIVE * 4,
        )
        source_units = self.store.list_memory_units_for_reflection(
            memory_set_id=memory_set_id,
            current_time=finished_at,
            statuses=list(ACTIVE_MEMORY_STATUSES),
            include_memory_types=["commitment", "summary"],
            limit=96,
        )
        drive_states = self._build_drive_states(
            memory_set_id=memory_set_id,
            finished_at=finished_at,
            source_units=source_units,
            persona_context=persona_context,
            mood_state=mood_state,
            affect_states=affect_states,
            scope_support_index=scope_support_index,
        )
        self.store.replace_drive_states(
            memory_set_id=memory_set_id,
            drive_states=drive_states,
        )

        existing_ids = {
            drive_state["drive_id"]
            for drive_state in existing_drive_states
            if isinstance(drive_state, dict) and isinstance(drive_state.get("drive_id"), str)
        }
        current_ids = {
            drive_state["drive_id"]
            for drive_state in drive_states
            if isinstance(drive_state, dict) and isinstance(drive_state.get("drive_id"), str)
        }
        result_status = "no_change"
        if self._drive_state_signature(existing_drive_states) != self._drive_state_signature(drive_states):
            result_status = "updated"

        return {
            "result_status": result_status,
            "active_drive_ids": [drive_state["drive_id"] for drive_state in drive_states],
            "removed_drive_ids": sorted(existing_ids - current_ids),
            "drive_summaries": self._drive_state_summaries(drive_states),
            "persona_context_summary": persona_context.to_summary_payload(),
            "scope_supports": self._build_drive_scope_support_summaries(
                drive_states=drive_states,
                scope_support_index=scope_support_index,
                summary_update_index=summary_update_index,
            ),
        }

    def _build_drive_states(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        source_units: list[dict[str, Any]],
        persona_context: PersonaContext,
        mood_state: dict[str, Any],
        affect_states: list[dict[str, Any]],
        scope_support_index: dict[tuple[str, str], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # commitment は継続単位ごと、summary は scope ごとに drive 候補を集約する。
        grouped_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
        group_order: list[str] = []
        seen_group_keys: set[str] = set()
        for unit in source_units:
            candidate = self._build_drive_candidate_from_memory_unit(
                finished_at=finished_at,
                unit=unit,
            )
            if candidate is None:
                continue
            group_key = candidate["group_key"]
            grouped_candidates[group_key].append(candidate)
            if group_key not in seen_group_keys:
                seen_group_keys.add(group_key)
                group_order.append(group_key)

        drive_states: list[dict[str, Any]] = []
        for group_key in group_order:
            drive_state = self._build_drive_state_from_candidates(
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                candidates=grouped_candidates[group_key],
                persona_context=persona_context,
                mood_state=mood_state,
                affect_states=affect_states,
                scope_support_index=scope_support_index,
            )
            if drive_state is None:
                continue
            drive_states.append(drive_state)

        drive_states.sort(
            key=lambda item: (
                float(item.get("salience", 0.0)),
                timestamp_sort_key(item.get("updated_at")),
                item.get("drive_id", ""),
            ),
            reverse=True,
        )
        return drive_states[:DRIVE_MAX_ACTIVE]

    def _build_drive_candidate_from_memory_unit(
        self,
        *,
        finished_at: str,
        unit: dict[str, Any],
    ) -> dict[str, Any] | None:
        memory_unit_id = unit.get("memory_unit_id")
        summary_text = str(unit.get("summary_text") or "").strip()
        scope_type = unit.get("scope_type")
        scope_key = unit.get("scope_key")
        memory_type = unit.get("memory_type")
        if not isinstance(memory_unit_id, str) or not memory_unit_id:
            return None
        if not summary_text:
            return None
        if not isinstance(scope_type, str) or not scope_type:
            return None
        if not isinstance(scope_key, str) or not scope_key:
            return None

        drive_kind = self._drive_kind_from_memory_unit(unit)
        if drive_kind is None:
            return None
        base_salience = self._drive_candidate_base_salience(
            drive_kind=drive_kind,
            unit=unit,
        )
        source_updated_at = self._drive_source_updated_at(unit=unit, finished_at=finished_at)
        supporting_event_ids = [
            event_id
            for event_id in unit.get("evidence_event_ids", [])
            if isinstance(event_id, str) and event_id
        ]
        group_key = self._drive_candidate_group_key(
            drive_kind=drive_kind,
            unit=unit,
        )
        return {
            "group_key": group_key,
            "drive_kind": drive_kind,
            "scope_type": scope_type,
            "scope_key": scope_key,
            "summary_text": summary_text,
            "salience": base_salience,
            "memory_unit_id": memory_unit_id,
            "memory_type": memory_type,
            "status": unit.get("status"),
            "commitment_state": unit.get("commitment_state"),
            "source_updated_at": source_updated_at,
            "supporting_event_ids": supporting_event_ids[:DRIVE_MAX_SUPPORTING_EVENT_IDS],
        }

    def _drive_expires_at(self, *, finished_at: str, hours: int) -> str:
        return (local_datetime(finished_at) + timedelta(hours=hours)).isoformat()

    def _build_drive_state_from_candidates(
        self,
        *,
        memory_set_id: str,
        finished_at: str,
        candidates: list[dict[str, Any]],
        persona_context: PersonaContext,
        mood_state: dict[str, Any],
        affect_states: list[dict[str, Any]],
        scope_support_index: dict[tuple[str, str], dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not candidates:
            return None

        ordered_candidates = sorted(
            candidates,
            key=lambda item: (
                float(item.get("salience", 0.0)),
                timestamp_sort_key(item.get("source_updated_at")),
                item.get("memory_unit_id", ""),
            ),
            reverse=True,
        )
        lead = ordered_candidates[0]
        drive_kind = lead["drive_kind"]
        focus_scope_type = lead["scope_type"]
        focus_scope_key = lead["scope_key"]
        scope_support = scope_support_index.get((focus_scope_type, focus_scope_key), {})

        supporting_memory_unit_ids: list[str] = []
        supporting_memory_types: list[str] = []
        supporting_event_ids: list[str] = []
        related_scope_refs: list[str] = []
        freshest_support_at = lead["source_updated_at"]
        for candidate in ordered_candidates:
            memory_unit_id = candidate.get("memory_unit_id")
            if isinstance(memory_unit_id, str) and memory_unit_id and memory_unit_id not in supporting_memory_unit_ids:
                supporting_memory_unit_ids.append(memory_unit_id)
            memory_type = candidate.get("memory_type")
            if isinstance(memory_type, str) and memory_type and memory_type not in supporting_memory_types:
                supporting_memory_types.append(memory_type)
            for event_id in candidate.get("supporting_event_ids", []):
                if event_id not in supporting_event_ids:
                    supporting_event_ids.append(event_id)
                if len(supporting_event_ids) >= DRIVE_MAX_SUPPORTING_EVENT_IDS:
                    break
            scope_ref = f"{candidate['scope_type']}:{candidate['scope_key']}"
            if scope_ref not in related_scope_refs:
                related_scope_refs.append(scope_ref)
            candidate_updated_at = candidate.get("source_updated_at")
            if timestamp_sort_key(candidate_updated_at) > timestamp_sort_key(freshest_support_at):
                freshest_support_at = candidate_updated_at

        support_count = len(ordered_candidates)
        scope_support_kinds = self._drive_scope_support_kinds(
            drive_kind=drive_kind,
            scope_support=scope_support,
        )
        freshness_hint = self._drive_freshness_hint(
            source_updated_at=freshest_support_at,
            finished_at=finished_at,
        )
        support_strength = round(
            self._drive_support_strength(
                candidates=ordered_candidates,
                finished_at=finished_at,
                scope_support_kinds=scope_support_kinds,
            ),
            3,
        )
        scope_alignment = round(
            self._drive_scope_alignment(
                focus_scope_type=focus_scope_type,
                focus_scope_key=focus_scope_key,
                candidates=ordered_candidates,
                scope_support=scope_support,
            ),
            3,
        )
        signal_strength = round(
            self._drive_signal_strength(
                drive_kind=drive_kind,
                focus_scope_type=focus_scope_type,
                focus_scope_key=focus_scope_key,
                mood_state=mood_state,
                affect_states=affect_states,
            ),
            3,
        )
        persona_alignment = round(
            self._drive_persona_alignment(
                drive_kind=drive_kind,
                persona_context=persona_context,
                scope_support_kinds=scope_support_kinds,
                supporting_memory_types=supporting_memory_types,
                support_count=support_count,
                support_strength=support_strength,
                scope_alignment=scope_alignment,
            ),
            3,
        )
        mixed_penalty = self._drive_mixed_penalty(
            candidates=ordered_candidates,
            finished_at=finished_at,
            freshness_hint=freshness_hint,
        )
        stability_hint = self._drive_stability_hint(
            freshness_hint=freshness_hint,
            support_strength=support_strength,
            signal_strength=signal_strength,
            mixed_penalty=mixed_penalty,
        )
        salience = clamp_score(
            float(lead.get("salience", 0.0))
            + min(DRIVE_MAX_SUPPORT_BONUS, DRIVE_SUPPORT_SALIENCE_STEP * max(0, support_count - 1) + support_strength * 0.06)
            + min(DRIVE_MAX_SCOPE_SUPPORT_BONUS, max(0.0, (scope_alignment - 0.5) * 0.08) + 0.02 * max(0, len(scope_support_kinds) - 1))
            + DRIVE_FRESHNESS_SALIENCE_ADJUSTMENTS.get(freshness_hint, 0.0)
            + min(DRIVE_MAX_SIGNAL_BONUS, signal_strength * DRIVE_MAX_SIGNAL_BONUS)
            + ((persona_alignment - 0.5) * DRIVE_PERSONA_ALIGNMENT_SALIENCE_RANGE)
            - mixed_penalty
            - self._drive_stability_penalty(stability_hint=stability_hint)
        )
        if self._should_skip_drive_state(
            lead=lead,
            salience=salience,
            freshness_hint=freshness_hint,
            support_strength=support_strength,
            signal_strength=signal_strength,
            stability_hint=stability_hint,
        ):
            return None
        expires_at = self._drive_expires_at(
            finished_at=finished_at,
            hours=self._drive_expiry_hours(
                drive_kind=drive_kind,
                lead=lead,
                freshness_hint=freshness_hint,
                stability_hint=stability_hint,
            ),
        )
        drive_signature = {
            "drive_kind": drive_kind,
            "focus_scope_type": focus_scope_type,
            "focus_scope_key": focus_scope_key,
            "supporting_memory_unit_ids": supporting_memory_unit_ids[:DRIVE_MAX_SUPPORTING_MEMORY_UNITS],
        }
        return {
            "drive_id": f"drive:{stable_json(drive_signature)[:20]}",
            "memory_set_id": memory_set_id,
            "drive_kind": drive_kind,
            "summary_text": lead["summary_text"],
            "salience": salience,
            "related_scope_refs": related_scope_refs,
            "supporting_memory_unit_ids": supporting_memory_unit_ids[:DRIVE_MAX_SUPPORTING_MEMORY_UNITS],
            "supporting_memory_types": supporting_memory_types,
            "supporting_evidence_event_ids": supporting_event_ids,
            "scope_support_kinds": scope_support_kinds,
            "focus_scope_type": focus_scope_type,
            "focus_scope_key": focus_scope_key,
            "support_count": support_count,
            "support_strength": support_strength,
            "scope_alignment": scope_alignment,
            "freshness_hint": freshness_hint,
            "signal_strength": signal_strength,
            "persona_alignment": persona_alignment,
            "stability_hint": stability_hint,
            "source_updated_at": freshest_support_at,
            "updated_at": finished_at,
            "expires_at": expires_at,
        }

    def _drive_kind_from_memory_unit(self, unit: dict[str, Any]) -> str | None:
        memory_type = unit.get("memory_type")
        scope_type = unit.get("scope_type")
        if memory_type == "commitment":
            if self._drive_commitment_is_transient_support(unit):
                return None
            commitment_state = unit.get("commitment_state")
            if commitment_state == "on_hold":
                return "resume_when_ready"
            if commitment_state in {"open", "waiting_confirmation"}:
                return "follow_through"
            return None
        if memory_type != "summary":
            return None
        if clamp_score(unit.get("salience")) < DRIVE_SUMMARY_MIN_SALIENCE:
            return None
        if scope_type == "relationship":
            return "relationship_attunement"
        if scope_type == "user":
            return "user_attention"
        if scope_type == "self":
            return "self_regulation"
        if scope_type == "topic":
            return "topic_continuation"
        return None

    def _drive_candidate_base_salience(
        self,
        *,
        drive_kind: str,
        unit: dict[str, Any],
    ) -> float:
        memory_type = unit.get("memory_type")
        base_salience = clamp_score(unit.get("salience"))
        if memory_type == "commitment":
            commitment_state = unit.get("commitment_state")
            if commitment_state == "waiting_confirmation":
                return clamp_score(base_salience + 0.18)
            if commitment_state == "on_hold":
                return clamp_score(base_salience + 0.08)
            return clamp_score(base_salience + 0.14)
        scope_type = unit.get("scope_type")
        return clamp_score(base_salience + DRIVE_SCOPE_SALIENCE_BOOSTS.get(scope_type, 0.0))

    def _drive_candidate_group_key(
        self,
        *,
        drive_kind: str,
        unit: dict[str, Any],
    ) -> str:
        if unit.get("memory_type") == "summary":
            return f"{drive_kind}:{unit['scope_type']}:{unit['scope_key']}"
        return f"{drive_kind}:{unit['memory_unit_id']}"

    def _drive_commitment_is_transient_support(self, unit: dict[str, Any]) -> bool:
        qualifiers = unit.get("qualifiers")
        if not isinstance(qualifiers, dict):
            qualifiers = {}
        predicate = unit.get("predicate")
        transient_predicate = predicate in {"provide_support", "support_posture", "waits_for", "watch_over", "stand_by"}
        if qualifiers.get("scope_duration") in {"turn", "session"}:
            return True
        if qualifiers.get("source") in {"assistant_response", "assistant_self_statement"}:
            return True
        if qualifiers.get("commitment_actor") in {"self", "assistant"}:
            return True
        if transient_predicate and qualifiers.get("source") == "inference":
            return True
        return False

    def _drive_source_updated_at(self, *, unit: dict[str, Any], finished_at: str) -> str:
        for key in ("last_confirmed_at", "formed_at"):
            value = unit.get(key)
            if isinstance(value, str) and value:
                return value
        return finished_at

    def _drive_freshness_hint(
        self,
        *,
        source_updated_at: str,
        finished_at: str,
    ) -> str:
        age_hours = hours_since(source_updated_at, finished_at)
        if age_hours <= DRIVE_FRESH_HOURS:
            return "fresh"
        if age_hours <= DRIVE_WARM_HOURS:
            return "warm"
        return "stale"

    def _drive_scope_support_kinds(
        self,
        *,
        drive_kind: str,
        scope_support: dict[str, Any],
    ) -> list[str]:
        support_kinds: list[str] = []
        if isinstance(scope_support, dict):
            for value in scope_support.get("support_kinds", []):
                if isinstance(value, str) and value and value not in support_kinds:
                    support_kinds.append(value)
        if "memory_units" not in support_kinds:
            support_kinds.append("memory_units")
        return support_kinds

    def _drive_candidate_weight(
        self,
        *,
        candidate: dict[str, Any],
        finished_at: str,
    ) -> float:
        freshness_hint = self._drive_freshness_hint(
            source_updated_at=candidate.get("source_updated_at") or finished_at,
            finished_at=finished_at,
        )
        freshness_weight = DRIVE_CANDIDATE_FRESHNESS_WEIGHTS.get(freshness_hint, 0.48)
        memory_type = candidate.get("memory_type")
        if memory_type == "commitment":
            state_weight = DRIVE_COMMITMENT_STATE_WEIGHTS.get(candidate.get("commitment_state"), 0.62)
        else:
            state_weight = DRIVE_SUMMARY_STATUS_WEIGHTS.get(candidate.get("status"), 0.8)
        return clamp_score(candidate.get("salience")) * freshness_weight * state_weight

    def _drive_support_strength(
        self,
        *,
        candidates: list[dict[str, Any]],
        finished_at: str,
        scope_support_kinds: list[str],
    ) -> float:
        if not candidates:
            return 0.0
        weighted_support = sum(
            self._drive_candidate_weight(candidate=candidate, finished_at=finished_at)
            for candidate in candidates
        )
        support_strength = clamp_score(weighted_support / 1.35)
        support_strength += 0.03 * max(0, len(scope_support_kinds) - 1)
        return clamp_score(support_strength)

    def _drive_scope_alignment(
        self,
        *,
        focus_scope_type: str,
        focus_scope_key: str,
        candidates: list[dict[str, Any]],
        scope_support: dict[str, Any],
    ) -> float:
        if not candidates:
            return 0.0
        aligned_count = sum(
            1
            for candidate in candidates
            if candidate.get("scope_type") == focus_scope_type and candidate.get("scope_key") == focus_scope_key
        )
        alignment = aligned_count / max(1, len(candidates))
        support_kinds = scope_support.get("support_kinds", []) if isinstance(scope_support, dict) else []
        if any(value in {"episodes", "memory_units"} for value in support_kinds if isinstance(value, str)):
            alignment += 0.18
        elif support_kinds:
            alignment += 0.08
        related_scope_refs = {
            f"{candidate.get('scope_type')}:{candidate.get('scope_key')}"
            for candidate in candidates
            if isinstance(candidate.get("scope_type"), str) and isinstance(candidate.get("scope_key"), str)
        }
        if len(related_scope_refs) > 1:
            alignment -= min(0.3, 0.15 * (len(related_scope_refs) - 1))
        return clamp_score(alignment)

    def _drive_signal_strength(
        self,
        *,
        drive_kind: str,
        focus_scope_type: str,
        focus_scope_key: str,
        mood_state: dict[str, Any],
        affect_states: list[dict[str, Any]],
    ) -> float:
        if drive_kind == "self_regulation":
            current_vad = mood_state.get("current_vad")
            if isinstance(current_vad, dict):
                mood_signal = max(
                    abs(float(current_vad.get("v", 0.0))),
                    abs(float(current_vad.get("a", 0.0))),
                    abs(float(current_vad.get("d", 0.0))),
                )
                confidence = clamp_score(mood_state.get("confidence"))
                return clamp_score(mood_signal * max(0.45, confidence))
            return 0.0

        if focus_scope_type not in {"relationship", "user"}:
            return 0.0
        affect_signal = 0.0
        for record in affect_states:
            if not isinstance(record, dict):
                continue
            if record.get("target_scope_type") != focus_scope_type:
                continue
            if record.get("target_scope_key") != focus_scope_key:
                continue
            affect_signal = max(
                affect_signal,
                clamp_score(record.get("intensity")) * clamp_score(record.get("confidence")),
            )
        if drive_kind == "relationship_attunement":
            if affect_signal >= DRIVE_RELATIONSHIP_SIGNAL_HIGH:
                return clamp_score(affect_signal)
            if affect_signal >= DRIVE_RELATIONSHIP_SIGNAL_LOW:
                return clamp_score(affect_signal * 0.9)
            return clamp_score(affect_signal * 0.7)
        if affect_signal >= DRIVE_MOOD_SIGNAL_HIGH:
            return clamp_score(affect_signal)
        if affect_signal >= DRIVE_MOOD_SIGNAL_LOW:
            return clamp_score(affect_signal * 0.85)
        return clamp_score(affect_signal * 0.6)

    def _drive_persona_alignment(
        self,
        *,
        drive_kind: str,
        persona_context: PersonaContext,
        scope_support_kinds: list[str],
        supporting_memory_types: list[str],
        support_count: int,
        support_strength: float,
        scope_alignment: float,
    ) -> float:
        baseline_payload = persona_context.initiative_baseline
        baseline = str(baseline_payload.get("level") or "medium").strip() if isinstance(baseline_payload, dict) else "medium"
        table = DRIVE_PERSONA_ALIGNMENT_BY_BASELINE.get(baseline, DRIVE_PERSONA_ALIGNMENT_BY_BASELINE["medium"])
        alignment = float(table.get(drive_kind, 0.5))
        if "persona_context" in scope_support_kinds:
            alignment += 0.04
        if "commitment" in supporting_memory_types and drive_kind in {"follow_through", "resume_when_ready"}:
            alignment += 0.04
        if "summary" in supporting_memory_types and drive_kind in {
            "relationship_attunement",
            "user_attention",
            "self_regulation",
            "topic_continuation",
        }:
            alignment += 0.02
        alignment += (scope_alignment - 0.5) * 0.08
        alignment += min(0.04, clamp_score(support_strength) * 0.04)
        if support_count >= 2:
            alignment += 0.02
        return clamp_score(alignment)

    def _drive_mixed_penalty(
        self,
        *,
        candidates: list[dict[str, Any]],
        finished_at: str,
        freshness_hint: str,
    ) -> float:
        if len(candidates) <= 1:
            return 0.0
        weighted_candidates = [
            self._drive_candidate_weight(candidate=candidate, finished_at=finished_at)
            for candidate in candidates
        ]
        lead_weight = weighted_candidates[0]
        second_weight = weighted_candidates[1] if len(weighted_candidates) > 1 else 0.0
        if lead_weight <= 0.0 or second_weight < lead_weight * 0.6:
            return 0.0
        variant_signatures = {
            (
                candidate.get("memory_type"),
                optional_text(candidate.get("summary_text")) or optional_text(candidate.get("commitment_state")) or "",
            )
            for candidate in candidates
        }
        if len(variant_signatures) <= 1:
            return 0.0
        total_weight = sum(weighted_candidates)
        lead_share = lead_weight / total_weight if total_weight > 0.0 else 1.0
        penalty = 0.05 + max(0.0, 0.72 - lead_share) * 0.35
        if freshness_hint == "stale":
            penalty += 0.04
        return min(DRIVE_MAX_MIXED_PENALTY, penalty)

    def _drive_stability_hint(
        self,
        *,
        freshness_hint: str,
        support_strength: float,
        signal_strength: float,
        mixed_penalty: float,
    ) -> str:
        if mixed_penalty >= 0.05:
            return "mixed"
        if freshness_hint == "stale" and support_strength < DRIVE_STALE_SUMMARY_SUPPORT_FLOOR and signal_strength < DRIVE_STALE_SUMMARY_SIGNAL_FLOOR:
            return "weak"
        return "stable"

    def _drive_stability_penalty(self, *, stability_hint: str) -> float:
        if stability_hint == "weak":
            return DRIVE_WEAK_STABILITY_PENALTY
        return 0.0

    def _should_skip_drive_state(
        self,
        *,
        lead: dict[str, Any],
        salience: float,
        freshness_hint: str,
        support_strength: float,
        signal_strength: float,
        stability_hint: str,
    ) -> bool:
        if lead.get("memory_type") != "summary":
            return False
        if salience >= DRIVE_MIN_SUMMARY_DRIVE_SALIENCE:
            return False
        if stability_hint != "weak":
            return False
        return freshness_hint == "stale" and support_strength < DRIVE_STALE_SUMMARY_SUPPORT_FLOOR and signal_strength < DRIVE_STALE_SUMMARY_SIGNAL_FLOOR

    def _drive_expiry_hours(
        self,
        *,
        drive_kind: str,
        lead: dict[str, Any],
        freshness_hint: str,
        stability_hint: str,
    ) -> int:
        base_hours = DRIVE_KIND_EXPIRY_HOURS.get(drive_kind, 48)
        if lead.get("memory_type") != "summary":
            return base_hours
        if stability_hint == "weak":
            return max(12, min(base_hours, 18))
        if stability_hint == "mixed":
            return max(18, min(base_hours, 24))
        if freshness_hint == "stale":
            return max(18, min(base_hours, 24))
        if freshness_hint == "warm":
            return max(18, min(base_hours, base_hours - 6))
        return base_hours

    def _drive_state_signature(self, drive_states: list[dict[str, Any]]) -> str:
        return stable_json(self._drive_state_summaries(drive_states))

    def _drive_state_summaries(self, drive_states: list[dict[str, Any]]) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for drive_state in drive_states:
            if not isinstance(drive_state, dict):
                continue
            summaries.append(
                {
                    "drive_id": drive_state.get("drive_id"),
                    "drive_kind": drive_state.get("drive_kind"),
                    "summary_text": drive_state.get("summary_text"),
                    "salience": drive_state.get("salience"),
                    "related_scope_refs": drive_state.get("related_scope_refs", []),
                    "supporting_memory_unit_ids": drive_state.get("supporting_memory_unit_ids", []),
                    "supporting_memory_types": drive_state.get("supporting_memory_types", []),
                    "scope_support_kinds": drive_state.get("scope_support_kinds", []),
                    "focus_scope_type": drive_state.get("focus_scope_type"),
                    "focus_scope_key": drive_state.get("focus_scope_key"),
                    "support_count": drive_state.get("support_count"),
                    "support_strength": drive_state.get("support_strength"),
                    "scope_alignment": drive_state.get("scope_alignment"),
                    "freshness_hint": drive_state.get("freshness_hint"),
                    "signal_strength": drive_state.get("signal_strength"),
                    "persona_alignment": drive_state.get("persona_alignment"),
                    "stability_hint": drive_state.get("stability_hint"),
                    "source_updated_at": drive_state.get("source_updated_at"),
                    "updated_at": drive_state.get("updated_at"),
                    "expires_at": drive_state.get("expires_at"),
                }
            )
        return summaries

    def _build_drive_scope_support_summaries(
        self,
        *,
        drive_states: list[dict[str, Any]],
        scope_support_index: dict[tuple[str, str], dict[str, Any]],
        summary_update_index: dict[tuple[str, str], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        tracked_scope_keys: set[tuple[str, str]] = set()
        drive_ids_by_scope: dict[tuple[str, str], list[str]] = defaultdict(list)
        for drive_state in drive_states:
            if not isinstance(drive_state, dict):
                continue
            scope_type = drive_state.get("focus_scope_type")
            scope_key = drive_state.get("focus_scope_key")
            drive_id = drive_state.get("drive_id")
            if not isinstance(scope_type, str) or not scope_type:
                continue
            if not isinstance(scope_key, str) or not scope_key:
                continue
            tracked_scope_keys.add((scope_type, scope_key))
            if isinstance(drive_id, str) and drive_id:
                drive_ids_by_scope[(scope_type, scope_key)].append(drive_id)
        tracked_scope_keys.update(summary_update_index.keys())

        summaries: list[dict[str, Any]] = []
        for scope_type, scope_key in sorted(tracked_scope_keys):
            scope_support = scope_support_index.get((scope_type, scope_key), {})
            summary_update = summary_update_index.get((scope_type, scope_key), {})
            support_kinds: list[str] = []
            if isinstance(scope_support, dict):
                for value in scope_support.get("support_kinds", []):
                    if isinstance(value, str) and value and value not in support_kinds:
                        support_kinds.append(value)
            if not support_kinds and drive_ids_by_scope.get((scope_type, scope_key)):
                support_kinds.append("memory_units")

            item: dict[str, Any] = {
                "scope_type": scope_type,
                "scope_key": scope_key,
                "support_kinds": support_kinds,
                "summary_updated": bool(summary_update.get("summary_updated")),
            }
            scope_label = scope_support.get("scope_label") if isinstance(scope_support, dict) else None
            if isinstance(scope_label, str) and scope_label:
                item["scope_label"] = scope_label
            if drive_ids_by_scope.get((scope_type, scope_key)):
                item["active_drive_ids"] = drive_ids_by_scope[(scope_type, scope_key)]
            operations = summary_update.get("operations")
            if isinstance(operations, list) and operations:
                item["summary_update_operations"] = [
                    value
                    for value in operations
                    if isinstance(value, str) and value
                ]
            summaries.append(item)
        return summaries
