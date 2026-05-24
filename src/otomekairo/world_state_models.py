from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WorldStateSourcePack:
    trigger_kind: str
    current_input_summary: str
    source_kind: str
    source_ref: str
    time_context: str
    client_context: dict[str, Any]
    allowed_state_types: tuple[str, ...] = field(default_factory=tuple)
    visual_context: dict[str, Any] | None = None
    external_service_context: dict[str, Any] | None = None
    body_context: dict[str, Any] | None = None
    device_context: dict[str, Any] | None = None
    schedule_context: dict[str, Any] | None = None
    social_context_context: dict[str, Any] | None = None
    environment_context: dict[str, Any] | None = None
    location_context: dict[str, Any] | None = None
    capability_result_summary: dict[str, Any] | None = None

    def context(self, context_key: str) -> dict[str, Any] | None:
        value = getattr(self, context_key, None)
        return value if isinstance(value, dict) and value else None

    def state_type_context(self, state_type: str) -> dict[str, Any] | None:
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
            "capability_result_summary",
        ):
            value = self.context(key)
            if value is not None:
                payload[key] = value
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
