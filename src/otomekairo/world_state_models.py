from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeAlias


@dataclass(frozen=True, slots=True)
class WorldStateScheduleSlot:
    slot_key: str
    summary_text: str
    summary_source: str
    not_before: str | None = None
    expires_at: str | None = None

    def to_prompt_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "slot_key": self.slot_key,
            "summary_text": self.summary_text,
            "summary_source": self.summary_source,
        }
        if isinstance(self.not_before, str) and self.not_before.strip():
            payload["not_before"] = self.not_before
        if isinstance(self.expires_at, str) and self.expires_at.strip():
            payload["expires_at"] = self.expires_at
        return payload


@dataclass(frozen=True, slots=True)
class WorldStatePendingIntent:
    intent_kind: str | None = None
    intent_summary: str | None = None
    reason_summary: str | None = None
    not_before: str | None = None
    expires_at: str | None = None
    slot_key: str | None = None

    def to_prompt_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key, value in (
            ("intent_kind", self.intent_kind),
            ("intent_summary", self.intent_summary),
            ("reason_summary", self.reason_summary),
            ("not_before", self.not_before),
            ("expires_at", self.expires_at),
            ("slot_key", self.slot_key),
        ):
            if isinstance(value, str) and value.strip():
                payload[key] = value
        return payload


@dataclass(frozen=True, slots=True)
class WorldStateVisualContext:
    summary_text: str
    visual_summary_text: str
    image_interpreted: bool | None = None
    visual_confidence_hint: str | None = None
    image_count: int | None = None
    capability_id: str | None = None
    vision_source_id: str | None = None
    source_kind: str | None = None
    source_label: str | None = None

    def hook_summary_source(self) -> str:
        if isinstance(self.visual_summary_text, str) and self.visual_summary_text.strip():
            return "visual_summary_text"
        return "summary_text"

    def signal_fields(self) -> list[str]:
        fields: list[str] = []
        for key, value in (
            ("vision_source_id", self.vision_source_id),
            ("source_kind", self.source_kind),
            ("source_label", self.source_label),
            ("visual_summary_text", self.visual_summary_text),
            ("image_interpreted", self.image_interpreted),
            ("visual_confidence_hint", self.visual_confidence_hint),
            ("image_count", self.image_count),
        ):
            if isinstance(value, str) and value.strip():
                fields.append(key)
            elif isinstance(value, (int, bool)):
                fields.append(key)
        return fields

    def to_prompt_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "summary_text": self.summary_text,
            "visual_summary_text": self.visual_summary_text,
        }
        for key, value in (
            ("capability_id", self.capability_id),
            ("visual_confidence_hint", self.visual_confidence_hint),
            ("vision_source_id", self.vision_source_id),
            ("source_kind", self.source_kind),
            ("source_label", self.source_label),
        ):
            if isinstance(value, str) and value.strip():
                payload[key] = value
        if isinstance(self.image_interpreted, bool):
            payload["image_interpreted"] = self.image_interpreted
        if isinstance(self.image_count, int) and self.image_count >= 0:
            payload["image_count"] = self.image_count
        return payload


@dataclass(frozen=True, slots=True)
class WorldStateExternalServiceContext:
    summary_text: str
    external_service_summary: str | None = None
    client_summary_text: str | None = None
    result_summary_text: str | None = None
    status_text: str | None = None
    service: str | None = None
    summary_source_hint: str | None = None
    capability_id: str | None = None

    def hook_summary_source(self) -> str:
        if isinstance(self.summary_source_hint, str) and self.summary_source_hint.strip():
            return self.summary_source_hint
        if isinstance(self.status_text, str) and self.status_text.strip():
            return "status_text"
        return "external_service_summary"

    def signal_fields(self) -> list[str]:
        fields: list[str] = []
        for key, value in (
            ("service", self.service),
            ("status_text", self.status_text),
            ("external_service_summary", self.external_service_summary),
        ):
            if isinstance(value, str) and value.strip():
                fields.append(key)
        return fields

    def to_prompt_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "summary_text": self.summary_text,
        }
        for key, value in (
            ("external_service_summary", self.external_service_summary),
            ("client_summary_text", self.client_summary_text),
            ("result_summary_text", self.result_summary_text),
            ("status_text", self.status_text),
            ("service", self.service),
            ("summary_source_hint", self.summary_source_hint),
            ("capability_id", self.capability_id),
        ):
            if isinstance(value, str) and value.strip():
                payload[key] = value
        return payload


