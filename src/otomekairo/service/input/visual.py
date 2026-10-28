from __future__ import annotations

import uuid
from typing import Any

from otomekairo.capabilities import capability_readiness_result_digest
from otomekairo.llm.client import LLMError
from otomekairo.memory.utils import llm_local_time_text
from otomekairo.service.common import ServiceError
from otomekairo.service.input.constants import (
    VISUAL_OBSERVATION_DATA_URI_PREFIX,
    VISUAL_OBSERVATION_IMAGE_LIMIT,
)
from otomekairo.service.input.source_owner import visual_source_owner


class ServiceInputVisualMixin:
    def _normalize_visual_observation_images(
        self,
        images: Any,
        *,
        allow_missing: bool,
    ) -> list[str]:
        if images is None and allow_missing:
            return []
        if not isinstance(images, list):
            raise ServiceError(400, "invalid_images", "images must be an array.")
        if len(images) > VISUAL_OBSERVATION_IMAGE_LIMIT:
            raise ServiceError(
                400,
                "invalid_images",
                f"images must contain at most {VISUAL_OBSERVATION_IMAGE_LIMIT} item.",
            )
        normalized_images: list[str] = []
        for image in images:
            if not isinstance(image, str) or not image.strip():
                raise ServiceError(400, "invalid_images", "images must contain non-empty strings.")
            normalized_image = image.strip()
            if not self._is_image_data_uri(normalized_image):
                raise ServiceError(400, "invalid_images", "images must contain image data URIs.")
            normalized_images.append(normalized_image)
        return normalized_images

    def _is_image_data_uri(self, value: str) -> bool:
        if not value.startswith(VISUAL_OBSERVATION_DATA_URI_PREFIX):
            return False
        header, separator, body = value.partition(",")
        if separator != "," or not body.strip():
            return False
        return ";base64" in header






    def _build_current_visual_observations(
        self, *, trigger_kind: str, observation_summary: dict[str, Any] | None,
        client_context: dict[str, Any], observed_at: str,
    ) -> list[dict[str, Any]]:
        sources = [(observation_summary, False)]
        sources.extend((item, True) for item in client_context.get("wake_observations", [])
                       if isinstance(item, dict) and item.get("status") == "succeeded")
        observations: list[dict[str, Any]] = []
        seen: set[str] = set()
        for summary, is_wake in sources:
            if not isinstance(summary, dict) or summary.get("image_interpreted") is not True:
                continue
            visual_id = summary.get("visual_observation_id")
            text = summary.get("visual_summary_text")
            if not isinstance(visual_id, str) or not visual_id.strip() or not isinstance(text, str) or not text.strip():
                raise ValueError("interpreted visual observation requires an ID and a detailed description")
            if visual_id in seen:
                continue
            if is_wake:
                context = {"source": "wake_observation", "image_interpreted": True,
                           "image_input_kind": summary["image_input_kind"], "visual_summary_text": text}
            else:
                context = self._build_visual_observation_decision_context(
                    trigger_kind=trigger_kind, observation_summary=summary,
                )
                if context is None:
                    raise ValueError("unsupported interpreted visual observation source")
            context.update({"visual_observation_id": visual_id, "observed_at": summary["observed_at"],
                            "visual_summary_text": text})
            for key in ("vision_source_id", "source_kind", "source_label", "image_count", "request_id",
                        "change_state", "change_basis", "change_reason_summary"):
                if key in summary:
                    context[key] = summary[key]
            if "visual_confidence_hint" in summary:
                context["confidence_hint"] = summary["visual_confidence_hint"]
            context.pop("visual_confidence_hint", None)
            owner = visual_source_owner(summary.get("source_kind"))
            if owner is not None:
                context["source_owner"] = owner
            observations.append(context)
            seen.add(visual_id)
        return observations

    def _pipeline_augmented_query_text(
        self, *, input_text: str, trigger_kind: str,
        observation_summary: dict[str, Any] | None,
        current_visual_observations: list[dict[str, Any]], current_input: Any = None,
        observations: Any = None,
    ) -> str:
        parts = [input_text]
        if trigger_kind == "capability_result" and getattr(current_input, "sender_kind", None) == "person":
            result = (observation_summary or {}).get("mcp_result_summary")
            if isinstance(result, str) and result.strip():
                parts.append("<<<OTOMEKAIRO_INTERNAL_CONTEXT capability_result>>>\n" + result
                             + "\n<<<END_OTOMEKAIRO_INTERNAL_CONTEXT>>>")
        for observation in current_visual_observations:
            parts.append("<<<OTOMEKAIRO_INTERNAL_CONTEXT current_visual_observation>>>\n"
                         + str(observation["source"]) + "\n" + observation["visual_summary_text"]
                         + "\n<<<END_OTOMEKAIRO_INTERNAL_CONTEXT>>>")
        if observations is not None:
            from otomekairo.observations.models import Image
            values = observations.to_prompt_payload()
            for item, observation in zip(values["surroundings"]["observations"] + values["input_materials"]["observations"], observations.observations, strict=True):
                if not isinstance(observation.payload, Image):
                    parts.append("<<<OTOMEKAIRO_INTERNAL_CONTEXT current_observation>>>\n"
                                 + observation.source_label + "\n" + item["interpretation"]["summary_text"]
                                 + "\n<<<END_OTOMEKAIRO_INTERNAL_CONTEXT>>>")
        return "\n\n".join(parts)

    def _observation_summary_is_conversation_attachment(self, observation_summary: dict[str, Any] | None) -> bool:
        if not isinstance(observation_summary, dict):
            return False
        return observation_summary.get("source") == "conversation_attachment"

    def _visual_observation_summary_text(self, observation_summary: dict[str, Any] | None) -> str | None:
        if not isinstance(observation_summary, dict):
            return None
        summary_text = observation_summary.get("visual_summary_text")
        if not isinstance(summary_text, str) or not summary_text.strip():
            return None
        return summary_text.strip()

    def _build_visual_observation_decision_context(
        self,
        *,
        trigger_kind: str,
        observation_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(observation_summary, dict):
            return None
        summary_text = self._visual_observation_summary_text(observation_summary)
        if summary_text is None:
            return None

        if trigger_kind == "user_message" and observation_summary.get("source") == "conversation_attachment":
            source = "conversation_attachment"
            image_input_kind = "conversation_attachment"
        elif self._observation_summary_is_vision_capture(observation_summary):
            source = "vision_capture_result"
            image_input_kind = "vision_capture_result"
        elif observation_summary.get("source") == "wake_reference":
            source = "wake_reference"
            image_input_kind = "wake_reference"
        else:
            return None

        payload: dict[str, Any] = {
            "source": source,
            "image_input_kind": image_input_kind,
            "image_interpreted": observation_summary.get("image_interpreted") is True,
            "visual_summary_text": summary_text,
        }
        for key in ("image_count", "visual_confidence_hint", "vision_source_id", "source_kind", "source_label"):
            value = observation_summary.get(key)
            if value is not None:
                payload[key] = value
        for key in ("change_state", "change_basis", "change_reason_summary"):
            value = observation_summary.get(key)
            if value is not None:
                payload[key] = value
        source_owner = visual_source_owner(payload.get("source_kind"))
        if source_owner is not None:
            payload["source_owner"] = source_owner
        if source in {"vision_capture_result", "wake_reference"}:
            payload["retention_policy"] = "visual_record_candidate"
        return payload

    def _observation_summary_is_vision_capture(
        self,
        observation_summary: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(observation_summary, dict):
            return False
        return (
            observation_summary.get("source") == "capability_result"
            and observation_summary.get("capability_id") == "vision.capture"
        )
