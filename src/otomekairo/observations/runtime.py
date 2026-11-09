from __future__ import annotations

import json
import math
import threading
import uuid
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from otomekairo.memory.utils import local_datetime, parse_iso, source_text_hash
from otomekairo.store.identifiers import InternalIdentityConflictError
from otomekairo.observations.models import (
    EventReport, Measurement, Observation, ObservationError, SourceReport,
    MAX_SENSOR_OBSERVATIONS, observation_expires_at,
)
from otomekairo.observations.validation import exact_fields, nonempty


@dataclass(frozen=True, slots=True)
class SensorSample:
    sample_id: str
    occurred_at: str
    fingerprint: str
    observation: Observation | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class PreparedSensorSample:
    sample_id: str
    occurred_at: str
    fingerprint: str
    payload: Measurement | EventReport | None
    error_code: str | None


class EnvironmentRuntime:
    def __init__(self, config: dict[str, Any], *, resolve_ids: Callable[[list[tuple[str, str]]], list[str]]) -> None:
        self._resolve_ids = resolve_ids
        self._lock = threading.RLock()
        self._revision = 0
        self._sources: dict[str, dict[str, Any]] = {}
        self._samples: dict[tuple[str, str], SensorSample] = {}
        self._event_order: dict[tuple[str, str], Observation] = {}
        self.configure(config)

    def configure(self, config: dict[str, Any]) -> set[tuple[str, str]]:
        sources = {s["sensor_source_id"]: deepcopy(s) for s in config["sensor_sources"]}
        invalidated: set[tuple[str, str]] = set()
        with self._lock:
            for key in list(self._samples):
                source_id, channel_id = key
                old = self._sources[source_id]
                new = sources.get(source_id)
                old_identity = {k: v for k, v in old.items() if k not in {"display_name", "channels"}}
                new_identity = ({k: v for k, v in new.items() if k not in {"display_name", "channels"}}
                                if new is not None else None)
                old_channel = next(c for c in old["channels"] if c["channel_id"] == channel_id)
                new_channel = (next((c for c in new["channels"] if c["channel_id"] == channel_id), None)
                               if new is not None else None)
                if old_identity != new_identity or new_channel is None or any(
                    old_channel[k] != new_channel[k] for k in ("kind", "value_type", "unit")
                ):
                    del self._samples[key]
                    self._event_order.pop(key, None)
                    invalidated.add(key)
            self._sources = sources
            self._config = deepcopy(config)
            self._revision += 1
        return invalidated

    def submit(self, body: Any, *, received_at: str) -> dict[str, Any]:
        code = "invalid_sensor_samples"
        exact_fields(body, {"client_id", "sensor_source_id", "samples"}, code)
        source_id = nonempty(body["sensor_source_id"], code, "sensor_source:")
        with self._lock:
            revision = self._revision
            source = deepcopy(self._sources.get(source_id))
            places = {p["place_ref"]: p["display_name"] for p in self._config["places"]}
        if source is None:
            raise ObservationError("sensor_source_not_found", "Sensor source is not registered.", 404)
        if not source["enabled"]:
            raise ObservationError("sensor_source_disabled", "Sensor source is disabled.", 409)
        if body["client_id"] != source["producer_client_id"]:
            raise ObservationError("sensor_source_client_mismatch", "Sensor producer does not match.", 409)
        raw_samples = body["samples"]
        if not isinstance(raw_samples, list) or not 1 <= len(raw_samples) <= MAX_SENSOR_OBSERVATIONS:
            raise ObservationError(code, "Sensor samples must be a non-empty bounded array.")
        channels = {c["channel_id"]: c for c in source["channels"]}
        prepared: dict[str, PreparedSensorSample] = {}
        for raw in raw_samples:
            exact_fields(raw, {"sample_id", "channel_id", "occurred_at", "status", "payload", "error_code"}, code)
            channel_id = nonempty(raw["channel_id"], code)
            if channel_id in prepared:
                raise ObservationError(code, "A channel may appear only once per request.")
            if channel_id not in channels:
                raise ObservationError("invalid_sensor_channel", "Sensor channel is not registered.")
            try:
                sample_id = str(uuid.UUID(raw["sample_id"]))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ObservationError(code, "Sensor sample ID must be a UUID.") from exc
            occurred = self._timestamp(raw["occurred_at"])
            if parse_iso(occurred) > parse_iso(received_at):
                raise ObservationError("invalid_observation_time", "Sensor observation is in the future.")
            payload = None
            if raw["status"] == "failed":
                if raw["payload"] is not None or not isinstance(raw["error_code"], str) or raw["error_code"] not in {
                    "source_unavailable", "acquisition_failed", "invalid_device_value"
                }:
                    raise ObservationError("invalid_sensor_value", "Failed report must contain only its error code.")
            elif raw["status"] == "succeeded" and raw["error_code"] is None:
                payload = self._payload(raw["payload"], channels[channel_id], occurred)
            else:
                raise ObservationError("invalid_sensor_value", "Sensor report status is invalid.")
            prepared[channel_id] = PreparedSensorSample(
                sample_id, occurred, json.dumps(raw, sort_keys=True, ensure_ascii=False, allow_nan=False),
                payload, raw["error_code"],
            )
        with self._lock:
            if revision != self._revision:
                raise ObservationError("sensor_source_changed", "Sensor configuration changed while validating.", 409)
            acceptance_by_channel = {}
            for channel_id, sample in prepared.items():
                previous = self._samples.get((source_id, channel_id))
                acceptance = "accepted"
                if previous is not None:
                    if previous.sample_id == sample.sample_id:
                        if previous.fingerprint != sample.fingerprint:
                            raise ObservationError("sensor_sample_conflict", "Sample ID was reused with different data.", 409)
                        acceptance = "duplicate"
                    elif parse_iso(sample.occurred_at) <= parse_iso(previous.occurred_at):
                        raise ObservationError("sensor_sample_out_of_order", "Sensor report is not newer than the latest report.", 409)
                    if acceptance == "accepted":
                        self._validate_event_order(self._event_order.get((source_id, channel_id)), sample.payload)
                acceptance_by_channel[channel_id] = acceptance
            successful = [(channel_id, sample) for channel_id, sample in prepared.items() if sample.payload is not None]
            identities = [(json.dumps([source_id, channel_id, sample.sample_id], separators=(",", ":")),
                           source_text_hash(sample.fingerprint)) for channel_id, sample in successful]
            try:
                identifiers = self._resolve_ids(identities)
            except InternalIdentityConflictError as exc:
                raise ObservationError("sensor_sample_conflict", "Sample ID was reused with different data.", 409) from exc
            observations = {}
            for (channel_id, sample), observation_id in zip(successful, identifiers, strict=True):
                observations[channel_id] = Observation(
                    observation_id=observation_id,
                    source_ref=source_id, channel_id=channel_id, origin_kind="sensor_report",
                    source_kind="sensor", source_label=f"{source['display_name']} / {channels[channel_id]['display_name']}",
                    source_owner=source["source_owner"], place_ref=source["place_ref"], observed_at=sample.occurred_at,
                    received_at=received_at, expires_at=observation_expires_at(sample.occurred_at, source["max_age_seconds"]),
                    time_basis="reported" if isinstance(sample.payload, EventReport) else "measured", payload=sample.payload,
                    place_name=places[source["place_ref"]] if source["place_ref"] is not None else None,
                )
            results = []
            for channel_id, sample in prepared.items():
                observation = observations.get(channel_id)
                results.append({"sample_id": sample.sample_id, "channel_id": channel_id,
                                "acceptance": acceptance_by_channel[channel_id],
                                "observation_id": observation.observation_id if observation is not None else None})
                if acceptance_by_channel[channel_id] == "accepted":
                    key = (source_id, channel_id)
                    self._samples[key] = SensorSample(
                        sample.sample_id, sample.occurred_at, sample.fingerprint, observation, sample.error_code,
                    )
                    if observation is not None and isinstance(observation.payload, EventReport):
                        self._event_order[key] = observation
            return {"sensor_source_id": source_id, "results": results}

    @staticmethod
    def _timestamp(value: Any) -> str:
        try:
            if not isinstance(value, str):
                raise ValueError("timestamp must be a string")
            return local_datetime(value).isoformat()
        except (ValueError, TypeError, OverflowError) as exc:
            raise ObservationError("invalid_observation_time", "A timestamp with timezone offset is required.") from exc

    def _payload(self, payload: Any, channel: dict[str, Any], occurred: str) -> Measurement | EventReport:
        code = "invalid_sensor_value"
        if channel["kind"] == "measurement":
            exact_fields(payload, {"kind", "value", "unit"}, code)
            value = payload["value"]
            try:
                valid_type = (type(value) in {int, float} and math.isfinite(value)
                              if channel["value_type"] == "number" else type(value) is bool)
            except OverflowError:
                valid_type = False
            if payload["kind"] != "measurement" or not valid_type or payload["unit"] != channel["unit"]:
                raise ObservationError(code, "Measurement value or unit does not match the registered channel.")
            return Measurement(value, payload["unit"])
        exact_fields(payload, {"kind", "monitoring_started_at", "last_occurred_at"}, code)
        if payload["kind"] != "event":
            raise ObservationError(code, "Event payload kind is invalid.")
        started = self._timestamp(payload["monitoring_started_at"])
        last = self._timestamp(payload["last_occurred_at"]) if payload["last_occurred_at"] is not None else None
        if parse_iso(started) > parse_iso(occurred) or (last is not None and not (
            parse_iso(started) <= parse_iso(last) <= parse_iso(occurred)
        )):
            raise ObservationError("invalid_observation_time", "Event monitoring and occurrence times are inconsistent.")
        return EventReport(started, last)

    @staticmethod
    def _validate_event_order(previous: Observation | None, current: Measurement | EventReport | None) -> None:
        if previous is None or current is None or not isinstance(previous.payload, EventReport):
            return
        old, new = previous.payload, current
        if not isinstance(new, EventReport):
            raise ObservationError("invalid_sensor_value", "Event channel kind changed.")
        if parse_iso(new.monitoring_started_at) < parse_iso(old.monitoring_started_at) or (
            new.monitoring_started_at == old.monitoring_started_at and old.last_occurred_at is not None
            and (new.last_occurred_at is None or parse_iso(new.last_occurred_at) < parse_iso(old.last_occurred_at))
        ):
            raise ObservationError("invalid_observation_time", "Event history cannot move backwards.")

    def snapshot(self, current_time: str, *, expected_config: dict[str, Any] | None = None) -> tuple[tuple[Observation, ...], tuple[SourceReport, ...]]:
        observations: list[Observation] = []
        reports: list[SourceReport] = []
        with self._lock:
            if expected_config is not None and expected_config != self._config:
                raise ObservationError("sensor_source_changed", "Environment configuration changed during the cycle.", 409)
            for source_id, source in self._sources.items():
                if not source["enabled"]:
                    continue
                for channel in source["channels"]:
                    channel_id = channel["channel_id"]
                    sample = self._samples.get((source_id, channel_id))
                    if sample is not None and sample.observation is not None and sample.observation.is_fresh(current_time):
                        observations.append(sample.observation)
                        continue
                    status = "not_observed" if sample is None else "failed" if sample.observation is None else "stale"
                    reports.append(SourceReport(
                        source_id, channel_id, f"{source['display_name']} / {channel['display_name']}",
                        source["place_ref"], status, sample.occurred_at if sample else None,
                        sample.error_code if sample else None,
                    ))
        return tuple(observations), tuple(reports)
