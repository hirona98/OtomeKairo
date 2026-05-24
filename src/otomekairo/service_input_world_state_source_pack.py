from __future__ import annotations

from typing import Any

from otomekairo.llm import LLMError
from otomekairo.memory_utils import llm_local_time_text
from otomekairo.service_input_constants import (
    WORLD_STATE_CONTEXT_KEYS_BY_TYPE,
    WORLD_STATE_FOREGROUND_LIMIT,
    WORLD_STATE_MAX_ACTIVE,
)
from otomekairo.world_state_models import (
    WorldStateCapabilityResultSummary,
    WorldStateClientContext,
    WorldStateContext,
    WorldStateExternalServiceContext,
    WorldStateNamedSummaryContext,
    WorldStatePendingIntent,
    WorldStateScheduleContext,
    WorldStateScheduleSlot,
    WorldStateSourcePack,
    WorldStateTrace,
    WorldStateVisualContext,
)


class ServiceInputWorldStateSourcePackMixin:
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
    ) -> tuple[WorldStateTrace, list[dict[str, Any]]]:
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
            world_state_trace = WorldStateTrace(
                result_status="succeeded",
                candidate_state_count=len(payload.get("state_candidates", [])),
                input_world_state_count=len(visible_foreground_world_state),
                previous_foreground_world_state=previous_foreground_world_state,
                foreground_world_state=visible_foreground_world_state,
                updated_state_count=int(refresh_summary.get("updated_count", 0)),
                replaced_state_count=int(refresh_summary.get("replaced_count", 0)),
                expired_state_count=int(refresh_summary.get("expired_count", 0)),
                dropped_state_count=int(refresh_summary.get("dropped_count", 0)),
                source_kind=source_kind,
                source_ref=source_ref,
                source_pack_contexts=source_pack_contexts,
                source_pack_state_type_hooks=source_pack_state_type_hooks,
                normalized_candidate_policies=normalized_candidate_policies,
                failure_reason=None,
                foreground_world_state_filter=foreground_visibility_filter,
                stored_foreground_world_state=foreground_world_state if foreground_visibility_filter is not None else None,
            )
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
            world_state_trace = WorldStateTrace(
                result_status="failed",
                candidate_state_count=0,
                input_world_state_count=len(visible_foreground_world_state),
                previous_foreground_world_state=previous_foreground_world_state,
                foreground_world_state=visible_foreground_world_state,
                updated_state_count=0,
                replaced_state_count=0,
                expired_state_count=0,
                dropped_state_count=0,
                source_kind=source_kind,
                source_ref=source_ref,
                source_pack_contexts=source_pack_contexts,
                source_pack_state_type_hooks=source_pack_state_type_hooks,
                normalized_candidate_policies=[],
                failure_reason=str(exc),
                foreground_world_state_filter=foreground_visibility_filter,
                stored_foreground_world_state=(
                    previous_foreground_world_state if foreground_visibility_filter is not None else None
                ),
            )
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
    ) -> WorldStateSourcePack:
        payload = WorldStateSourcePack(
            trigger_kind=trigger_kind,
            current_input_summary=self._clamp(input_text.strip(), limit=200) or "",
            source_kind=source_kind,
            source_ref=source_ref,
            time_context=llm_local_time_text(started_at).replace("\n", " / "),
            client_context=self._build_world_state_client_context(client_context),
        )
        visual_context = self._build_world_state_visual_context(
            observation_summary=observation_summary,
        )
        if visual_context is not None:
            payload.visual_context = visual_context
        for attribute_name, value in (
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
                setattr(payload, attribute_name, value)
        if source_kind == "capability_result":
            capability_result_summary = self._build_world_state_capability_result_summary(observation_summary)
            if capability_result_summary is not None:
                payload.capability_result_summary = capability_result_summary
        payload.allowed_state_types = tuple(self._world_state_allowed_state_types(source_pack=payload))
        return payload

    def _build_world_state_visual_context(
        self,
        *,
        observation_summary: dict[str, Any] | None,
    ) -> WorldStateVisualContext | None:
        visual_summary_text = None
        if not self._observation_summary_updates_visual_world_state(observation_summary):
            return None
        image_interpreted = None
        visual_confidence_hint = None
        image_count = None
        capability_id_text = None
        vision_source_id = None
        source_kind = None
        source_label = None
        if isinstance(observation_summary, dict):
            visual_summary_text = self._client_context_text(observation_summary.get("visual_summary_text"), limit=160)
            image_interpreted_value = observation_summary.get("image_interpreted")
            if isinstance(image_interpreted_value, bool):
                image_interpreted = image_interpreted_value
            visual_confidence_hint_value = observation_summary.get("visual_confidence_hint")
            if isinstance(visual_confidence_hint_value, str) and visual_confidence_hint_value.strip():
                visual_confidence_hint = visual_confidence_hint_value.strip()
            image_count_value = observation_summary.get("image_count")
            if isinstance(image_count_value, int) and image_count_value >= 0:
                image_count = image_count_value
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
                    normalized_value = self._clamp(value.strip(), limit=limit)
                    if source_key == "vision_source_id":
                        vision_source_id = normalized_value
                    elif source_key == "source_kind":
                        source_kind = normalized_value
                    elif source_key == "source_label":
                        source_label = normalized_value
        if visual_summary_text is None:
            return None
        return WorldStateVisualContext(
            summary_text=visual_summary_text,
            visual_summary_text=visual_summary_text,
            image_interpreted=image_interpreted,
            visual_confidence_hint=visual_confidence_hint,
            image_count=image_count,
            capability_id=capability_id_text,
            vision_source_id=vision_source_id,
            source_kind=source_kind,
            source_label=source_label,
        )

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

    def _build_world_state_external_service_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        source_kind: str,
    ) -> WorldStateExternalServiceContext | None:
        client_summary_text = self._client_context_text(client_context.get("external_service_summary"), limit=160)
        summary_text = client_summary_text
        result_summary_text = None
        service = None
        capability_id_text = None
        if isinstance(observation_summary, dict):
            result_summary_text = self._client_context_text(observation_summary.get("status_text"), limit=160)
            if result_summary_text is not None:
                summary_text = result_summary_text
            service = self._client_context_text(observation_summary.get("service"), limit=80)
            capability_id = observation_summary.get("capability_id")
            if isinstance(capability_id, str) and capability_id.strip():
                capability_id_text = capability_id.strip()
        has_external_signal = summary_text is not None or result_summary_text is not None or service is not None
        if not has_external_signal:
            return None
        summary_source_hint = None
        if result_summary_text is not None:
            summary_source_hint = "capability_result.status_text"
        elif client_summary_text is not None:
            summary_source_hint = self._world_state_client_context_summary_source(
                source_kind=source_kind,
                field_name="external_service_summary",
            )
        return WorldStateExternalServiceContext(
            summary_text=summary_text or result_summary_text or service or "",
            external_service_summary=client_summary_text,
            client_summary_text=client_summary_text,
            result_summary_text=result_summary_text,
            status_text=result_summary_text,
            service=service,
            summary_source_hint=summary_source_hint,
            capability_id=capability_id_text,
        )

    def _build_world_state_body_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        source_kind: str,
    ) -> WorldStateNamedSummaryContext | None:
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
    ) -> WorldStateNamedSummaryContext | None:
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
    ) -> WorldStateNamedSummaryContext | None:
        client_summary_text = self._client_context_text(client_context.get(client_summary_key), limit=160)
        summary_text = client_summary_text
        capability_id_text = None
        observation_text = None
        if isinstance(observation_summary, dict):
            observation_text = self._client_context_text(observation_summary.get(observation_summary_key), limit=160)
            if observation_text is not None:
                summary_text = observation_text
            capability_id = observation_summary.get("capability_id")
            if isinstance(capability_id, str) and capability_id.strip():
                capability_id_text = capability_id.strip()
        if summary_text is None:
            return None
        summary_source_hint = None
        if observation_text is not None:
            summary_source_hint = f"capability_result.{observation_summary_key}"
        elif client_summary_text is not None:
            summary_source_hint = self._world_state_client_context_summary_source(
                source_kind=source_kind,
                field_name=client_summary_key,
            )
        return WorldStateNamedSummaryContext(
            summary_text=summary_text,
            summary_field_name=explicit_field_name,
            client_summary_text=client_summary_text,
            result_summary_text=observation_text,
            summary_source_hint=summary_source_hint,
            capability_id=capability_id_text,
        )

    def _build_world_state_client_context(self, client_context: dict[str, Any]) -> WorldStateClientContext:
        source = None
        value = client_context.get("source")
        if isinstance(value, str) and value.strip():
            source = self._clamp(value.strip(), limit=48)
        return WorldStateClientContext(source=source)

    def _build_world_state_social_context_context(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        source_kind: str,
    ) -> WorldStateNamedSummaryContext | None:
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
    ) -> WorldStateNamedSummaryContext | None:
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
    ) -> WorldStateNamedSummaryContext | None:
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
    ) -> WorldStateScheduleContext | None:
        client_summary_text = self._client_context_text(client_context.get("schedule_summary"), limit=160)
        summary_text = client_summary_text
        capability_id_text = None
        observation_text = None
        schedule_slots = self._build_world_state_schedule_slots(
            client_context=client_context,
            observation_summary=observation_summary,
            source_kind=source_kind,
        )
        if isinstance(observation_summary, dict):
            observation_text = self._client_context_text(observation_summary.get("schedule_summary"), limit=160)
            if observation_text is not None:
                summary_text = observation_text
            capability_id = observation_summary.get("capability_id")
            if isinstance(capability_id, str) and capability_id.strip():
                capability_id_text = capability_id.strip()
        summary_source_hint = None
        schedule_summary = None
        if summary_text is not None:
            schedule_summary = summary_text
            if observation_text is not None:
                summary_source_hint = "capability_result.schedule_summary"
            elif client_summary_text is not None:
                summary_source_hint = self._world_state_client_context_summary_source(
                    source_kind=source_kind,
                    field_name="schedule_summary",
                )
        elif schedule_slots:
            summary_text = (
                schedule_slots[0].summary_text
                if len(schedule_slots) == 1
                else f"近い予定が {len(schedule_slots)} 件ある。"
            )
            summary_source_hint = self._world_state_client_context_summary_source(
                source_kind=source_kind,
                field_name="schedule_slots",
                from_observation=isinstance(observation_summary, dict)
                and isinstance(observation_summary.get("schedule_slots"), list)
                and bool(observation_summary.get("schedule_slots")),
            )
        pending_intent = self._build_world_state_pending_intent_context(selected_candidate)
        if summary_text is None and pending_intent is None and not schedule_slots:
            return None
        return WorldStateScheduleContext(
            summary_text=summary_text,
            schedule_summary=schedule_summary,
            client_summary_text=client_summary_text,
            result_summary_text=observation_text,
            summary_source_hint=summary_source_hint,
            capability_id=capability_id_text,
            schedule_slots=schedule_slots,
            pending_intent=pending_intent,
        )

    def _build_world_state_schedule_slots(
        self,
        *,
        client_context: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        source_kind: str,
    ) -> tuple[WorldStateScheduleSlot, ...]:
        raw_slots: Any = None
        from_observation = False
        if isinstance(observation_summary, dict):
            raw_slots = observation_summary.get("schedule_slots")
            from_observation = isinstance(raw_slots, list)
        if not isinstance(raw_slots, list):
            raw_slots = client_context.get("schedule_slots")
            from_observation = False
        if not isinstance(raw_slots, list):
            return ()
        normalized_slots: list[WorldStateScheduleSlot] = []
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
            not_before = item.get("not_before")
            expires_at = item.get("expires_at")
            normalized_slots.append(
                WorldStateScheduleSlot(
                    slot_key=slot_key,
                    summary_text=summary_text,
                    summary_source=summary_source,
                    not_before=not_before.strip() if isinstance(not_before, str) and not_before.strip() else None,
                    expires_at=expires_at.strip() if isinstance(expires_at, str) and expires_at.strip() else None,
                )
            )
        return tuple(normalized_slots[:4])

    def _build_world_state_pending_intent_context(
        self,
        selected_candidate: dict[str, Any] | None,
    ) -> WorldStatePendingIntent | None:
        if not isinstance(selected_candidate, dict):
            return None
        payload: dict[str, str] = {}
        for key, limit in (
            ("intent_kind", 48),
            ("intent_summary", 120),
            ("reason_summary", 160),
        ):
            value = self._client_context_text(selected_candidate.get(key), limit=limit)
            if value is not None:
                payload[key] = value
        not_before = None
        expires_at = None
        for key in ("not_before", "expires_at"):
            value = selected_candidate.get(key)
            if isinstance(value, str) and value.strip():
                if key == "not_before":
                    not_before = value.strip()
                else:
                    expires_at = value.strip()
        slot_key = self._world_state_schedule_slot_key(selected_candidate)
        if not payload and not_before is None and expires_at is None and slot_key is None:
            return None
        return WorldStatePendingIntent(
            intent_kind=payload.get("intent_kind"),
            intent_summary=payload.get("intent_summary"),
            reason_summary=payload.get("reason_summary"),
            not_before=not_before,
            expires_at=expires_at,
            slot_key=slot_key,
        )

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
    ) -> WorldStateCapabilityResultSummary | None:
        if not isinstance(observation_summary, dict):
            return None
        schedule_slots = self._build_world_state_schedule_slots(
            client_context={},
            observation_summary=observation_summary,
            source_kind="capability_result",
        )
        payload = WorldStateCapabilityResultSummary(
            capability_id=self._client_context_text(observation_summary.get("capability_id"), limit=80),
            image_count=(
                observation_summary.get("image_count")
                if isinstance(observation_summary.get("image_count"), int) and observation_summary.get("image_count") >= 0
                else None
            ),
            image_interpreted=(
                observation_summary.get("image_interpreted")
                if isinstance(observation_summary.get("image_interpreted"), bool)
                else None
            ),
            visual_summary_text=self._client_context_text(observation_summary.get("visual_summary_text"), limit=160),
            visual_confidence_hint=self._client_context_text(observation_summary.get("visual_confidence_hint"), limit=24),
            service=self._client_context_text(observation_summary.get("service"), limit=80),
            status_text=self._client_context_text(observation_summary.get("status_text"), limit=160),
            social_context_summary=self._client_context_text(observation_summary.get("social_context_summary"), limit=160),
            body_state_summary=self._client_context_text(observation_summary.get("body_state_summary"), limit=160),
            device_state_summary=self._client_context_text(observation_summary.get("device_state_summary"), limit=160),
            schedule_summary=self._client_context_text(observation_summary.get("schedule_summary"), limit=160),
            environment_summary=self._client_context_text(observation_summary.get("environment_summary"), limit=160),
            location_summary=self._client_context_text(observation_summary.get("location_summary"), limit=160),
            schedule_slots=schedule_slots,
            error=self._client_context_text(observation_summary.get("error"), limit=240),
        )
        if not payload.to_prompt_payload():
            return None
        return payload

    def _summarize_world_state_source_pack_contexts(self, source_pack: WorldStateSourcePack) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        if source_pack.allowed_state_types:
            summary["allowed_state_types"] = list(source_pack.allowed_state_types)
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
            if key == "client_context":
                client_context_payload = source_pack.client_context.to_prompt_payload()
                if client_context_payload:
                    summary[key] = client_context_payload
                continue
            if key == "capability_result_summary":
                value = source_pack.capability_result_summary
                if isinstance(value, WorldStateCapabilityResultSummary):
                    capability_result_payload = value.to_prompt_payload()
                    if capability_result_payload:
                        summary[key] = capability_result_payload
                continue
            value = source_pack.context(key)
            if value is not None:
                summary[key] = value.to_prompt_payload()
        return summary

    def _summarize_world_state_state_type_hooks(self, source_pack: WorldStateSourcePack) -> dict[str, Any]:
        hooks: dict[str, Any] = {}
        for state_type, context_key in WORLD_STATE_CONTEXT_KEYS_BY_TYPE:
            context = source_pack.context(context_key)
            if context is None:
                continue
            hook = self._build_world_state_state_type_hook(state_type=state_type, context=context)
            if hook is not None:
                hooks[state_type] = hook
        return hooks

    def _build_world_state_state_type_hook(
        self,
        *,
        state_type: str,
        context: WorldStateContext,
    ) -> dict[str, Any] | None:
        if not isinstance(context.summary_text, str) or not context.summary_text.strip():
            return None
        payload: dict[str, Any] = {
            "summary_text": context.summary_text,
            "summary_source": self._world_state_hook_summary_source(state_type=state_type, context=context),
            "signal_fields": self._world_state_hook_signal_fields(state_type=state_type, context=context),
        }
        capability_id = getattr(context, "capability_id", None)
        if isinstance(capability_id, str) and capability_id.strip():
            payload["capability_id"] = capability_id
        if isinstance(context, WorldStateVisualContext):
            for key, value in (
                ("vision_source_id", context.vision_source_id),
                ("source_kind", context.source_kind),
                ("source_label", context.source_label),
            ):
                if isinstance(value, str) and value.strip():
                    payload[key] = value
        if isinstance(context, WorldStateExternalServiceContext):
            if isinstance(context.service, str) and context.service.strip():
                payload["service"] = context.service
        elif isinstance(context, WorldStateScheduleContext):
            if isinstance(context.pending_intent, WorldStatePendingIntent):
                if (
                    isinstance(context.pending_intent.intent_summary, str)
                    and context.pending_intent.intent_summary.strip()
                ):
                    payload["pending_intent_summary"] = context.pending_intent.intent_summary
                if isinstance(context.pending_intent.slot_key, str) and context.pending_intent.slot_key.strip():
                    payload["pending_intent_slot_key"] = context.pending_intent.slot_key
            if context.schedule_slots:
                payload["real_schedule_slot_count"] = len(context.schedule_slots)
                payload["schedule_slot_keys"] = [slot.slot_key for slot in context.schedule_slots][:4]
        return payload

    def _world_state_hook_summary_source(self, *, state_type: str, context: WorldStateContext) -> str:
        _ = state_type
        return context.hook_summary_source()

    def _world_state_hook_signal_fields(self, *, state_type: str, context: WorldStateContext) -> list[str]:
        _ = state_type
        return context.signal_fields()

    def _world_state_allowed_state_types(self, *, source_pack: WorldStateSourcePack) -> list[str]:
        allowed: list[str] = []
        for state_type, context_key in WORLD_STATE_CONTEXT_KEYS_BY_TYPE:
            context = source_pack.context(context_key)
            if context is not None:
                allowed.append(state_type)
        return allowed

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