@dataclass(frozen=True, slots=True)
class WorldStateNamedSummaryContext:
    summary_text: str
    summary_field_name: str
    client_summary_text: str | None = None
    result_summary_text: str | None = None
    summary_source_hint: str | None = None
    capability_id: str | None = None

    def hook_summary_source(self) -> str:
        if isinstance(self.summary_source_hint, str) and self.summary_source_hint.strip():
            return self.summary_source_hint
        return self.summary_field_name

    def signal_fields(self) -> list[str]:
        return [self.summary_field_name]

    def to_prompt_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "summary_text": self.summary_text,
            self.summary_field_name: self.summary_text,
        }
        for key, value in (
            ("client_summary_text", self.client_summary_text),
            ("result_summary_text", self.result_summary_text),
            ("summary_source_hint", self.summary_source_hint),
            ("capability_id", self.capability_id),
        ):
            if isinstance(value, str) and value.strip():
                payload[key] = value
        return payload


@dataclass(frozen=True, slots=True)
class WorldStateScheduleContext:
    summary_text: str | None = None
    schedule_summary: str | None = None
    client_summary_text: str | None = None
    result_summary_text: str | None = None
    summary_source_hint: str | None = None
    capability_id: str | None = None
    schedule_slots: tuple[WorldStateScheduleSlot, ...] = field(default_factory=tuple)
    pending_intent: WorldStatePendingIntent | None = None

    def hook_summary_source(self) -> str:
        if isinstance(self.summary_source_hint, str) and self.summary_source_hint.strip():
            return self.summary_source_hint
        if isinstance(self.schedule_summary, str) and self.schedule_summary.strip():
            return "schedule_summary"
        if isinstance(self.pending_intent, WorldStatePendingIntent):
            return "pending_intent"
        return "summary_text"

    def signal_fields(self) -> list[str]:
        fields: list[str] = []
        if isinstance(self.schedule_summary, str) and self.schedule_summary.strip():
            fields.append("schedule_summary")
        if self.schedule_slots:
            fields.append("schedule_slots")
        if isinstance(self.pending_intent, WorldStatePendingIntent):
            fields.append("pending_intent")
        return fields

    def pending_intent_slot_key(self) -> str | None:
        if not isinstance(self.pending_intent, WorldStatePendingIntent):
            return None
        if not isinstance(self.pending_intent.slot_key, str) or not self.pending_intent.slot_key.strip():
            return None
        return self.pending_intent.slot_key

    def to_prompt_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if isinstance(self.summary_text, str) and self.summary_text.strip():
            payload["summary_text"] = self.summary_text
        for key, value in (
            ("schedule_summary", self.schedule_summary),
            ("client_summary_text", self.client_summary_text),
            ("result_summary_text", self.result_summary_text),
            ("summary_source_hint", self.summary_source_hint),
        ):
            if isinstance(value, str) and value.strip():
                payload[key] = value
        if isinstance(self.capability_id, str) and self.capability_id.strip():
            if isinstance(self.schedule_summary, str) and self.schedule_summary.strip():
                payload["capability_id"] = self.capability_id
        if self.schedule_slots:
            payload["schedule_slots"] = [slot.to_prompt_payload() for slot in self.schedule_slots]
        if isinstance(self.pending_intent, WorldStatePendingIntent):
            pending_intent_payload = self.pending_intent.to_prompt_payload()
            if pending_intent_payload:
                payload["pending_intent"] = pending_intent_payload
        return payload


WorldStateBodyContext: TypeAlias = WorldStateNamedSummaryContext
WorldStateDeviceContext: TypeAlias = WorldStateNamedSummaryContext
WorldStateSocialContext: TypeAlias = WorldStateNamedSummaryContext
WorldStateEnvironmentContext: TypeAlias = WorldStateNamedSummaryContext
WorldStateLocationContext: TypeAlias = WorldStateNamedSummaryContext
WorldStateContext: TypeAlias = (
    WorldStateVisualContext
    | WorldStateExternalServiceContext
    | WorldStateNamedSummaryContext
    | WorldStateScheduleContext
)


