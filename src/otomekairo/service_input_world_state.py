from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from otomekairo.llm import LLMError
from otomekairo.memory_utils import llm_local_time_text
from otomekairo.service_input_constants import (
    WORLD_STATE_CONTEXT_KEYS_BY_TYPE,
    WORLD_STATE_FOREGROUND_LIMIT,
    WORLD_STATE_HINT_SCORES,
    WORLD_STATE_MAX_ACTIVE,
    WORLD_STATE_TTL_SECONDS_BY_TYPE,
    WORLD_STATE_USER_INPUT_CURRENT_STATE_TERMS_BY_TYPE,
    WORLD_STATE_USER_INPUT_REQUEST_TERMS,
)


class ServiceInputWorldStateMixin:
    def _refresh_world_state_context(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        input_text: str,
        trigger_kind: str,
        client_context: dict[str, Any],
        cycle_id: str | None,
        selected_candidate: dict[str, Any] | None,
        observation_summary: dict[str, Any] | None,
        capability_request_summary: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        previous_foreground_world_state = (
            self._summarize_foreground_world_states(
                self._list_current_world_states(
                    state=state,
                    current_time=started_at,
                    limit=WORLD_STATE_FOREGROUND_LIMIT,
                ),
                current_time=started_at,
            )
            or []
        )
        source_kind = self._world_state_source_kind(trigger_kind)
        source_ref = self._world_state_source_ref(
            cycle_id=cycle_id,
            trigger_kind=trigger_kind,
            started_at=started_at,
            capability_request_summary=capability_request_summary,
        )
        source_pack_contexts: dict[str, Any] = {}
        source_pack_state_type_hooks: dict[str, Any] = {}
        try:
            source_pack = self._build_world_state_source_pack(
                started_at=started_at,
                input_text=input_text,
                trigger_kind=trigger_kind,
                client_context=client_context,
                source_kind=source_kind,
                source_ref=source_ref,
                selected_candidate=selected_candidate,
                observation_summary=observation_summary,
            )
            source_pack_contexts = self._summarize_world_state_source_pack_contexts(source_pack)
            source_pack_state_type_hooks = self._summarize_world_state_state_type_hooks(source_pack)
            role_definition = state["model_presets"][state["selected_model_preset_id"]]["roles"]["input_interpretation"]
            payload = self.llm.generate_world_state(
                role_definition=role_definition,
                source_pack=source_pack,
            )
            world_states = self._normalize_world_state_candidates(
                memory_set_id=state["selected_memory_set_id"],
                observed_at=started_at,
                source_kind=source_kind,
                source_ref=source_ref,
                payload=payload,
                source_pack=source_pack,
            )
            world_states.extend(
                self._normalize_world_state_schedule_slot_records(
                    memory_set_id=state["selected_memory_set_id"],
                    observed_at=started_at,
                    source_kind=source_kind,
                    source_ref=source_ref,
                    source_pack=source_pack,
                )
            )
            normalized_candidate_policies = self._summarize_world_state_candidate_policies(world_states)
            refresh_summary = self.store.refresh_world_states(
                memory_set_id=state["selected_memory_set_id"],
                current_time=started_at,
                world_states=world_states,
                max_active=WORLD_STATE_MAX_ACTIVE,
            )
            foreground_world_state = (
                self._summarize_foreground_world_states(
                    self._list_current_world_states(
                        state=state,
                        current_time=started_at,
                        limit=WORLD_STATE_FOREGROUND_LIMIT,
                    ),
                    current_time=started_at,
                )
                or []
            )
            visible_foreground_world_state, foreground_visibility_filter = (
                self._filter_foreground_world_state_for_capability_result(
                    foreground_world_state=foreground_world_state,
                    trigger_kind=trigger_kind,
                    observation_summary=observation_summary,
                    capability_request_summary=capability_request_summary,
                )
            )
            world_state_trace = {
                "result_status": "succeeded",
                "candidate_state_count": len(payload.get("state_candidates", [])),
                "input_world_state_count": len(visible_foreground_world_state),
                "previous_foreground_world_state": previous_foreground_world_state,
                "foreground_world_state": visible_foreground_world_state,
                "updated_state_count": int(refresh_summary.get("updated_count", 0)),
                "replaced_state_count": int(refresh_summary.get("replaced_count", 0)),
                "expired_state_count": int(refresh_summary.get("expired_count", 0)),
                "dropped_state_count": int(refresh_summary.get("dropped_count", 0)),
                "source_kind": source_kind,
                "source_ref": source_ref,
                "source_pack_contexts": source_pack_contexts,
                "source_pack_state_type_hooks": source_pack_state_type_hooks,
                "normalized_candidate_policies": normalized_candidate_policies,
                "failure_reason": None,
            }
            if foreground_visibility_filter is not None:
                world_state_trace["foreground_world_state_filter"] = foreground_visibility_filter
                world_state_trace["stored_foreground_world_state"] = foreground_world_state
            return (
                world_state_trace,
                visible_foreground_world_state,
            )
        except (LLMError, KeyError, TypeError, ValueError) as exc:
            visible_foreground_world_state, foreground_visibility_filter = (
                self._filter_foreground_world_state_for_capability_result(
                    foreground_world_state=previous_foreground_world_state,
                    trigger_kind=trigger_kind,
                    observation_summary=observation_summary,
                    capability_request_summary=capability_request_summary,
                )
            )
            world_state_trace = {
                "result_status": "failed",
                "candidate_state_count": 0,
                "input_world_state_count": len(visible_foreground_world_state),
                "previous_foreground_world_state": previous_foreground_world_state,
                "foreground_world_state": visible_foreground_world_state,
                "updated_state_count": 0,
                "replaced_state_count": 0,
                "expired_state_count": 0,
                "dropped_state_count": 0,
                "source_kind": source_kind,
                "source_ref": source_ref,
                "source_pack_contexts": source_pack_contexts,
                "source_pack_state_type_hooks": source_pack_state_type_hooks,
                "normalized_candidate_policies": [],
                "failure_reason": str(exc),
            }
            if foreground_visibility_filter is not None:
                world_state_trace["foreground_world_state_filter"] = foreground_visibility_filter
                world_state_trace["stored_foreground_world_state"] = previous_foreground_world_state
            return (
                world_state_trace,
                visible_foreground_world_state,
            )

    def _build_world_state_source_pack(
        self,
        *,
        started_at: str,
        input_text: str,
        trigger_kind: str,
        client_context: dict[str, Any],
        source_kind: str,
        source_ref: str,
        selected_candidate: dict[str, Any] | None,
        observation_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "trigger_kind": trigger_kind,
            "current_input_summary": self._clamp(input_text.strip(), limit=200) or "",
            "source_kind": source_kind,
            "source_ref": source_ref,
            "time_context": llm_local_time_text(started_at).replace("\n", " / "),
            "client_context": self._build_world_state_client_context(client_context),
        }
        visual_context = self._build_world_state_visual_context(
            observation_summary=observation_summary,
        )
        if visual_context is not None:
            payload["visual_context"] = visual_context
        for key, value in (
            (
                "external_service_context",
                self._build_world_state_external_service_context(
                    client_context=client_context,
                    observation_summary=observation_summary,
                    source_kind=source_kind,
                ),
            ),
            (
                "body_context",
                self._build_world_state_body_context(
                    client_context=client_context,
                    observation_summary=observation_summary,
                    source_kind=source_kind,
                ),
            ),
            (
                "device_context",
                self._build_world_state_device_context(
                    client_context=client_context,
                    observation_summary=observation_summary,
                    source_kind=source_kind,
                ),
            ),
            (
                "schedule_context",
                self._build_world_state_schedule_context(
                    client_context=client_context,
                    observation_summary=observation_summary,
                    source_kind=source_kind,
                    selected_candidate=selected_candidate,
                ),
            ),
            (
                "social_context_context",
                self._build_world_state_social_context_context(
                    client_context=client_context,
                    observation_summary=observation_summary,
                    source_kind=source_kind,
                ),
            ),
            (
                "environment_context",
                self._build_world_state_environment_context(
                    client_context=client_context,
                    observation_summary=observation_summary,
                    source_kind=source_kind,
                ),
            ),
            (
                "location_context",
                self._build_world_state_location_context(
                    client_context=client_context,
                    observation_summary=observation_summary,
                    source_kind=source_kind,
                ),
            ),
        ):
            if value is not None:
                payload[key] = value
        if source_kind == "capability_result":
            capability_result_summary = self._build_world_state_capability_result_summary(observation_summary)
            if capability_result_summary is not None:
                payload["capability_result_summary"] = capability_result_summary
        payload["allowed_state_types"] = self._world_state_allowed_state_types(source_pack=payload)
        return payload

    def _build_world_state_visual_context(
        self,
        *,
        observation_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {}
        visual_summary_text = None
        capability_id_text = None
        if not self._observation_summary_updates_visual_world_state(observation_summary):
            return None
        if isinstance(observation_summary, dict):
            visual_summary_text = self._client_context_text(observation_summary.get("visual_summary_text"), limit=160)
            if visual_summary_text is not None:
                payload["summary_text"] = visual_summary_text
                payload["visual_summary_text"] = visual_summary_text
            image_interpreted = observation_summary.get("image_interpreted")
            if isinstance(image_interpreted, bool):
                payload["image_interpreted"] = image_interpreted
            visual_confidence_hint = observation_summary.get("visual_confidence_hint")
            if isinstance(visual_confidence_hint, str) and visual_confidence_hint.strip():
                payload["visual_confidence_hint"] = visual_confidence_hint.strip()
            image_count = observation_summary.get("image_count")
            if isinstance(image_count, int) and image_count >= 0:
                payload["image_count"] = image_count
            capability_id = observation_summary.get("capability_id")
            if isinstance(capability_id, str) and capability_id.strip():
                capability_id_text = capability_id.strip()
            for source_key, limit in (
                ("vision_source_id", 96),
                ("source_kind", 32),
                ("source_label", 80),
            ):
                value = observation_summary.get(source_key)
                if isinstance(value, str) and value.strip():
                    payload[source_key] = self._clamp(value.strip(), limit=limit)
        has_visual_signal = "summary_text" in payload
        if not has_visual_signal:
            return None
        if capability_id_text is not None:
            payload["capability_id"] = capability_id_text
        return payload

    def _observation_summary_updates_visual_world_state(
        self,
        observation_summary: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(observation_summary, dict):
            return False
        if self._observation_summary_is_desktop_vision_capture(observation_summary):
            return False
        return (
            observation_summary.get("source") == "capability_result"
            and observation_summary.get("capability_id") == "vision.capture"
        )

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
            source_key = self._world_state_vision_source_key({"vision_source_id": vision_source_id})
            if source_key is not None:
                return source_key
        return None

    def _build_world_state_external_service_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        source_kind: str,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {}
        client_summary_text = self._client_context_text(client_context.get("external_service_summary"), limit=160)
        summary_text = client_summary_text
        capability_id_text = None
        result_summary_text = None
        if client_summary_text is not None:
            payload["external_service_summary"] = client_summary_text
            payload["client_summary_text"] = client_summary_text
        if isinstance(observation_summary, dict):
            result_summary_text = self._client_context_text(observation_summary.get("status_text"), limit=160)
            if result_summary_text is not None:
                summary_text = result_summary_text
                payload["status_text"] = result_summary_text
                payload["result_summary_text"] = result_summary_text
            service = self._client_context_text(observation_summary.get("service"), limit=80)
            if service is not None:
                payload["service"] = service
            capability_id = observation_summary.get("capability_id")
            if isinstance(capability_id, str) and capability_id.strip():
                capability_id_text = capability_id.strip()
        has_external_signal = summary_text is not None or "status_text" in payload or "service" in payload
        if not has_external_signal:
            return None
        if summary_text is not None:
            payload["summary_text"] = summary_text
        if result_summary_text is not None:
            payload["summary_source_hint"] = "capability_result.status_text"
        elif client_summary_text is not None:
            payload["summary_source_hint"] = self._world_state_client_context_summary_source(
                source_kind=source_kind,
                field_name="external_service_summary",
            )
        if capability_id_text is not None:
            payload["capability_id"] = capability_id_text
        return payload

    def _build_world_state_body_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        source_kind: str,
    ) -> dict[str, Any] | None:
        return self._build_world_state_capability_state_context(
            client_context=client_context,
            observation_summary=observation_summary,
            source_kind=source_kind,
            client_summary_key="body_state_summary",
            observation_summary_key="body_state_summary",
            explicit_field_name="body_state_summary",
        )

    def _build_world_state_device_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        source_kind: str,
    ) -> dict[str, Any] | None:
        return self._build_world_state_capability_state_context(
            client_context=client_context,
            observation_summary=observation_summary,
            source_kind=source_kind,
            client_summary_key="device_state_summary",
            observation_summary_key="device_state_summary",
            explicit_field_name="device_state_summary",
        )

    def _build_world_state_capability_state_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        source_kind: str,
        client_summary_key: str,
        observation_summary_key: str,
        explicit_field_name: str,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {}
        client_summary_text = self._client_context_text(client_context.get(client_summary_key), limit=160)
        summary_text = client_summary_text
        capability_id_text = None
        observation_text = None
        if client_summary_text is not None:
            payload["client_summary_text"] = client_summary_text
        if isinstance(observation_summary, dict):
            observation_text = self._client_context_text(observation_summary.get(observation_summary_key), limit=160)
            if observation_text is not None:
                summary_text = observation_text
                payload["result_summary_text"] = observation_text
            capability_id = observation_summary.get("capability_id")
            if isinstance(capability_id, str) and capability_id.strip():
                capability_id_text = capability_id.strip()
        if summary_text is None:
            return None
        payload["summary_text"] = summary_text
        payload[explicit_field_name] = summary_text
        if observation_text is not None:
            payload["summary_source_hint"] = f"capability_result.{observation_summary_key}"
        elif client_summary_text is not None:
            payload["summary_source_hint"] = self._world_state_client_context_summary_source(
                source_kind=source_kind,
                field_name=client_summary_key,
            )
        if capability_id_text is not None:
            payload["capability_id"] = capability_id_text
        return payload

    def _build_world_state_client_context(self, client_context: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key, limit in (
            ("source", 48),
        ):
            value = client_context.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = self._clamp(value.strip(), limit=limit)
        return payload

    def _build_world_state_social_context_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        source_kind: str,
    ) -> dict[str, Any] | None:
        return self._build_world_state_capability_state_context(
            client_context=client_context,
            observation_summary=observation_summary,
            source_kind=source_kind,
            client_summary_key="social_context_summary",
            observation_summary_key="social_context_summary",
            explicit_field_name="social_context_summary",
        )

    def _build_world_state_environment_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        source_kind: str,
    ) -> dict[str, Any] | None:
        return self._build_world_state_capability_state_context(
            client_context=client_context,
            observation_summary=observation_summary,
            source_kind=source_kind,
            client_summary_key="environment_summary",
            observation_summary_key="environment_summary",
            explicit_field_name="environment_summary",
        )

    def _build_world_state_location_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        source_kind: str,
    ) -> dict[str, Any] | None:
        return self._build_world_state_capability_state_context(
            client_context=client_context,
            observation_summary=observation_summary,
            source_kind=source_kind,
            client_summary_key="location_summary",
            observation_summary_key="location_summary",
            explicit_field_name="location_summary",
        )

    def _build_world_state_schedule_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        source_kind: str,
        selected_candidate: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {}
        client_summary_text = self._client_context_text(client_context.get("schedule_summary"), limit=160)
        summary_text = client_summary_text
        capability_id_text = None
        observation_text = None
        schedule_slots = self._build_world_state_schedule_slots(
            client_context=client_context,
            observation_summary=observation_summary,
            source_kind=source_kind,
        )
        if client_summary_text is not None:
            payload["client_summary_text"] = client_summary_text
        if isinstance(observation_summary, dict):
            observation_text = self._client_context_text(observation_summary.get("schedule_summary"), limit=160)
            if observation_text is not None:
                summary_text = observation_text
                payload["result_summary_text"] = observation_text
            capability_id = observation_summary.get("capability_id")
            if isinstance(capability_id, str) and capability_id.strip():
                capability_id_text = capability_id.strip()
        if summary_text is not None:
            payload["summary_text"] = summary_text
            payload["schedule_summary"] = summary_text
            if observation_text is not None:
                payload["summary_source_hint"] = "capability_result.schedule_summary"
            elif client_summary_text is not None:
                payload["summary_source_hint"] = self._world_state_client_context_summary_source(
                    source_kind=source_kind,
                    field_name="schedule_summary",
                )
        elif schedule_slots:
            payload["summary_text"] = (
                schedule_slots[0]["summary_text"]
                if len(schedule_slots) == 1
                else f"近い予定が {len(schedule_slots)} 件ある。"
            )
            payload["summary_source_hint"] = self._world_state_client_context_summary_source(
                source_kind=source_kind,
                field_name="schedule_slots",
                from_observation=isinstance(observation_summary, dict)
                and isinstance(observation_summary.get("schedule_slots"), list)
                and bool(observation_summary.get("schedule_slots")),
            )
        if schedule_slots:
            payload["schedule_slots"] = schedule_slots
        pending_intent = self._build_world_state_pending_intent_context(selected_candidate)
        if pending_intent is not None:
            payload["pending_intent"] = pending_intent
        if capability_id_text is not None and summary_text is not None:
            payload["capability_id"] = capability_id_text
        if not payload:
            return None
        return payload

    def _build_world_state_schedule_slots(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        source_kind: str,
    ) -> list[dict[str, Any]]:
        raw_slots: Any = None
        from_observation = False
        if isinstance(observation_summary, dict):
            raw_slots = observation_summary.get("schedule_slots")
            from_observation = isinstance(raw_slots, list)
        if not isinstance(raw_slots, list):
            raw_slots = client_context.get("schedule_slots")
            from_observation = False
        if not isinstance(raw_slots, list):
            return []
        normalized_slots: list[dict[str, Any]] = []
        seen_slot_keys: set[str] = set()
        summary_source = self._world_state_client_context_summary_source(
            source_kind=source_kind,
            field_name="schedule_slots",
            from_observation=from_observation,
        )
        for item in raw_slots:
            if not isinstance(item, dict):
                continue
            slot_key = self._client_context_text(item.get("slot_key"), limit=160)
            summary_text = self._client_context_text(item.get("summary_text"), limit=160)
            if slot_key is None or summary_text is None or slot_key in seen_slot_keys:
                continue
            seen_slot_keys.add(slot_key)
            slot_payload: dict[str, Any] = {
                "slot_key": slot_key,
                "summary_text": summary_text,
                "summary_source": summary_source,
            }
            for key in ("not_before", "expires_at"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    slot_payload[key] = value.strip()
            normalized_slots.append(slot_payload)
        return normalized_slots[:4]

    def _build_world_state_pending_intent_context(
        self,
        selected_candidate: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(selected_candidate, dict):
            return None
        payload: dict[str, Any] = {}
        for key, limit in (
            ("intent_kind", 48),
            ("intent_summary", 120),
            ("reason_summary", 160),
        ):
            value = self._client_context_text(selected_candidate.get(key), limit=limit)
            if value is not None:
                payload[key] = value
        for key in ("not_before", "expires_at"):
            value = selected_candidate.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = value.strip()
        slot_key = self._world_state_schedule_slot_key(selected_candidate)
        if slot_key is not None:
            payload["slot_key"] = slot_key
        if not payload:
            return None
        return payload

    def _world_state_client_context_summary_source(
        self,
        *,
        source_kind: str,
        field_name: str,
        from_observation: bool = False,
    ) -> str:
        if source_kind == "capability_result" and from_observation:
            return f"capability_result.{field_name}"
        if source_kind == "capability_result":
            return f"capability_result.client_context.{field_name}"
        return f"client_context.{field_name}"

    def _build_world_state_capability_result_summary(
        self,
        observation_summary: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(observation_summary, dict):
            return None
        payload: dict[str, Any] = {}
        for key in (
            "capability_id",
            "image_count",
            "image_interpreted",
            "visual_summary_text",
            "visual_confidence_hint",
            "service",
            "status_text",
            "social_context_summary",
            "body_state_summary",
            "device_state_summary",
            "schedule_summary",
            "environment_summary",
            "location_summary",
            "schedule_slots",
            "error",
        ):
            value = observation_summary.get(key)
            if value is None:
                continue
            payload[key] = value
        if not payload:
            return None
        return payload

    def _summarize_world_state_source_pack_contexts(self, source_pack: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        allowed_state_types = source_pack.get("allowed_state_types")
        if isinstance(allowed_state_types, list):
            summary["allowed_state_types"] = [
                value
                for value in allowed_state_types
                if isinstance(value, str) and value.strip()
            ]
        for key in (
            "client_context",
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
            value = source_pack.get(key)
            if isinstance(value, dict) and value:
                summary[key] = value
        return summary

    def _summarize_world_state_state_type_hooks(self, source_pack: dict[str, Any]) -> dict[str, Any]:
        hooks: dict[str, Any] = {}
        for state_type, context_key in WORLD_STATE_CONTEXT_KEYS_BY_TYPE:
            context = source_pack.get(context_key)
            if not isinstance(context, dict) or not context:
                continue
            hook = self._build_world_state_state_type_hook(state_type=state_type, context=context)
            if hook is not None:
                hooks[state_type] = hook
        return hooks

    def _build_world_state_state_type_hook(
        self,
        *,
        state_type: str,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        summary_text = self._client_context_text(context.get("summary_text"), limit=160)
        if summary_text is None:
            return None
        payload: dict[str, Any] = {
            "summary_text": summary_text,
            "summary_source": self._world_state_hook_summary_source(state_type=state_type, context=context),
            "signal_fields": self._world_state_hook_signal_fields(state_type=state_type, context=context),
        }
        capability_id = self._client_context_text(context.get("capability_id"), limit=80)
        if capability_id is not None:
            payload["capability_id"] = capability_id
        if state_type == "visual_context":
            for source_key, limit in (
                ("vision_source_id", 96),
                ("source_kind", 32),
                ("source_label", 80),
            ):
                value = self._client_context_text(context.get(source_key), limit=limit)
                if value is not None:
                    payload[source_key] = value
        if state_type == "external_service":
            service = self._client_context_text(context.get("service"), limit=80)
            if service is not None:
                payload["service"] = service
        elif state_type == "schedule":
            pending_intent = context.get("pending_intent")
            if isinstance(pending_intent, dict):
                pending_summary = self._client_context_text(pending_intent.get("intent_summary"), limit=120)
                if pending_summary is not None:
                    payload["pending_intent_summary"] = pending_summary
                slot_key = self._client_context_text(pending_intent.get("slot_key"), limit=160)
                if slot_key is not None:
                    payload["pending_intent_slot_key"] = slot_key
            schedule_slots = context.get("schedule_slots")
            if isinstance(schedule_slots, list) and schedule_slots:
                slot_keys = [
                    self._client_context_text(item.get("slot_key"), limit=160)
                    for item in schedule_slots
                    if isinstance(item, dict)
                ]
                payload["real_schedule_slot_count"] = len(schedule_slots)
                payload["schedule_slot_keys"] = [value for value in slot_keys if value is not None][:4]
        return payload

    def _world_state_hook_summary_source(self, *, state_type: str, context: dict[str, Any]) -> str:
        summary_source_hint = self._client_context_text(context.get("summary_source_hint"), limit=120)
        if summary_source_hint is not None:
            return summary_source_hint
        if state_type == "visual_context":
            if isinstance(context.get("visual_summary_text"), str) and context["visual_summary_text"].strip():
                return "visual_summary_text"
            return "summary_text"
        if state_type == "external_service":
            if isinstance(context.get("status_text"), str) and context["status_text"].strip():
                return "status_text"
            return "external_service_summary"
        if state_type == "body":
            return "body_state_summary"
        if state_type == "device":
            return "device_state_summary"
        if state_type == "schedule":
            if isinstance(context.get("schedule_summary"), str) and context["schedule_summary"].strip():
                return "schedule_summary"
            if isinstance(context.get("pending_intent"), dict):
                return "pending_intent"
        if state_type == "social_context":
            return "social_context_summary"
        if state_type == "environment":
            return "environment_summary"
        if state_type == "location":
            return "location_summary"
        return "summary_text"

    def _world_state_hook_signal_fields(self, *, state_type: str, context: dict[str, Any]) -> list[str]:
        keys_by_state_type = {
            "visual_context": (
                "vision_source_id",
                "source_kind",
                "source_label",
                "active_app",
                "window_title",
                "visual_summary_text",
                "image_interpreted",
                "visual_confidence_hint",
                "image_count",
            ),
            "external_service": (
                "service",
                "status_text",
                "external_service_summary",
            ),
            "body": (
                "body_state_summary",
            ),
            "device": (
                "device_state_summary",
            ),
            "schedule": (
                "schedule_summary",
                "schedule_slots",
                "pending_intent",
            ),
            "social_context": (
                "social_context_summary",
            ),
            "environment": (
                "environment_summary",
            ),
            "location": (
                "location_summary",
            ),
        }
        signal_fields: list[str] = []
        for key in keys_by_state_type.get(state_type, ()):
            value = context.get(key)
            if isinstance(value, str):
                if value.strip():
                    signal_fields.append(key)
            elif isinstance(value, dict):
                if value:
                    signal_fields.append(key)
            elif isinstance(value, (int, float, bool)):
                signal_fields.append(key)
        return signal_fields

    def _world_state_allowed_state_types(self, *, source_pack: dict[str, Any]) -> list[str]:
        allowed: list[str] = []
        for state_type, context_key in WORLD_STATE_CONTEXT_KEYS_BY_TYPE:
            context = source_pack.get(context_key)
            if isinstance(context, dict) and context:
                allowed.append(state_type)
        return allowed

    def _normalize_world_state_candidates(
        self,
        *,
        memory_set_id: str,
        observed_at: str,
        source_kind: str,
        source_ref: str,
        payload: dict[str, Any],
        source_pack: dict[str, Any],
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen_identity: set[tuple[str, str, str]] = set()
        allowed_state_types = set(self._world_state_allowed_state_types(source_pack=source_pack))
        for candidate in payload.get("state_candidates", []):
            if not isinstance(candidate, dict):
                continue
            state_type = str(candidate["state_type"]).strip()
            if state_type not in allowed_state_types:
                continue
            scope_type, scope_key = self._parse_world_state_scope(str(candidate["scope"]).strip())
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
            ttl_hint = str(candidate["ttl_hint"]).strip()
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
                    "summary_text": str(candidate["summary_text"]).strip(),
                    "source_kind": source_kind,
                    "source_ref": source_ref,
                    "confidence": self._world_state_score_from_hint(candidate["confidence_hint"]),
                    "salience": self._world_state_score_from_hint(candidate["salience_hint"]),
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

    def _should_skip_user_input_current_state_candidate(
        self,
        *,
        state_type: str,
        source_kind: str,
        source_context: dict[str, Any] | None,
        source_pack: dict[str, Any],
    ) -> bool:
        if source_kind != "user_input" or source_context is not None:
            return False
        state_terms = WORLD_STATE_USER_INPUT_CURRENT_STATE_TERMS_BY_TYPE.get(state_type)
        if not state_terms:
            return False
        current_input = str(source_pack.get("current_input_summary") or "").strip()
        if not current_input:
            return False
        if not self._contains_any_text(current_input, WORLD_STATE_USER_INPUT_REQUEST_TERMS):
            return False
        return self._contains_any_text(current_input, state_terms)

    def _should_skip_system_wake_inferred_state_candidate(
        self,
        *,
        state_type: str,
        source_context: dict[str, Any] | None,
        source_pack: dict[str, Any],
    ) -> bool:
        if source_pack.get("trigger_kind") not in {"wake", "background_wake"}:
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
        source_pack: dict[str, Any],
    ) -> dict[str, Any] | None:
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
        context = source_pack.get(context_key)
        if not isinstance(context, dict) or not context:
            return None
        return context

    def _world_state_ttl_policy(
        self,
        *,
        current_time: str,
        state_type: str,
        ttl_hint: str,
        context: dict[str, Any] | None,
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
        context: dict[str, Any] | None,
    ) -> str:
        if not isinstance(context, dict) or not context:
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
        source_pack: dict[str, Any],
    ) -> list[dict[str, Any]]:
        context = self._world_state_source_context(state_type="schedule", source_pack=source_pack)
        if not isinstance(context, dict):
            return []
        schedule_slots = context.get("schedule_slots")
        if not isinstance(schedule_slots, list):
            return []
        normalized_records: list[dict[str, Any]] = []
        seen_slot_keys: set[str] = set()
        for item in schedule_slots:
            if not isinstance(item, dict):
                continue
            slot_key = self._client_context_text(item.get("slot_key"), limit=160)
            summary_text = self._client_context_text(item.get("summary_text"), limit=160)
            if slot_key is None or summary_text is None or slot_key in seen_slot_keys:
                continue
            ttl_policy = self._world_state_schedule_slot_ttl_policy(
                current_time=observed_at,
                source_kind=source_kind,
                schedule_slot=item,
            )
            if ttl_policy is None:
                continue
            seen_slot_keys.add(slot_key)
            record: dict[str, Any] = {
                "world_state_id": f"world_state:{uuid.uuid4().hex}",
                "memory_set_id": memory_set_id,
                "state_type": "schedule",
                "scope_type": "self",
                "scope_key": "self",
                "summary_text": summary_text,
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
                "integration_key": f"schedule:{slot_key}",
                "slot_key": slot_key,
            }
            not_before = item.get("not_before")
            if isinstance(not_before, str) and not_before.strip():
                record["slot_not_before"] = not_before.strip()
            slot_expires_at = item.get("expires_at")
            if isinstance(slot_expires_at, str) and slot_expires_at.strip():
                record["slot_expires_at"] = slot_expires_at.strip()
            if ttl_policy.get("capped_by") is not None:
                record["ttl_capped_by"] = ttl_policy["capped_by"]
            normalized_records.append(record)
        return normalized_records

    def _world_state_schedule_slot_ttl_policy(
        self,
        *,
        current_time: str,
        source_kind: str,
        schedule_slot: dict[str, Any],
    ) -> dict[str, Any] | None:
        raw_summary_source = schedule_slot.get("summary_source")
        if isinstance(raw_summary_source, str) and raw_summary_source.strip():
            summary_source = raw_summary_source.strip()
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
        expires_at = schedule_slot.get("expires_at")
        if isinstance(expires_at, str) and expires_at.strip():
            remaining_seconds = int((self._parse_iso(expires_at.strip()) - self._parse_iso(current_time)).total_seconds())
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
        context: dict[str, Any] | None,
    ) -> str | None:
        if state_type != "schedule" or not isinstance(context, dict):
            return None
        pending_intent = context.get("pending_intent")
        if not isinstance(pending_intent, dict):
            return None
        expires_at = pending_intent.get("expires_at")
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
        context: dict[str, Any] | None,
    ) -> int:
        if state_type != "schedule" or not isinstance(context, dict):
            raise ValueError("world_state ttl cap is invalid.")
        pending_intent = context.get("pending_intent")
        if not isinstance(pending_intent, dict):
            raise ValueError("world_state ttl cap is invalid.")
        expires_at = pending_intent.get("expires_at")
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
        context: dict[str, Any] | None,
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

    def _world_state_vision_source_key(self, context: dict[str, Any] | None) -> str | None:
        if not isinstance(context, dict):
            return None
        vision_source_id = self._client_context_text(context.get("vision_source_id"), limit=96)
        if vision_source_id is None:
            return None
        return vision_source_id.strip() or None

    def _world_state_service_key(self, context: dict[str, Any] | None) -> str | None:
        if not isinstance(context, dict):
            return None
        service = self._client_context_text(context.get("service"), limit=80)
        if service is None:
            return None
        normalized = "".join(character if character.isalnum() else "_" for character in service.lower()).strip("_")
        return normalized or None

    def _world_state_schedule_context_slot_key(self, context: dict[str, Any] | None) -> str | None:
        if not isinstance(context, dict):
            return None
        pending_intent = context.get("pending_intent")
        if not isinstance(pending_intent, dict):
            return None
        slot_key = self._client_context_text(pending_intent.get("slot_key"), limit=160)
        if slot_key is None:
            return None
        return slot_key

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

    def _world_state_source_kind(self, trigger_kind: str) -> str:
        if trigger_kind == "user_message":
            return "user_input"
        if trigger_kind == "capability_result":
            return "capability_result"
        return "client_context"

    def _world_state_source_ref(
        self,
        *,
        cycle_id: str | None,
        trigger_kind: str,
        started_at: str,
        capability_request_summary: dict[str, Any] | None,
    ) -> str:
        if isinstance(capability_request_summary, dict):
            request_id = capability_request_summary.get("request_id")
            if isinstance(request_id, str) and request_id.strip():
                return request_id.strip()
        if isinstance(cycle_id, str) and cycle_id:
            return cycle_id
        return f"{trigger_kind}:{started_at}"

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
