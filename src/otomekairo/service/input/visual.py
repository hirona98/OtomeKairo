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

    def _interpret_visual_observation(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        trigger_kind: str,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        input_text: str,
        images: list[str],
        visual_observation_change_context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not images:
            return client_context, observation_summary

        # role/source pack
        selected_preset = state["model_presets"][state["selected_model_preset_id"]]
        interpretation_role = selected_preset["roles"]["input_interpretation"]
        persona_context = self._build_selected_persona_context(state=state, role="visual_observation")
        source_pack = self._build_visual_observation_source_pack(
            started_at=started_at,
            input_text=input_text,
            trigger_kind=trigger_kind,
            client_context=client_context,
            observation_summary=observation_summary,
            persona_context=persona_context,
            visual_observation_change_context=visual_observation_change_context,
        )

        # 実行
        try:
            payload = self.llm.generate_visual_observation_summary(
                role_definition=interpretation_role,
                persona_context=persona_context,
                source_pack=source_pack,
                images=images,
            )
        except (LLMError, KeyError, ValueError) as exc:
            observation_summary["image_interpretation_error"] = str(exc)
            raise

        # 反映
        visual_summary_text = str(payload["summary_text"]).strip()
        visual_confidence_hint = str(payload["confidence_hint"]).strip()
        change_state = str(payload["change_state"]).strip()
        change_basis = str(payload["change_basis"]).strip()
        change_reason_summary = str(payload["change_reason_summary"]).strip()
        enriched_client_context = {
            **client_context,
            "image_summary_text": visual_summary_text,
        }
        enriched_observation_summary = {
            **observation_summary,
            "visual_observation_id": f"visual_observation:{uuid.uuid4().hex}",
            "image_interpreted": True,
            "visual_summary_text": visual_summary_text,
            "visual_confidence_hint": visual_confidence_hint,
            "change_state": change_state,
            "change_basis": change_basis,
            "change_reason_summary": change_reason_summary,
        }
        capability_id = enriched_observation_summary.get("capability_id")
        readiness_digest = (
            capability_readiness_result_digest(capability_id, enriched_observation_summary)
            if isinstance(capability_id, str)
            else None
        )
        if isinstance(readiness_digest, dict):
            enriched_observation_summary["readiness_digest"] = readiness_digest
        return enriched_client_context, enriched_observation_summary

    def _build_visual_observation_source_pack(
        self,
        *,
        started_at: str,
        input_text: str,
        trigger_kind: str,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
        persona_context: Any,
        visual_observation_change_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "trigger_kind": trigger_kind,
            "persona_context": persona_context.to_prompt_payload(),
            "image_input_kind": self._visual_observation_input_kind(
                trigger_kind=trigger_kind,
                observation_summary=observation_summary,
            ),
            "time_context": llm_local_time_text(started_at).replace("\n", " / "),
            "client_context": self._build_visual_observation_client_context(
                trigger_kind=trigger_kind,
                client_context=client_context,
                observation_summary=observation_summary,
            ),
            "observation_summary": self._build_visual_observation_observation_summary(observation_summary),
            "current_input_summary": input_text.strip(),
        }
        if isinstance(visual_observation_change_context, dict) and visual_observation_change_context:
            payload["change_context"] = visual_observation_change_context
        return payload

    def _visual_observation_input_kind(
        self,
        *,
        trigger_kind: str,
        observation_summary: dict[str, Any],
    ) -> str:
        image_input_kind = observation_summary.get("image_input_kind")
        if isinstance(image_input_kind, str) and image_input_kind.strip():
            return image_input_kind.strip()
        if trigger_kind == "capability_result" and observation_summary.get("capability_id") == "vision.capture":
            return "vision_capture_result"
        return "conversation_attachment"

    def _build_visual_observation_client_context(
        self,
        *,
        trigger_kind: str,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        include_foreground_hint = (
            trigger_kind == "capability_result"
            and observation_summary.get("capability_id") == "vision.capture"
        )
        for key, limit in (
            ("source", 48),
            ("client_id", 80),
            ("vision_source_id", 96),
            ("source_kind", 32),
            ("source_label", 80),
            ("active_app", 80),
            ("window_title", 120),
            ("locale", 32),
        ):
            if key in {"vision_source_id", "source_kind", "source_label", "active_app", "window_title", "locale"} and not include_foreground_hint:
                continue
            value = client_context.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = value.strip()
        return payload

    def _build_visual_observation_observation_summary(
        self,
        observation_summary: dict[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key in (
            "source",
            "image_input_kind",
            "capability_id",
            "vision_source_id",
            "source_kind",
            "source_label",
            "image_count",
            "image_interpreted",
            "visual_confidence_hint",
            "error",
        ):
            value = observation_summary.get(key)
            if value is None:
                continue
            payload[key] = value
        return payload

    def _pipeline_augmented_query_text(
        self,
        *,
        input_text: str,
        trigger_kind: str,
        observation_summary: dict[str, Any] | None,
    ) -> str:
        if trigger_kind != "user_message":
            return input_text
        visual_summary_text = self._visual_observation_summary_text(observation_summary)
        if visual_summary_text is None:
            return input_text
        normalized_input_text = input_text.strip()
        if visual_summary_text in normalized_input_text:
            return input_text
        label = (
            "conversation_attachment_visual_summary"
            if self._observation_summary_is_conversation_attachment(observation_summary)
            else "visual_summary"
        )
        visual_input_summary = (
            f"<<<OTOMEKAIRO_INTERNAL_CONTEXT {label}>>>\n"
            f"{visual_summary_text}\n"
            "<<<END_OTOMEKAIRO_INTERNAL_CONTEXT>>>"
        )
        if not normalized_input_text:
            return visual_input_summary
        return f"{normalized_input_text}\n\n{visual_input_summary}"

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
        if source == "vision_capture_result":
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
