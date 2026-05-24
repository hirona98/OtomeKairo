from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from otomekairo.service_input_constants import (
    WORLD_STATE_HINT_SCORES,
    WORLD_STATE_TTL_SECONDS_BY_TYPE,
    WORLD_STATE_USER_INPUT_CURRENT_STATE_TERMS_BY_TYPE,
    WORLD_STATE_USER_INPUT_REQUEST_TERMS,
)
from otomekairo.world_state_models import (
    WorldStateCandidate,
    WorldStateContext,
    WorldStateExternalServiceContext,
    WorldStateScheduleContext,
    WorldStatePendingIntent,
    WorldStateSourcePack,
    WorldStateVisualContext,
)


class ServiceInputWorldStateNormalizeMixin:
    def _normalize_world_state_candidates(
        self,
        *,
        memory_set_id: str,
        observed_at: str,
        source_kind: str,
        source_ref: str,
        payload: dict[str, Any],
        source_pack: WorldStateSourcePack,
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen_identity: set[tuple[str, str, str]] = set()
        allowed_state_types = set(source_pack.allowed_state_types)
        for candidate in self._world_state_candidates_from_payload(payload):
            state_type = candidate.state_type
            if state_type not in allowed_state_types:
                continue
            scope_type, scope_key = self._parse_world_state_scope(candidate.scope)
            identity = (state_type, scope_type, scope_key)
            if identity in seen_identity:
                continue
            seen_identity.add(identity)
            source_context = self._world_state_source_context(
                state_type=state_type,
                source_pack=source_pack,
            )
            if self._should_skip_user_input_current_state_candidate(
                state_type=state_type,
                source_kind=source_kind,
                source_context=source_context,
                source_pack=source_pack,
            ):
                continue
            if self._should_skip_system_wake_inferred_state_candidate(
                state_type=state_type,
                source_context=source_context,
                source_pack=source_pack,
            ):
                continue
            ttl_hint = candidate.ttl_hint
            ttl_policy = self._world_state_ttl_policy(
                current_time=observed_at,
                state_type=state_type,
                ttl_hint=ttl_hint,
                context=source_context,
            )
            integration_policy = self._world_state_integration_policy(
                state_type=state_type,
                scope_type=scope_type,
                scope_key=scope_key,
                context=source_context,
            )
            normalized.append(
                {
                    "world_state_id": f"world_state:{uuid.uuid4().hex}",
                    "memory_set_id": memory_set_id,
                    "state_type": state_type,
                    "scope_type": scope_type,
                    "scope_key": scope_key,
                    "summary_text": candidate.summary_text,
                    "source_kind": source_kind,
                    "source_ref": source_ref,
                    "confidence": self._world_state_score_from_hint(candidate.confidence_hint),
                    "salience": self._world_state_score_from_hint(candidate.salience_hint),
                    "observed_at": observed_at,
                    "expires_at": ttl_policy["expires_at"],
                    "updated_at": observed_at,
                    "summary_source": ttl_policy["summary_source"],
                    "ttl_hint": ttl_hint,
                    "ttl_seconds": ttl_policy["ttl_seconds"],
                    "integration_mode": integration_policy["mode"],
                    "integration_key": integration_policy["key"],
                }
            )
            if ttl_policy.get("capped_by") is not None:
                normalized[-1]["ttl_capped_by"] = ttl_policy["capped_by"]
        normalized.sort(key=lambda record: (record["salience"], record["updated_at"]), reverse=True)
        return normalized

    def _world_state_candidates_from_payload(self, payload: dict[str, Any]) -> list[WorldStateCandidate]:
        candidates: list[WorldStateCandidate] = []
        for raw_candidate in payload.get("state_candidates", []):
            candidate = WorldStateCandidate.from_payload(raw_candidate)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    def _should_skip_user_input_current_state_candidate(
        self,
        *,
        state_type: str,
        source_kind: str,
        source_context: WorldStateContext | None,
        source_pack: WorldStateSourcePack,
    ) -> bool:
        if source_kind != "user_input" or source_context is not None:
            return False
        state_terms = WORLD_STATE_USER_INPUT_CURRENT_STATE_TERMS_BY_TYPE.get(state_type)
        if not state_terms:
            return False
        current_input = source_pack.current_input_summary.strip()
        if not current_input:
            return False
        if not self._contains_any_text(current_input, WORLD_STATE_USER_INPUT_REQUEST_TERMS):
            return False
        return self._contains_any_text(current_input, state_terms)

    def _should_skip_system_wake_inferred_state_candidate(
        self,
        *,
        state_type: str,
        source_context: WorldStateContext | None,
        source_pack: WorldStateSourcePack,
    ) -> bool:
        if source_pack.trigger_kind not in {"wake", "background_wake"}:
            return False
        if source_context is not None:
            return False
        return state_type in {"visual_context", "body", "schedule", "social_context", "environment", "location"}

    def _contains_any_text(self, text: str, terms: tuple[str, ...]) -> bool:
        return any(term in text for term in terms)

    def _world_state_source_context(
        self,
        *,
        state_type: str,
        source_pack: WorldStateSourcePack,
    ) -> WorldStateContext | None:
        return source_pack.state_type_context(state_type)

    def _world_state_ttl_policy(
        self,
        *,
        current_time: str,
        state_type: str,
        ttl_hint: str,
        context: WorldStateContext | None,
    ) -> dict[str, Any]:
        summary_source = self._world_state_candidate_summary_source(
            state_type=state_type,
            context=context,
        )
        ttl_profiles = WORLD_STATE_TTL_SECONDS_BY_TYPE.get(state_type)
        if ttl_profiles is None:
            raise ValueError("world_state ttl is invalid.")
        ttl_table = ttl_profiles.get(summary_source) or ttl_profiles.get("summary_text")
        if ttl_table is None or ttl_hint not in ttl_table:
            raise ValueError("world_state ttl is invalid.")
        ttl_seconds = ttl_table[ttl_hint]
        ttl_capped_by = self._world_state_ttl_cap_source(
            current_time=current_time,
            state_type=state_type,
            context=context,
        )
        if ttl_capped_by is not None:
            ttl_seconds = min(
                ttl_seconds,
                self._world_state_capped_ttl_seconds(
                    current_time=current_time,
                    state_type=state_type,
                    context=context,
                ),
            )
        return {
            "summary_source": summary_source,
            "ttl_seconds": ttl_seconds,
            "expires_at": (self._parse_iso(current_time) + timedelta(seconds=ttl_seconds)).isoformat(),
            "capped_by": ttl_capped_by,
        }

    def _world_state_candidate_summary_source(
        self,
        *,
        state_type: str,
        context: WorldStateContext | None,
    ) -> str:
        if context is None:
            return "summary_text"
        if state_type in {
            "visual_context",
            "external_service",
            "body",
            "device",
            "schedule",
            "social_context",
            "environment",
            "location",
        }:
            return self._world_state_hook_summary_source(state_type=state_type, context=context)
        return "summary_text"

    def _normalize_world_state_schedule_slot_records(
        self,
        *,
        memory_set_id: str,
        observed_at: str,
        source_kind: str,
        source_ref: str,
        source_pack: WorldStateSourcePack,
    ) -> list[dict[str, Any]]:
        context = self._world_state_source_context(state_type="schedule", source_pack=source_pack)
        if not isinstance(context, WorldStateScheduleContext):
            return []
        if not context.schedule_slots:
            return []
        normalized_records: list[dict[str, Any]] = []
        seen_slot_keys: set[str] = set()
        for schedule_slot in context.schedule_slots:
            if schedule_slot.slot_key in seen_slot_keys:
                continue
            ttl_policy = self._world_state_schedule_slot_ttl_policy(
                current_time=observed_at,
                source_kind=source_kind,
                schedule_slot=schedule_slot,
            )
            if ttl_policy is None:
                continue
            seen_slot_keys.add(schedule_slot.slot_key)
            record: dict[str, Any] = {
                "world_state_id": f"world_state:{uuid.uuid4().hex}",
                "memory_set_id": memory_set_id,
                "state_type": "schedule",
                "scope_type": "self",
                "scope_key": "self",
                "summary_text": schedule_slot.summary_text,
                "source_kind": source_kind,
                "source_ref": source_ref,
                "confidence": self._world_state_score_from_hint("high"),
                "salience": self._world_state_score_from_hint("medium"),
                "observed_at": observed_at,
                "expires_at": ttl_policy["expires_at"],
                "updated_at": observed_at,
                "summary_source": ttl_policy["summary_source"],
                "ttl_hint": "medium",
                "ttl_seconds": ttl_policy["ttl_seconds"],
                "integration_mode": "schedule_slot",
                "integration_key": f"schedule:{schedule_slot.slot_key}",
                "slot_key": schedule_slot.slot_key,
            }
            if isinstance(schedule_slot.not_before, str) and schedule_slot.not_before.strip():
                record["slot_not_before"] = schedule_slot.not_before
            if isinstance(schedule_slot.expires_at, str) and schedule_slot.expires_at.strip():
                record["slot_expires_at"] = schedule_slot.expires_at
            if ttl_policy.get("capped_by") is not None:
                record["ttl_capped_by"] = ttl_policy["capped_by"]
            normalized_records.append(record)
        return normalized_records

    def _world_state_schedule_slot_ttl_policy(
        self,
        *,
        current_time: str,
        source_kind: str,
        schedule_slot: Any,
    ) -> dict[str, Any] | None:
        if isinstance(schedule_slot.summary_source, str) and schedule_slot.summary_source.strip():
            summary_source = schedule_slot.summary_source
        else:
            summary_source = self._world_state_client_context_summary_source(
                source_kind=source_kind,
                field_name="schedule_slots",
            )
        ttl_table = WORLD_STATE_TTL_SECONDS_BY_TYPE["schedule"].get(summary_source)
        if ttl_table is None:
            raise ValueError("world_state schedule slot ttl is invalid.")
        ttl_seconds = int(ttl_table["medium"])
        capped_by = None
        if isinstance(schedule_slot.expires_at, str) and schedule_slot.expires_at.strip():
            remaining_seconds = int(
                (self._parse_iso(schedule_slot.expires_at) - self._parse_iso(current_time)).total_seconds()
            )
            if remaining_seconds <= 0:
                return None
            ttl_seconds = min(ttl_seconds, max(1, remaining_seconds))
            capped_by = "schedule_slot.expires_at"
        return {
            "summary_source": summary_source,
            "ttl_seconds": ttl_seconds,
            "expires_at": (self._parse_iso(current_time) + timedelta(seconds=ttl_seconds)).isoformat(),
            "capped_by": capped_by,
        }

    def _world_state_ttl_cap_source(
        self,
        *,
        current_time: str,
        state_type: str,
        context: WorldStateContext | None,
    ) -> str | None:
        if state_type != "schedule" or not isinstance(context, WorldStateScheduleContext):
            return None
        if not isinstance(context.pending_intent, WorldStatePendingIntent):
            return None
        expires_at = context.pending_intent.expires_at
        if not isinstance(expires_at, str) or not expires_at.strip():
            return None
        remaining_seconds = int((self._parse_iso(expires_at.strip()) - self._parse_iso(current_time)).total_seconds())
        if remaining_seconds <= 0:
            return "pending_intent.expires_at"
        return "pending_intent.expires_at"

    def _world_state_capped_ttl_seconds(
        self,
        *,
        current_time: str,
        state_type: str,
        context: WorldStateContext | None,
    ) -> int:
        if state_type != "schedule" or not isinstance(context, WorldStateScheduleContext):
            raise ValueError("world_state ttl cap is invalid.")
        if not isinstance(context.pending_intent, WorldStatePendingIntent):
            raise ValueError("world_state ttl cap is invalid.")
        expires_at = context.pending_intent.expires_at
        if not isinstance(expires_at, str) or not expires_at.strip():
            raise ValueError("world_state ttl cap is invalid.")
        remaining_seconds = int((self._parse_iso(expires_at.strip()) - self._parse_iso(current_time)).total_seconds())
        return max(1, remaining_seconds)

    def _world_state_integration_policy(
        self,
        *,
        state_type: str,
        scope_type: str,
        scope_key: str,
        context: WorldStateContext | None,
    ) -> dict[str, str]:
        if state_type == "visual_context":
            vision_source_key = self._world_state_vision_source_key(context)
            if vision_source_key is None:
                raise ValueError("visual_context requires vision_source_id.")
            return {"mode": "vision_source", "key": f"visual_context:{vision_source_key}"}
        if state_type == "external_service":
            service_key = self._world_state_service_key(context)
            if service_key is not None:
                return {"mode": "external_service_service", "key": f"external_service:{service_key}"}
            return {"mode": "scope", "key": f"{state_type}:{scope_type}:{scope_key}"}
        if state_type == "body":
            return {"mode": "body_foreground", "key": "body:self"}
        if state_type == "device":
            return {"mode": "device_foreground", "key": "device:foreground"}
        if state_type == "schedule":
            schedule_slot_key = self._world_state_schedule_context_slot_key(context)
            if schedule_slot_key is not None:
                return {"mode": "schedule_slot", "key": f"schedule:{schedule_slot_key}"}
            return {"mode": "schedule_foreground", "key": "schedule:self"}
        return {"mode": "scope", "key": f"{state_type}:{scope_type}:{scope_key}"}

    def _world_state_vision_source_key(self, context: WorldStateContext | None) -> str | None:
        if not isinstance(context, WorldStateVisualContext):
            return None
        if not isinstance(context.vision_source_id, str) or not context.vision_source_id.strip():
            return None
        return context.vision_source_id

    def _world_state_service_key(self, context: WorldStateContext | None) -> str | None:
        if not isinstance(context, WorldStateExternalServiceContext):
            return None
        if not isinstance(context.service, str) or not context.service.strip():
            return None
        normalized = "".join(character if character.isalnum() else "_" for character in context.service.lower()).strip("_")
        return normalized or None

    def _world_state_schedule_context_slot_key(self, context: WorldStateContext | None) -> str | None:
        if not isinstance(context, WorldStateScheduleContext):
            return None
        return context.pending_intent_slot_key()

    def _world_state_schedule_slot_key(self, selected_candidate: dict[str, Any]) -> str | None:
        dedupe_key = self._client_context_text(selected_candidate.get("dedupe_key"), limit=160)
        if dedupe_key is not None:
            return dedupe_key
        not_before = selected_candidate.get("not_before")
        if isinstance(not_before, str) and not_before.strip():
            return f"at:{not_before.strip()}"
        intent_summary = self._client_context_text(selected_candidate.get("intent_summary"), limit=120)
        if intent_summary is not None:
            return f"summary:{intent_summary}"
        return None

    def _summarize_world_state_candidate_policies(
        self,
        world_states: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for world_state in world_states:
            if not isinstance(world_state, dict):
                continue
            scope_type = world_state.get("scope_type")
            scope_key = world_state.get("scope_key")
            if not isinstance(scope_type, str) or not isinstance(scope_key, str):
                continue
            summary = {
                "state_type": world_state.get("state_type"),
                "scope": self._world_state_scope_ref(scope_type=scope_type, scope_key=scope_key),
                "summary_source": world_state.get("summary_source"),
                "ttl_hint": world_state.get("ttl_hint"),
                "effective_ttl_seconds": world_state.get("ttl_seconds"),
                "integration_mode": world_state.get("integration_mode"),
                "integration_key": world_state.get("integration_key"),
            }
            ttl_capped_by = world_state.get("ttl_capped_by")
            if isinstance(ttl_capped_by, str) and ttl_capped_by.strip():
                summary["ttl_capped_by"] = ttl_capped_by.strip()
            summaries.append(summary)
        return summaries

    def _parse_world_state_scope(self, value: str) -> tuple[str, str]:
        if value in {"self", "user", "world"}:
            return value, value
        scope_type, separator, scope_key = value.partition(":")
        normalized_scope_key = scope_key.strip()
        if not separator or not normalized_scope_key:
            raise ValueError("world_state scope is invalid.")
        if scope_type == "entity":
            if not any(
                normalized_scope_key.startswith(prefix) and normalized_scope_key != prefix
                for prefix in ("person:", "place:", "tool:")
            ):
                raise ValueError("world_state entity scope is invalid.")
            return "entity", normalized_scope_key
        if scope_type == "topic":
            return "topic", value
        if scope_type == "relationship":
            refs = normalized_scope_key.split("|")
            if len(refs) < 2 or len(refs) != len(set(refs)):
                raise ValueError("world_state relationship scope is invalid.")
            if "self" in refs:
                expected_refs = ["self", *sorted(ref for ref in refs if ref != "self")]
            else:
                expected_refs = sorted(refs)
            if refs != expected_refs:
                raise ValueError("world_state relationship scope must be normalized.")
            return "relationship", normalized_scope_key
        raise ValueError("world_state scope_type is invalid.")

    def _world_state_score_from_hint(self, hint: Any) -> float:
        if not isinstance(hint, str) or hint.strip() not in WORLD_STATE_HINT_SCORES:
            raise ValueError("world_state hint score is invalid.")
        return WORLD_STATE_HINT_SCORES[hint.strip()]