@dataclass(slots=True)
class WorldStateSourcePack:
    trigger_kind: str
    current_input_summary: str
    source_kind: str
    source_ref: str
    time_context: str
    client_context: dict[str, Any]
    allowed_state_types: tuple[str, ...] = field(default_factory=tuple)
    visual_context: WorldStateVisualContext | None = None
    external_service_context: WorldStateExternalServiceContext | None = None
    body_context: WorldStateBodyContext | None = None
    device_context: WorldStateDeviceContext | None = None
    schedule_context: WorldStateScheduleContext | None = None
    social_context_context: WorldStateSocialContext | None = None
    environment_context: WorldStateEnvironmentContext | None = None
    location_context: WorldStateLocationContext | None = None
    capability_result_summary: dict[str, Any] | None = None

    def context(self, context_key: str) -> WorldStateContext | None:
        value = getattr(self, context_key, None)
        return value if isinstance(value, _WORLD_STATE_CONTEXT_TYPES) else None

    def state_type_context(self, state_type: str) -> WorldStateContext | None:
        context_key = {
            "visual_context": "visual_context",
            "external_service": "external_service_context",
            "body": "body_context",
            "device": "device_context",
            "schedule": "schedule_context",
            "social_context": "social_context_context",
            "environment": "environment_context",
            "location": "location_context",
        }.get(state_type)
        if context_key is None:
            return None
        return self.context(context_key)

    def to_prompt_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "trigger_kind": self.trigger_kind,
            "current_input_summary": self.current_input_summary,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "time_context": self.time_context,
            "client_context": self.client_context,
            "allowed_state_types": list(self.allowed_state_types),
        }
        for key in (
            "visual_context",
            "external_service_context",
            "body_context",
            "device_context",
            "schedule_context",
            "social_context_context",
            "environment_context",
            "location_context",
        ):
            value = self.context(key)
            if value is not None:
                payload[key] = value.to_prompt_payload()
        if isinstance(self.capability_result_summary, dict) and self.capability_result_summary:
            payload["capability_result_summary"] = self.capability_result_summary
        return payload


_WORLD_STATE_CONTEXT_TYPES = (
    WorldStateVisualContext,
    WorldStateExternalServiceContext,
    WorldStateNamedSummaryContext,
    WorldStateScheduleContext,
)


@dataclass(frozen=True, slots=True)
class WorldStateTrace:
    result_status: str
    candidate_state_count: int
    input_world_state_count: int
    previous_foreground_world_state: list[dict[str, Any]]
    foreground_world_state: list[dict[str, Any]]
    updated_state_count: int
    replaced_state_count: int
    expired_state_count: int
    dropped_state_count: int
    source_kind: str | None
    source_ref: str | None
    source_pack_contexts: dict[str, Any]
    source_pack_state_type_hooks: dict[str, Any]
    normalized_candidate_policies: list[dict[str, Any]]
    failure_reason: str | None = None
    foreground_world_state_filter: dict[str, Any] | None = None
    stored_foreground_world_state: list[dict[str, Any]] | None = None

    @classmethod
    def not_requested(
        cls,
        *,
        source_kind: str | None,
        source_ref: str | None,
        foreground_world_state: list[dict[str, Any]],
    ) -> "WorldStateTrace":
        return cls(
            result_status="not_requested",
            candidate_state_count=0,
            input_world_state_count=len(foreground_world_state),
            previous_foreground_world_state=foreground_world_state,
            foreground_world_state=foreground_world_state,
            updated_state_count=0,
            replaced_state_count=0,
            expired_state_count=0,
            dropped_state_count=0,
            source_kind=source_kind,
            source_ref=source_ref,
            source_pack_contexts={},
            source_pack_state_type_hooks={},
            normalized_candidate_policies=[],
            failure_reason=None,
        )

    def to_trace_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "result_status": self.result_status,
            "candidate_state_count": self.candidate_state_count,
            "input_world_state_count": self.input_world_state_count,
            "previous_foreground_world_state": self.previous_foreground_world_state,
            "foreground_world_state": self.foreground_world_state,
            "updated_state_count": self.updated_state_count,
            "replaced_state_count": self.replaced_state_count,
            "expired_state_count": self.expired_state_count,
            "dropped_state_count": self.dropped_state_count,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "source_pack_contexts": self.source_pack_contexts,
            "source_pack_state_type_hooks": self.source_pack_state_type_hooks,
            "normalized_candidate_policies": self.normalized_candidate_policies,
            "failure_reason": self.failure_reason,
        }
        if isinstance(self.foreground_world_state_filter, dict) and self.foreground_world_state_filter:
            payload["foreground_world_state_filter"] = self.foreground_world_state_filter
        if isinstance(self.stored_foreground_world_state, list):
            payload["stored_foreground_world_state"] = self.stored_foreground_world_state
        return payload


@dataclass(frozen=True, slots=True)
class WorldStateCandidate:
    state_type: str
    scope: str
    summary_text: str
    confidence_hint: str
    salience_hint: str
    ttl_hint: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "WorldStateCandidate | None":
        if not isinstance(payload, dict):
            return None
        parts = []
        for key in (
            "state_type",
            "scope",
            "summary_text",
            "confidence_hint",
            "salience_hint",
            "ttl_hint",
        ):
            value = payload.get(key)
            if not isinstance(value, str) or not value.strip():
                return None
            parts.append(value.strip())
        return cls(*parts)
