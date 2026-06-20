from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from otomekairo.service.common import ServiceError


@dataclass(frozen=True, slots=True)
class CapabilityResultPayloadSpec:
    summary_field: str
    accepted_detail_label: str


SIMPLE_CAPABILITY_RESULT_PAYLOAD_SPECS = {
    "external.status": CapabilityResultPayloadSpec(
        summary_field="status_text",
        accepted_detail_label="status_chars",
    ),
    "device.status": CapabilityResultPayloadSpec(
        summary_field="device_state_summary",
        accepted_detail_label="device_summary_chars",
    ),
    "body.status": CapabilityResultPayloadSpec(
        summary_field="body_state_summary",
        accepted_detail_label="body_summary_chars",
    ),
    "environment.status": CapabilityResultPayloadSpec(
        summary_field="environment_summary",
        accepted_detail_label="environment_summary_chars",
    ),
    "location.status": CapabilityResultPayloadSpec(
        summary_field="location_summary",
        accepted_detail_label="location_summary_chars",
    ),
    "social.status": CapabilityResultPayloadSpec(
        summary_field="social_context_summary",
        accepted_detail_label="social_context_summary_chars",
    ),
}


class ServiceSpontaneousCapabilityPayloadMixin:
    def _normalize_capability_result_payload(
        self,
        *,
        capability_id: str,
        result_payload: dict[str, Any],
    ) -> dict[str, Any]:
        if capability_id == "vision.capture":
            images = result_payload.get("images", [])
            client_context = result_payload.get("client_context")
            error = result_payload.get("error")
            if client_context is not None and not isinstance(client_context, dict):
                raise ServiceError(400, "invalid_capability_result", "vision.capture result.client_context must be an object.")
            if error is not None and not isinstance(error, str):
                raise ServiceError(400, "invalid_capability_result", "vision.capture result.error must be a string or null.")
            normalized_images = self._normalize_vision_capture_result_images(images)
            return {
                "images": normalized_images,
                "client_context": client_context or {},
                "error": error.strip() if isinstance(error, str) and error.strip() else None,
            }
        spec = SIMPLE_CAPABILITY_RESULT_PAYLOAD_SPECS.get(capability_id)
        if spec is not None:
            return self._normalize_simple_capability_result_payload(
                capability_id=capability_id,
                result_payload=result_payload,
                spec=spec,
            )
        if capability_id == "schedule.status":
            schedule_summary = result_payload.get("schedule_summary")
            schedule_slots = result_payload.get("schedule_slots")
            client_context = result_payload.get("client_context")
            error = result_payload.get("error")
            if not isinstance(schedule_summary, str) or not schedule_summary.strip():
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "schedule.status result.schedule_summary must be a non-empty string.",
                )
            if not isinstance(schedule_slots, list):
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "schedule.status result.schedule_slots must be an array.",
                )
            if client_context is not None and not isinstance(client_context, dict):
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "schedule.status result.client_context must be an object.",
                )
            if error is not None and not isinstance(error, str):
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "schedule.status result.error must be a string or null.",
                )
            return {
                "schedule_summary": schedule_summary.strip(),
                "schedule_slots": self._normalize_capability_result_schedule_slots(schedule_slots),
                "client_context": client_context or {},
                "error": error.strip() if isinstance(error, str) and error.strip() else None,
            }
        payload = dict(result_payload)
        client_context = payload.get("client_context")
        if client_context is not None and not isinstance(client_context, dict):
            raise ServiceError(400, "invalid_capability_result", "result.client_context must be an object.")
        error = payload.get("error")
        if error is not None and not isinstance(error, str):
            raise ServiceError(400, "invalid_capability_result", "result.error must be a string or null.")
        if isinstance(client_context, dict):
            payload["client_context"] = client_context
        if "error" in payload:
            payload["error"] = error.strip() if isinstance(error, str) and error.strip() else None
        return payload

    def _normalize_simple_capability_result_payload(
        self,
        *,
        capability_id: str,
        result_payload: dict[str, Any],
        spec: CapabilityResultPayloadSpec,
    ) -> dict[str, Any]:
        summary_text = result_payload.get(spec.summary_field)
        client_context = result_payload.get("client_context")
        error = result_payload.get("error")
        if not isinstance(summary_text, str) or not summary_text.strip():
            raise ServiceError(
                400,
                "invalid_capability_result",
                f"{capability_id} result.{spec.summary_field} must be a non-empty string.",
            )
        if client_context is not None and not isinstance(client_context, dict):
            raise ServiceError(
                400,
                "invalid_capability_result",
                f"{capability_id} result.client_context must be an object.",
            )
        if error is not None and not isinstance(error, str):
            raise ServiceError(
                400,
                "invalid_capability_result",
                f"{capability_id} result.error must be a string or null.",
            )
        return {
            spec.summary_field: summary_text.strip(),
            "client_context": client_context or {},
            "error": error.strip() if isinstance(error, str) and error.strip() else None,
        }

    def _normalize_vision_capture_result_images(self, images: Any) -> list[str]:
        try:
            return self._normalize_visual_observation_images(images, allow_missing=False)
        except ServiceError as exc:
            if exc.error_code != "invalid_images":
                raise
            raise ServiceError(
                400,
                "invalid_capability_result",
                f"vision.capture result.{exc.message}",
            ) from exc

    def _normalize_capability_result_schedule_slots(self, raw_slots: list[Any]) -> list[dict[str, Any]]:
        normalized_slots: list[dict[str, Any]] = []
        seen_slot_keys: set[str] = set()
        for item in raw_slots:
            if not isinstance(item, dict):
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "schedule.status result.schedule_slots must contain objects.",
                )
            slot_key = item.get("slot_key")
            summary_text = item.get("summary_text")
            if not isinstance(slot_key, str) or not slot_key.strip():
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "schedule.status result.schedule_slots[].slot_key must be a non-empty string.",
                )
            if not isinstance(summary_text, str) or not summary_text.strip():
                raise ServiceError(
                    400,
                    "invalid_capability_result",
                    "schedule.status result.schedule_slots[].summary_text must be a non-empty string.",
                )
            normalized_slot_key = slot_key.strip()
            if normalized_slot_key in seen_slot_keys:
                continue
            seen_slot_keys.add(normalized_slot_key)
            slot_payload: dict[str, Any] = {
                "slot_key": normalized_slot_key,
                "summary_text": summary_text.strip(),
            }
            for key in ("not_before", "expires_at"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    slot_payload[key] = value.strip()
            normalized_slots.append(slot_payload)
        return normalized_slots[:4]

    def _capability_result_log_channel(self, capability_id: str) -> str:
        return "CapabilityResult"

    def _capability_result_accepted_detail(self, *, capability_id: str, result_payload: dict[str, Any]) -> str:
        if capability_id == "vision.capture":
            images = result_payload.get("images")
            image_count = len(images) if isinstance(images, list) else 0
            return f"images={image_count} error={bool(result_payload.get('error'))}"
        if capability_id == "camera.ptz":
            status = result_payload.get("status")
            operation = result_payload.get("operation")
            amount = result_payload.get("amount")
            return f"status={status} operation={operation} amount={amount} error={bool(result_payload.get('error'))}"
        spec = SIMPLE_CAPABILITY_RESULT_PAYLOAD_SPECS.get(capability_id)
        if spec is not None:
            summary_text = result_payload.get(spec.summary_field)
            summary_chars = len(summary_text) if isinstance(summary_text, str) else 0
            return f"{spec.accepted_detail_label}={summary_chars} error={bool(result_payload.get('error'))}"
        return f"result_keys={len(result_payload)} error={bool(result_payload.get('error'))}"

    def _capability_result_context_hook_name(self, capability_id: str) -> str | None:
        hook_name = self._capability_state_policy(capability_id).get("result_context_hook")
        if isinstance(hook_name, str) and hook_name.strip():
            return hook_name.strip()
        return None

    def _capability_followup_hint_hook_name(self, capability_id: str) -> str | None:
        hook_name = self._capability_state_policy(capability_id).get("followup_hint_hook")
        if isinstance(hook_name, str) and hook_name.strip():
            return hook_name.strip()
        return None

    def _capability_result_capability_id(self, capability_response: dict[str, Any]) -> str:
        capability_id = capability_response.get("capability_id")
        if isinstance(capability_id, str) and capability_id.strip():
            return capability_id.strip()
        request_record = capability_response.get("request_record")
        if isinstance(request_record, dict):
            capability_id = request_record.get("capability_id")
            if isinstance(capability_id, str) and capability_id.strip():
                return capability_id.strip()
        return "unknown_capability"

    def _capability_result_payload_image_count(self, capability_response: dict[str, Any]) -> int | None:
        images = capability_response.get("images")
        if not isinstance(images, list):
            return None
        return len(images)

    def _capability_result_status_text(self, capability_response: dict[str, Any]) -> str | None:
        status_text = capability_response.get("status_text")
        if not isinstance(status_text, str) or not status_text.strip():
            return None
        return status_text.strip()
