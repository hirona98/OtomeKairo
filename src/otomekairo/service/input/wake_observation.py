from __future__ import annotations

from typing import Any

from otomekairo.llm.client import LLMError
from otomekairo.service.capability import CapabilityDispatchError
from otomekairo.service.common import debug_log
from otomekairo.service.input.source_owner import visual_source_owner


class ServiceInputWakeObservationMixin:
    def _run_wake_policy_observations(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        client_context: dict[str, Any],
        cycle_id: str | None,
    ) -> dict[str, Any]:
        observations = self._enabled_wake_policy_observations(state)
        if not observations:
            return client_context

        cycle_label = self._debug_cycle_label(cycle_id)
        debug_log("Wake", f"{cycle_label} observations start count={len(observations)}", level="DEBUG")
        summaries: list[dict[str, Any]] = []
        for observation in observations:
            summary = self._run_wake_policy_observation(
                state=state,
                started_at=started_at,
                observation=observation,
                cycle_id=cycle_id,
            )
            summary = self._record_wake_policy_observation_runtime_state(
                summary=summary,
                current_time=started_at,
            )
            summaries.append(summary)
        summary_text = self._wake_policy_observation_summary_text(summaries)
        debug_log("Wake", f"{cycle_label} observations done summary={self._clamp(summary_text)}")
        next_context = {
            **client_context,
            "wake_observations": summaries,
            "wake_observation_summary": summary_text,
        }
        visual_signals = self._wake_policy_visual_observation_signals(summaries)
        if visual_signals:
            next_context["visual_observation_signals"] = visual_signals
        return next_context

    def _enabled_wake_policy_observations(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        wake_policy = state.get("wake_policy")
        if not isinstance(wake_policy, dict) or wake_policy.get("mode") != "interval":
            return []
        observations = wake_policy.get("observations")
        if not isinstance(observations, list):
            observations = []
        enabled_observations = [
            observation
            for observation in observations
            if isinstance(observation, dict) and observation.get("enabled") is True
        ]
        return enabled_observations + self._enabled_camera_source_wake_observations(
            state=state,
            existing_observations=enabled_observations,
        )

    def _enabled_camera_source_wake_observations(
        self,
        *,
        state: dict[str, Any],
        existing_observations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        observed_source_ids: set[str] = set()
        for observation in existing_observations:
            input_payload = observation.get("input")
            if not isinstance(input_payload, dict):
                continue
            vision_source_id = input_payload.get("vision_source_id")
            if isinstance(vision_source_id, str) and vision_source_id.strip():
                observed_source_ids.add(vision_source_id.strip())

        camera_sources = state.get("camera_sources")
        if not isinstance(camera_sources, dict):
            return []
        dynamic_observations: list[dict[str, Any]] = []
        for camera_source in sorted(
            camera_sources.values(),
            key=lambda item: str(item.get("vision_source_id") or "") if isinstance(item, dict) else "",
        ):
            if not isinstance(camera_source, dict) or camera_source.get("enabled") is not True:
                continue
            vision_source_id = camera_source.get("vision_source_id")
            if not isinstance(vision_source_id, str) or not vision_source_id.strip():
                continue
            normalized_source_id = vision_source_id.strip()
            if normalized_source_id in observed_source_ids:
                continue
            dynamic_observations.append(
                {
                    "observation_id": self._camera_source_wake_observation_id(normalized_source_id),
                    "enabled": True,
                    "capability_id": "vision.capture",
                    "input": {
                        "vision_source_id": normalized_source_id,
                        "mode": "still",
                    },
                }
            )
        return dynamic_observations

    def _camera_source_wake_observation_id(self, vision_source_id: str) -> str:
        slug = "".join(
            character if character.isalnum() or character in "._-" else "_"
            for character in vision_source_id
        ).strip("_")
        return f"wake_observation:{slug or 'camera'}"

    def _run_wake_policy_observation(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        observation: dict[str, Any],
        cycle_id: str | None,
    ) -> dict[str, Any]:
        observation_id = self._client_context_text(observation.get("observation_id"), limit=96) or "observation:unknown"
        capability_id = self._client_context_text(observation.get("capability_id"), limit=80) or "unknown"
        input_payload = observation.get("input")
        if not isinstance(input_payload, dict):
            return self._wake_policy_observation_failure_summary(
                observation=observation,
                reason_summary="思考前観測 input が不正。",
            )
        resolved_input_payload = self._resolve_wake_policy_observation_input(
            capability_id=capability_id,
            input_payload=input_payload,
        )
        if resolved_input_payload is None:
            return self._wake_policy_observation_failure_summary(
                observation=observation,
                reason_summary="対象 vision source が接続されていない。",
            )
        resolved_observation = {
            **observation,
            "input": resolved_input_payload,
        }
        with self._runtime_state_lock:
            previous_observation_runtime = dict(
                self._wake_observation_runtime_state.get(observation_id.strip(), {})
            )

        try:
            capability_response = self._dispatch_capability_request(
                memory_set_id=state["selected_memory_set_id"],
                capability_id=capability_id,
                input_payload=resolved_input_payload,
                current_time=self._now_iso(),
                goal_summary=f"思考前観測 {observation_id}",
                wait_for_response=True,
                component="WakeObservation",
                track_ongoing_action=False,
            )
        except CapabilityDispatchError as exc:
            return self._wake_policy_observation_failure_summary(
                observation=observation,
                reason_summary=str(exc),
                capability_request_summary=exc.capability_request_summary,
            )
        except ValueError as exc:
            return self._wake_policy_observation_failure_summary(
                observation=observation,
                reason_summary=str(exc),
            )
        if not isinstance(capability_response, dict):
            return self._wake_policy_observation_failure_summary(
                observation=observation,
                reason_summary="capability response が空。",
            )
        return self._apply_wake_policy_observation_result(
            state=state,
            started_at=started_at,
            observation=resolved_observation,
            capability_response=capability_response,
            cycle_id=cycle_id,
            previous_observation_runtime=previous_observation_runtime,
        )

    def _resolve_wake_policy_observation_input(
        self,
        *,
        capability_id: str,
        input_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if capability_id != "vision.capture":
            return dict(input_payload)
        vision_source_id = input_payload.get("vision_source_id")
        if not isinstance(vision_source_id, str) or not vision_source_id.strip():
            return None
        normalized_source_id = vision_source_id.strip()
        if isinstance(self._event_stream_registry.get_vision_source(normalized_source_id), dict):
            return dict(input_payload)

        resolved_source = self._resolve_wake_policy_vision_source_from_stale_id(normalized_source_id)
        if not isinstance(resolved_source, dict):
            return None
        resolved_source_id = resolved_source.get("vision_source_id")
        if not isinstance(resolved_source_id, str) or not resolved_source_id.strip():
            return None
        return {
            **input_payload,
            "vision_source_id": resolved_source_id.strip(),
        }

    def _resolve_wake_policy_vision_source_from_stale_id(self, vision_source_id: str) -> dict[str, Any] | None:
        # CocoroConsole の再生成済み source id は末尾 token と default_for で同じ観測対象へ束ねる。
        stale_kind = vision_source_id.rsplit(":", 1)[-1].strip()
        if stale_kind in {"desktop", "camera", "virtual"}:
            resolved = self._event_stream_registry.find_single_vision_source(
                kind=stale_kind,
                default_for=stale_kind,
            )
            if isinstance(resolved, dict):
                return resolved
            return self._event_stream_registry.find_single_vision_source(kind=stale_kind)
        return None

    def _apply_wake_policy_observation_result(
        self,
        *,
        state: dict[str, Any],
        started_at: str,
        observation: dict[str, Any],
        capability_response: dict[str, Any],
        cycle_id: str | None,
        previous_observation_runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        capability_id = self._capability_result_capability_id(capability_response)
        request_record = capability_response.get("request_record")
        capability_request_summary = self._capability_request_summary(request_record)
        client_context = self._build_capability_result_client_context(capability_response)
        observation_summary = self._capability_result_observation_summary(capability_response)
        input_text = self._build_capability_result_input_text(
            client_context=client_context,
            capability_response=capability_response,
        )
        try:
            client_context, observation_summary, input_text = self._prepare_capability_result_context(
                state=state,
                started_at=started_at,
                capability_id=capability_id,
                client_context=client_context,
                observation_summary=observation_summary,
                input_text=input_text,
                capability_response=capability_response,
                visual_observation_change_context=self._build_visual_observation_change_context(
                    previous_observation_runtime
                ),
            )
            self._refresh_world_state_context(
                state=state,
                started_at=started_at,
                input_text=input_text,
                trigger_kind="capability_result",
                client_context=client_context,
                cycle_id=cycle_id,
                selected_candidate=None,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
                persona_context=self._build_selected_persona_context(state=state, role="world_state"),
            )
            visual_observation_context = self._build_visual_observation_decision_context(
                trigger_kind="capability_result",
                observation_summary=observation_summary,
            )
            self._refresh_activity_context(
                state=state,
                started_at=started_at,
                input_text=input_text,
                current_input=self._build_current_input(
                    input_text=input_text,
                    trigger_kind="capability_result",
                    capability_request_summary=capability_request_summary,
                ).to_prompt_payload(),
                recent_turns=[],
                trigger_kind="capability_result",
                client_context=client_context,
                observation_summary=observation_summary,
                visual_observation_context=visual_observation_context,
                foreground_world_state=None,
                cycle_id=cycle_id,
                cycle_label=self._debug_cycle_label(cycle_id),
                persona_context=self._build_selected_persona_context(state=state, role="activity_state"),
            )
            transition_summary = self._finish_wake_policy_observation_ongoing_action(
                request_record=request_record,
                current_time=self._now_iso(),
                capability_id=capability_id,
                capability_response=capability_response,
                observation_summary=observation_summary,
                failure_reason=None,
            )
            self._apply_capability_runtime_state_followup(
                capability_id=capability_id,
                current_time=self._now_iso(),
                observation_summary=observation_summary,
                result_payload=capability_response,
                ongoing_action_transition_summary=transition_summary,
            )
            return self._wake_policy_observation_success_summary(
                observation=observation,
                capability_response=capability_response,
                observation_summary=observation_summary,
                capability_request_summary=capability_request_summary,
            )
        except (LLMError, KeyError, TypeError, ValueError) as exc:
            transition_summary = self._finish_wake_policy_observation_ongoing_action(
                request_record=request_record,
                current_time=self._now_iso(),
                capability_id=capability_id,
                capability_response=capability_response,
                observation_summary=observation_summary,
                failure_reason=str(exc),
            )
            self._apply_capability_runtime_state_followup(
                capability_id=capability_id,
                current_time=self._now_iso(),
                observation_summary=observation_summary,
                result_payload=capability_response,
                ongoing_action_transition_summary=transition_summary,
                failure_reason=str(exc),
            )
            return self._wake_policy_observation_failure_summary(
                observation=observation,
                reason_summary=str(exc),
                capability_request_summary=capability_request_summary,
            )

    def _finish_wake_policy_observation_ongoing_action(
        self,
        *,
        request_record: Any,
        current_time: str,
        capability_id: str,
        capability_response: dict[str, Any],
        observation_summary: dict[str, Any] | None,
        failure_reason: str | None,
    ) -> dict[str, Any] | None:
        result_error = capability_response.get("error") not in {None, ""} or failure_reason is not None
        terminal_kind = "interrupted" if result_error else "completed"
        terminal_reason = (
            "思考前観測 の取得または反映に失敗した。"
            if result_error
            else "思考前観測 の取得結果を判断材料へ反映した。"
        )
        if result_error:
            final_step_summary = "思考前観測 を中断した。"
        elif self._observation_summary_is_vision_capture(observation_summary):
            final_step_summary = "視覚の思考前観測の結果を視覚記録候補と判断材料へ反映した。"
        else:
            final_step_summary = "思考前観測 の結果を world_state へ反映した。"
        detail_summary = failure_reason or self._capability_result_followup_hint_summary(
            capability_id=capability_id,
            observation_summary=observation_summary,
            result_payload=capability_response,
        )
        return self._finish_capability_ongoing_action(
            request_record=request_record,
            current_time=current_time,
            terminal_kind=terminal_kind,
            reason_code="wake_observation_result",
            terminal_reason=terminal_reason,
            final_step_summary=final_step_summary,
            transition_source="wake_policy_observation",
            result_error=result_error,
            detail_summary=detail_summary,
        )

    def _wake_policy_observation_success_summary(
        self,
        *,
        observation: dict[str, Any],
        capability_response: dict[str, Any],
        observation_summary: dict[str, Any],
        capability_request_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload = self._wake_policy_observation_base_summary(observation)
        error = observation_summary.get("error")
        has_error = isinstance(error, str) and error.strip()
        payload["status"] = "failed" if has_error else "succeeded"
        if has_error:
            payload["reason_summary"] = error.strip()
        request_id = capability_response.get("request_id")
        if isinstance(request_id, str) and request_id.strip():
            payload["request_id"] = request_id.strip()
        for key in (
            "vision_source_id",
            "source_kind",
            "source_label",
            "active_app",
            "window_title",
            "visual_summary_text",
            "change_state",
            "change_basis",
            "change_reason_summary",
            "error",
        ):
            value = observation_summary.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = value.strip()
        image_count = observation_summary.get("image_count")
        if isinstance(image_count, int):
            payload["image_count"] = image_count
        if isinstance(capability_request_summary, dict):
            payload["capability_request_summary"] = capability_request_summary
        return payload

    def _wake_policy_observation_failure_summary(
        self,
        *,
        observation: dict[str, Any],
        reason_summary: str,
        capability_request_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self._wake_policy_observation_base_summary(observation)
        payload["status"] = "failed"
        payload["reason_summary"] = reason_summary.strip()
        if isinstance(capability_request_summary, dict):
            payload["capability_request_summary"] = capability_request_summary
        return payload

    def _wake_policy_observation_base_summary(self, observation: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key, limit in (
            ("observation_id", 96),
            ("capability_id", 80),
        ):
            value = self._client_context_text(observation.get(key), limit=limit)
            if value is not None:
                payload[key] = value
        input_payload = observation.get("input")
        if isinstance(input_payload, dict):
            vision_source_id = self._client_context_text(input_payload.get("vision_source_id"), limit=96)
            if vision_source_id is not None:
                payload["vision_source_id"] = vision_source_id
        return payload

    def _wake_policy_observation_summary_text(self, summaries: list[dict[str, Any]]) -> str:
        if not summaries:
            return "定期観測対象は無い。"
        parts: list[str] = []
        for summary in summaries[:6]:
            label = self._client_context_text(summary.get("source_label"), limit=80)
            if label is None:
                label = self._client_context_text(summary.get("vision_source_id"), limit=96)
            if label is None:
                label = self._client_context_text(summary.get("observation_id"), limit=96) or "unknown"
            if summary.get("status") == "succeeded":
                text = self._client_context_text(summary.get("visual_summary_text"), limit=120)
                if text is None:
                    text = "取得済み"
                parts.append(f"{label}: {text}")
                continue
            reason = self._client_context_text(summary.get("reason_summary"), limit=120) or "取得失敗"
            parts.append(f"{label}: failed {reason}")
        return " / ".join(parts) or "定期観測結果は空。"

    def _build_visual_observation_change_context(
        self,
        previous_runtime: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(previous_runtime, dict) or not previous_runtime:
            return None
        payload: dict[str, Any] = {}
        previous_context = self._wake_observation_runtime_visual_context(
            runtime=previous_runtime,
            summary_key="last_summary",
            observed_at_key="last_success_at",
            source_prefix="last",
        )
        if previous_context is not None:
            payload["previous_observation_context"] = previous_context
        prompted_context = self._wake_observation_runtime_visual_context(
            runtime=previous_runtime,
            summary_key="last_prompted_observation_summary",
            observed_at_key="last_prompted_at",
            source_prefix="last_prompted",
        )
        if prompted_context is not None:
            payload["last_prompted_observation_context"] = prompted_context
        return payload or None

    def _wake_observation_runtime_visual_context(
        self,
        *,
        runtime: dict[str, Any],
        summary_key: str,
        observed_at_key: str,
        source_prefix: str,
    ) -> dict[str, Any] | None:
        summary_text = self._client_context_text(runtime.get(summary_key), limit=240)
        if summary_text is None:
            return None
        payload: dict[str, Any] = {
            "summary_text": summary_text,
        }
        observed_at = self._client_context_text(runtime.get(observed_at_key), limit=64)
        if observed_at is not None:
            payload["observed_at"] = observed_at
        for runtime_key, output_key, limit in (
            (f"{source_prefix}_vision_source_id", "vision_source_id", 96),
            (f"{source_prefix}_source_kind", "source_kind", 32),
            (f"{source_prefix}_source_label", "source_label", 80),
            (f"{source_prefix}_active_app", "active_app", 80),
            (f"{source_prefix}_window_title", "window_title", 120),
        ):
            value = self._client_context_text(runtime.get(runtime_key), limit=limit)
            if value is not None:
                payload[output_key] = value
        return payload

    def _record_wake_policy_observation_runtime_state(
        self,
        *,
        summary: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        observation_id = summary.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id.strip():
            return summary
        status = summary.get("status")
        if not isinstance(status, str) or not status.strip():
            return summary
        normalized_observation_id = observation_id.strip()
        with self._runtime_state_lock:
            previous_runtime = dict(self._wake_observation_runtime_state.get(normalized_observation_id, {}))

        enriched_summary = dict(summary)
        visual_signal = self._build_visual_observation_signal(
            summary=summary,
            previous_runtime=previous_runtime,
            current_time=current_time,
        )
        if visual_signal:
            enriched_summary["visual_observation_signal"] = visual_signal
            for key in ("change_state", "observation_signature"):
                value = visual_signal.get(key)
                if isinstance(value, str) and value.strip():
                    enriched_summary[key] = value.strip()

        last_summary = self._client_context_text(summary.get("visual_summary_text"), limit=160)
        if last_summary is None and status == "succeeded":
            last_summary = self._client_context_text(summary.get("source_label"), limit=80) or "取得済み"
        last_error = self._client_context_text(summary.get("reason_summary"), limit=160)
        if last_error is None:
            last_error = self._client_context_text(summary.get("error"), limit=160)
        payload: dict[str, Any] = {
            "observation_id": normalized_observation_id,
            "last_run_at": current_time,
            "last_status": status.strip(),
            "last_summary": last_summary,
            "last_error": last_error,
            "last_request_id": summary.get("request_id") if isinstance(summary.get("request_id"), str) else None,
            "last_vision_source_id": summary.get("vision_source_id") if isinstance(summary.get("vision_source_id"), str) else None,
            "last_source_kind": summary.get("source_kind") if isinstance(summary.get("source_kind"), str) else None,
            "last_source_label": summary.get("source_label") if isinstance(summary.get("source_label"), str) else None,
            "last_active_app": summary.get("active_app") if isinstance(summary.get("active_app"), str) else None,
            "last_window_title": summary.get("window_title") if isinstance(summary.get("window_title"), str) else None,
            "last_image_count": summary.get("image_count") if isinstance(summary.get("image_count"), int) else None,
        }
        for key in (
            "last_success_at",
            "last_observation_signature",
            "same_observation_count",
            "last_prompted_observation_signature",
            "last_prompted_observation_summary",
            "last_prompted_vision_source_id",
            "last_prompted_source_kind",
            "last_prompted_source_label",
            "last_prompted_active_app",
            "last_prompted_window_title",
            "last_prompted_at",
        ):
            if key not in payload and key in previous_runtime:
                payload[key] = previous_runtime[key]
        self._apply_visual_observation_runtime_payload(
            payload=payload,
            summary=enriched_summary,
            previous_runtime=previous_runtime,
            current_time=current_time,
        )
        with self._runtime_state_lock:
            self._wake_observation_runtime_state[normalized_observation_id] = payload
        return enriched_summary

    def _build_visual_observation_signal(
        self,
        *,
        summary: dict[str, Any],
        previous_runtime: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any] | None:
        _ = current_time
        if not self._wake_observation_is_vision_capture(summary):
            return None
        observation_signature = self._visual_observation_signature(summary)
        if observation_signature is None:
            return None
        change_state = self._client_context_text(summary.get("change_state"), limit=48)
        change_basis = self._client_context_text(summary.get("change_basis"), limit=48)
        reason_summary = self._client_context_text(summary.get("change_reason_summary"), limit=180)
        if change_state not in {"first_seen", "changed", "stable", "same_as_recent_speech"}:
            return None
        if change_basis not in {
            "no_previous_observation",
            "semantic_change",
            "semantic_stability",
            "recent_speech_repetition",
            "source_identity_changed",
        }:
            return None
        if reason_summary is None:
            return None

        previous_present = self._previous_visual_observation_runtime_present(previous_runtime)
        source_identity_change = self._visual_observation_source_identity_change(
            summary=summary,
            previous_runtime=previous_runtime,
        )
        if not previous_present:
            change_state = "first_seen"
            change_basis = "no_previous_observation"
            reason_summary = "同じ思考前観測の前回成功記録が無いため初回観測として扱う。"
        elif source_identity_change is not None:
            change_state = "changed"
            change_basis = "source_identity_changed"
            reason_summary = f"観測 source の {source_identity_change} が前回と異なる。"

        signal: dict[str, Any] = {
            "observation_id": summary.get("observation_id"),
            "change_state": change_state,
            "reason_summary": reason_summary,
            "observation_signature": observation_signature,
            "same_as_recent_speech": change_state == "same_as_recent_speech",
            "summary_text": self._client_context_text(summary.get("visual_summary_text"), limit=160),
            "vision_source_id": self._client_context_text(summary.get("vision_source_id"), limit=96),
            "source_kind": self._client_context_text(summary.get("source_kind"), limit=32),
            "source_label": self._client_context_text(summary.get("source_label"), limit=80),
            "active_app": self._client_context_text(summary.get("active_app"), limit=80),
            "window_title": self._client_context_text(summary.get("window_title"), limit=120),
            "change_basis": change_basis,
        }
        source_owner = visual_source_owner(signal.get("source_kind"))
        if source_owner is not None:
            signal["source_owner"] = source_owner
        compact_signal = {key: value for key, value in signal.items() if value is not None}
        self._debug_log_visual_observation_change(
            signal=compact_signal,
            previous_present=previous_present,
            prompted_present=self._prompted_visual_observation_runtime_present(previous_runtime),
        )
        return compact_signal

    def _apply_visual_observation_runtime_payload(
        self,
        *,
        payload: dict[str, Any],
        summary: dict[str, Any],
        previous_runtime: dict[str, Any],
        current_time: str,
    ) -> None:
        signal = summary.get("visual_observation_signal")
        if not isinstance(signal, dict):
            return
        observation_signature = self._client_context_text(signal.get("observation_signature"), limit=360)
        if observation_signature is None:
            return
        same_count = previous_runtime.get("same_observation_count")
        if not isinstance(same_count, int) or same_count < 0:
            same_count = 0
        change_state = self._client_context_text(signal.get("change_state"), limit=48)
        payload["last_success_at"] = current_time
        payload["last_observation_signature"] = observation_signature
        if change_state in {"stable", "same_as_recent_speech"}:
            payload["same_observation_count"] = same_count + 1
        else:
            payload["same_observation_count"] = 1
        for key in (
            "last_prompted_observation_signature",
            "last_prompted_observation_summary",
            "last_prompted_vision_source_id",
            "last_prompted_source_kind",
            "last_prompted_source_label",
            "last_prompted_active_app",
            "last_prompted_window_title",
            "last_prompted_at",
        ):
            value = previous_runtime.get(key)
            if isinstance(value, str) and value.strip():
                payload[key] = value.strip()

    def _wake_observation_is_vision_capture(self, summary: dict[str, Any]) -> bool:
        return (
            summary.get("status") == "succeeded"
            and summary.get("capability_id") == "vision.capture"
        )

    def _visual_observation_signature(self, summary: dict[str, Any]) -> str | None:
        parts: list[str] = []
        for key in ("vision_source_id", "source_kind", "source_label", "visual_summary_text"):
            value = self._client_context_text(summary.get(key), limit=160)
            if value is not None:
                parts.append(f"{key}={value}")
        if not parts:
            return None
        normalized = " | ".join(" ".join(part.lower().split()) for part in parts)
        return normalized

    def _previous_visual_observation_runtime_present(
        self,
        previous_runtime: dict[str, Any],
    ) -> bool:
        return (
            previous_runtime.get("last_status") == "succeeded"
            and isinstance(previous_runtime.get("last_success_at"), str)
            and isinstance(previous_runtime.get("last_summary"), str)
            and bool(str(previous_runtime["last_summary"]).strip())
        )

    def _prompted_visual_observation_runtime_present(
        self,
        previous_runtime: dict[str, Any],
    ) -> bool:
        prompted_summary = previous_runtime.get("last_prompted_observation_summary")
        return isinstance(prompted_summary, str) and bool(prompted_summary.strip())

    def _visual_observation_source_identity_change(
        self,
        *,
        summary: dict[str, Any],
        previous_runtime: dict[str, Any],
    ) -> str | None:
        for summary_key, runtime_key, label in (
            ("vision_source_id", "last_vision_source_id", "vision_source_id"),
            ("source_kind", "last_source_kind", "source_kind"),
        ):
            current_value = self._client_context_text(summary.get(summary_key), limit=120)
            previous_value = self._client_context_text(previous_runtime.get(runtime_key), limit=120)
            if current_value is not None and previous_value is not None and current_value != previous_value:
                return label
        return None

    def _debug_log_visual_observation_change(
        self,
        *,
        signal: dict[str, Any],
        previous_present: bool,
        prompted_present: bool,
    ) -> None:
        debug_log(
            "Wake",
            (
                "visual observation change "
                f"state={signal.get('change_state')} basis={signal.get('change_basis')} "
                f"previous={'present' if previous_present else 'missing'} "
                f"prompted={'present' if prompted_present else 'missing'} "
                f"source={signal.get('source_kind') or '-'}"
            ),
        )

    def _wake_policy_visual_observation_signals(self, summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        signals: list[dict[str, Any]] = []
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            signal = summary.get("visual_observation_signal")
            if isinstance(signal, dict) and signal:
                signals.append(dict(signal))
        return signals

    def _compact_visual_observation_signal(self, signal: Any) -> dict[str, Any] | None:
        if not isinstance(signal, dict):
            return None
        payload: dict[str, Any] = {}
        for key, limit in (
            ("observation_id", 96),
            ("change_state", 48),
            ("change_basis", 48),
            ("reason_summary", 180),
            ("summary_text", 160),
            ("vision_source_id", 96),
            ("source_kind", 32),
            ("source_label", 80),
            ("source_owner", 32),
            ("active_app", 80),
            ("window_title", 120),
        ):
            value = self._client_context_text(signal.get(key), limit=limit)
            if value is not None:
                payload[key] = value
        same_as_recent_speech = signal.get("same_as_recent_speech")
        if isinstance(same_as_recent_speech, bool):
            payload["same_as_recent_speech"] = same_as_recent_speech
        return payload or None

    def _compact_visual_observation_signals(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        signals: list[dict[str, Any]] = []
        for item in value[:6]:
            compact = self._compact_visual_observation_signal(item)
            if compact:
                signals.append(compact)
        return signals
