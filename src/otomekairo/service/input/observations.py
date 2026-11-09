from __future__ import annotations

import json
from typing import Any

from otomekairo.observations.acquisition import ObservationAcquisition
from otomekairo.observations.context import ObservationContext
from otomekairo.observations.models import Image, Measurement, SourceReport
from otomekairo.service.input.source_owner import visual_source_owner


class ServiceInputObservationsMixin:
    def _collect_capture_report(self, *, state, acquisition, source_ref, source_label, status, error_code=None):
        camera = state["camera_sources"].get(source_ref)
        acquisition.reports.append(SourceReport(source_ref, None, source_label,
            camera["place_ref"] if camera is not None else None, status, self._now_iso(), error_code))

    def _new_observation_acquisition(self, state: dict[str, Any]) -> ObservationAcquisition:
        return ObservationAcquisition(
            state["selected_memory_set_id"], state["environment_settings"]["image_max_age_seconds"], self.store.allocate_id,
        )

    def _collect_capture_images(self, *, state: dict[str, Any], acquisition: ObservationAcquisition,
                                capability_response: dict[str, Any], target: dict[str, Any],
                                comparison: dict[str, Any] | None = None) -> None:
        metadata = capability_response["client_context"]
        source_id = metadata["vision_source_id"]
        source_kind = metadata["source_kind"]
        if source_kind == "camera":
            place_ref = state["camera_sources"][source_id]["place_ref"]
        else:
            place_ref = None
        target["image_input_kind"] = "vision_capture_result"
        for image in capability_response["images"]:
            acquisition.add_image(
                data_uri=image, source_ref=source_id, source_kind=source_kind, source_label=metadata["source_label"],
                source_owner=visual_source_owner(source_kind), place_ref=place_ref, origin_kind="vision_capture",
                observed_at=capability_response["captured_at"], received_at=self._now_iso(), time_basis="captured",
                target=target, comparison=comparison,
                place_name=next(p["display_name"] for p in state["environment_settings"]["places"] if p["place_ref"] == place_ref) if place_ref is not None else None,
            )

    def _collect_material_images(self, *, acquisition: ObservationAcquisition, images: list[str],
                                 target: dict[str, Any], origin_kind: str, observed_at: str) -> None:
        target["image_input_kind"] = origin_kind
        for image in images:
            acquisition.add_image(
                data_uri=image, source_ref=f"{origin_kind}:{len(acquisition.images)}", source_kind=origin_kind,
                source_label="会話添付画像" if origin_kind == "conversation_attachment" else "参照画像",
                source_owner=None, place_ref=None, origin_kind=origin_kind, observed_at=observed_at,
                received_at=observed_at, time_basis="received" if origin_kind == "conversation_attachment" else "resolved",
                target=target,
            )

    def _interpret_observation_inputs(self, *, state: dict[str, Any], acquisition: ObservationAcquisition,
                                      current_input: Any, recent_turns: list[dict[str, Any]],
                                      client_context: dict[str, Any], cycle_id: str | None,
                                      include_environment: bool = True) -> tuple[ObservationContext, dict[str, Any]]:
        current_time = self._now_iso()
        sensor_observations, sensor_reports = self._environment_runtime.snapshot(current_time, expected_config=state["environment_settings"])
        context = acquisition.freeze(snapshot_at=current_time, sensor_observations=sensor_observations, sensor_reports=sensor_reports)
        if not include_environment:
            context = context.isolated_view()
        comparisons = json.loads(context.comparisons_json)
        with self._runtime_state_lock:
            for ref, observation in zip(context.observation_refs(), context.observations, strict=True):
                if observation.origin_kind not in {"vision_capture", "sensor_report"}:
                    continue
                previous = self._observation_history.get(self._observation_history_key(context, observation))
                if previous is None:
                    continue
                comparisons = [row for row in comparisons if row["observation_ref"] != ref]
                if previous["identity"] != self._observation_identity(observation):
                    comparisons.append({"observation_ref": ref, "source_identity_changed": True})
                else:
                    comparisons.append({"observation_ref": ref, "previous_observation_context": previous["previous"],
                                        "last_prompted_observation_context": previous.get("last_prompted"),
                                        "same_sample": previous["observation_id"] == observation.observation_id})
        context = ObservationContext(context.snapshot, comparisons_json=json.dumps(comparisons, ensure_ascii=False))
        client_context["observation_snapshot"] = context.to_trace_payload()
        client_context["input_interpretation"] = None
        if context.snapshot.source_reports:
            self.store.invalidate_observation_states(
                memory_set_id=context.snapshot.memory_set_id,
                sources=[(r.source_ref, r.channel_id) for r in context.snapshot.source_reports],
            )
        result = self.llm.generate_input_interpretation(
            model_config=state["model_presets"][state["selected_model_preset_id"]],
            persona_context=self._build_selected_persona_context(state=state, role="input_interpretation"),
            input_text=current_input.text, current_input=current_input,
            recent_turns=self._recall_hint_recent_turns(recent_turns), current_time=current_time,
            observations=context, activity_context=None,
        )
        context = acquisition.apply(context, result["observation_interpretations"])
        values = {item["observation_ref"]: item for item in result["observation_interpretations"]}
        signals = []
        with self._runtime_state_lock:
            for ref, observation in zip(context.observation_refs(), context.observations, strict=True):
                value = values[ref]
                signals.append({"observation_id": observation.observation_id, "source_ref": observation.source_ref,
                                "channel_id": observation.channel_id, "source_kind": observation.source_kind,
                                "source_owner": observation.source_owner, "observed_at": observation.observed_at,
                                "summary_text": value["summary_text"], "change_state": value["change_state"],
                                "change_basis": value["change_basis"], "change_reason_summary": value["change_reason_summary"]})
                if observation.origin_kind in {"vision_capture", "sensor_report"}:
                    key = self._observation_history_key(context, observation)
                    old = self._observation_history.get(key, {})
                    self._observation_history[key] = {
                        "identity": self._observation_identity(observation), "observation_id": observation.observation_id,
                        "previous": {"summary_text": value["summary_text"], "observed_at": observation.observed_at,
                                     "payload": observation.payload.to_payload() if not isinstance(observation.payload, Image) else None},
                        "last_prompted": old.get("last_prompted"),
                    }
        client_context["observation_signals"] = signals
        for summary in client_context.get("wake_observations", []):
            self._record_wake_policy_observation_runtime_state(summary=summary, current_time=current_time)
        wake_observations = client_context.get("wake_observations")
        if isinstance(wake_observations, list):
            client_context["visual_observation_signals"] = self._wake_policy_visual_observation_signals(wake_observations)
            client_context["wake_observation_trace"]["wake_observations"] = wake_observations
        for target in acquisition.targets:
            if target.get("source") == "wake_reference" and target.get("image_interpreted") is True:
                client_context["wake_reference"]["visual_summary_text"] = target["visual_summary_text"]
        client_context["observation_snapshot"] = context.to_trace_payload()
        client_context["input_interpretation"] = result
        if cycle_id is not None:
            self._persist_observation_snapshot(state=state, context=context, acquisition=acquisition,
                                               cycle_id=cycle_id, client_context=client_context)
        return context, result

    @staticmethod
    def _observation_history_key(context, observation):
        return context.snapshot.memory_set_id, observation.source_ref, observation.channel_id

    def _record_observation_speech(self, *, memory_set_id, client_context, speech_text, spoken_at, decision, current_visual_observations):
        if not isinstance(client_context, dict):
            return
        selection = self._execution_outward_decision(decision).get("foreground_selection") or {}
        selected = {selection.get("primary_factor_ref"), *selection.get("supporting_factor_refs", [])}
        selected_ids = {signal["observation_id"] for signal in client_context.get("observation_signals", [])
                        if f"observation_signal:{signal['observation_id']}" in selected}
        selected_ids.update(signal["observation_id"] for signal in client_context.get("observation_signals", [])
                            if signal["observation_id"] in selected)
        selected_ids.update(observation["visual_observation_id"] for index, observation in enumerate(current_visual_observations or [])
                            if f"visual_observation:current:{index}" in selected)
        with self._runtime_state_lock:
            for signal in client_context.get("observation_signals", []):
                if signal["observation_id"] not in selected_ids:
                    continue
                key = memory_set_id, signal["source_ref"], signal["channel_id"]
                history = self._observation_history.get(key)
                if history is not None and history["observation_id"] == signal["observation_id"]:
                    history["last_prompted"] = {**history["previous"], "speech_text": speech_text, "spoken_at": spoken_at}

    @staticmethod
    def _observation_identity(observation):
        return (observation.origin_kind, observation.source_kind, observation.source_owner, observation.place_ref,
                observation.payload.to_payload()["kind"], observation.payload.unit if isinstance(observation.payload, Measurement) else None)

    @staticmethod
    def _compact_observation_signals(value):
        if value is None:
            return []
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError("Observation signals must be an array of objects.")
        fields = ("observation_id", "source_ref", "channel_id", "source_kind", "source_label", "source_owner",
                  "observed_at", "summary_text", "change_state", "change_basis", "change_reason_summary")
        return [{key: item[key] for key in fields if key in item} for item in value]

    def _observation_noop_pipeline(self, *, state, started_at, reason_summary, observations, input_interpretation,
                                    precomputed_states, client_context, observation_summary, trigger_kind):
        result = self._noop_pipeline(state=state, started_at=started_at, reason_summary=reason_summary)
        result.update(observation_snapshot=observations.to_trace_payload(), recall_hint=input_interpretation["recall_hint"],
                      world_state_trace=precomputed_states[0], foreground_world_state=precomputed_states[1],
                      activity_context=precomputed_states[2], activity_trace=precomputed_states[3],
                      current_visual_observations=self._build_current_visual_observations(
                          trigger_kind=trigger_kind, observation_summary=observation_summary,
                          client_context=client_context, observed_at=started_at,
                      ))
        return result

    def _persist_observation_snapshot(self, *, state: dict[str, Any], context: ObservationContext,
                                      acquisition: ObservationAcquisition, cycle_id: str,
                                      client_context: dict[str, Any]) -> None:
        visuals = []
        interpreted = {ref: value for ref, value in zip(context.observation_refs(),
                      context.to_prompt_payload()["surroundings"]["observations"] + context.to_prompt_payload()["input_materials"]["observations"], strict=True)}
        by_id = {o.observation_id: interpreted[ref]["interpretation"] for ref, o in zip(context.observation_refs(), context.observations, strict=True)}
        for observation, target in zip(acquisition.images, acquisition.targets, strict=True):
            if observation.observation_id in by_id:
                value = by_id[observation.observation_id]
                summary = {**target, "visual_observation_id": observation.observation_id,
                           "observed_at": observation.observed_at, "image_interpreted": True,
                           "visual_summary_text": value["summary_text"], "visual_confidence_hint": value["confidence_hint"],
                           "change_state": value["change_state"], "change_basis": value["change_basis"],
                           "change_reason_summary": value["change_reason_summary"]}
                record = self._build_visual_observation_record(
                    cycle_id=cycle_id, memory_set_id=context.snapshot.memory_set_id,
                    observed_at=observation.observed_at, client_context=client_context, observation_summary=summary,
                )
                if record is not None:
                    visuals.append(record)
        events = []
        for observation in context.observations:
            if isinstance(observation.payload, Image):
                continue
            events.append({
                "cycle_id": cycle_id, "memory_set_id": context.snapshot.memory_set_id,
                "kind": "sensor_observation", "role": None, "text": None,
                "created_at": context.snapshot.snapshot_at, "observation": observation.to_trace_payload(),
            })
        self.store.persist_observations(visual_records=visuals, sensor_events=events)
