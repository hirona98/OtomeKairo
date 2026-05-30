from __future__ import annotations

from typing import Any


class ServiceInputWorldStateForegroundMixin:
    def _filter_foreground_world_state_for_capability_result(
        self,
        *,
        foreground_world_state: list[dict[str, Any]],
        trigger_kind: str,
        observation_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        target_vision_source_id = self._capability_result_target_vision_source_id(
            trigger_kind=trigger_kind,
            observation_summary=observation_summary,
            capability_request_summary=capability_request_summary,
        )
        if target_vision_source_id is None:
            return foreground_world_state, None

        target_integration_key = f"visual_context:{target_vision_source_id}"
        filtered_world_state: list[dict[str, Any]] = []
        dropped_visual_context_count = 0
        for summary in foreground_world_state:
            if not isinstance(summary, dict):
                continue
            if summary.get("state_type") == "visual_context":
                if summary.get("integration_key") != target_integration_key:
                    dropped_visual_context_count += 1
                    continue
            filtered_world_state.append(summary)

        return (
            filtered_world_state,
            {
                "mode": "vision_source",
                "capability_id": "vision.capture",
                "vision_source_id": target_vision_source_id,
                "integration_key": target_integration_key,
                "input_count": len(foreground_world_state),
                "output_count": len(filtered_world_state),
                "dropped_visual_context_count": dropped_visual_context_count,
            },
        )

    def _capability_result_target_vision_source_id(
        self,
        *,
        trigger_kind: str,
        observation_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
    ) -> str | None:
        if trigger_kind != "capability_result":
            return None
        capability_id = self._capability_result_source_capability_id(
            observation_summary=observation_summary,
            capability_request_summary=capability_request_summary,
        )
        if capability_id != "vision.capture":
            return None
        for source in (observation_summary, capability_request_summary):
            if not isinstance(source, dict):
                continue
            vision_source_id = source.get("vision_source_id")
            if not isinstance(vision_source_id, str) or not vision_source_id.strip():
                input_payload = source.get("input")
                if isinstance(input_payload, dict):
                    vision_source_id = input_payload.get("vision_source_id")
            if not isinstance(vision_source_id, str) or not vision_source_id.strip():
                continue
            normalized_vision_source_id = self._client_context_text(vision_source_id, limit=96)
            if normalized_vision_source_id is not None:
                return normalized_vision_source_id
        return None
