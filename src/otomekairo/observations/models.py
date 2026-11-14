from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from otomekairo.memory.utils import llm_local_time_text, parse_iso

MAX_SNAPSHOT_IMAGES = 8
MAX_SENSOR_OBSERVATIONS = 32
CAPTURE_CLOCK_SKEW_SECONDS = 60


class ObservationError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class MeasurementChange:
    pattern: str
    previous_value: float | int | None
    delta: float | int | None
    elapsed_seconds: float | int | None

    def to_payload(self) -> dict[str, Any]:
        return {"pattern": self.pattern, "previous_value": self.previous_value,
                "delta": self.delta, "elapsed_seconds": self.elapsed_seconds}


@dataclass(frozen=True, slots=True)
class Measurement:
    value: float | int | bool
    unit: str | None
    change: MeasurementChange | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = {"kind": "measurement", "value": self.value, "unit": self.unit}
        if self.change is not None:
            payload["change"] = self.change.to_payload()
        return payload


@dataclass(frozen=True, slots=True)
class EventReport:
    monitoring_started_at: str
    last_occurred_at: str | None

    def to_payload(self) -> dict[str, Any]:
        return {"kind": "event", "monitoring_started_at": self.monitoring_started_at,
                "last_occurred_at": self.last_occurred_at}


@dataclass(frozen=True, slots=True)
class Image:
    media_ref: str
    media_type: str

    def to_payload(self) -> dict[str, Any]:
        return {"kind": "image", "media_ref": self.media_ref, "media_type": self.media_type}


@dataclass(frozen=True, slots=True)
class Observation:
    observation_id: str
    source_ref: str
    channel_id: str | None
    origin_kind: str
    source_kind: str
    source_label: str
    source_owner: str | None
    place_ref: str | None
    observed_at: str
    received_at: str
    expires_at: str
    time_basis: str
    payload: Measurement | EventReport | Image
    observed_persons: tuple[tuple[str, str], ...] = ()
    place_name: str | None = None

    def to_trace_payload(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id, "source_ref": self.source_ref,
            "channel_id": self.channel_id, "origin_kind": self.origin_kind,
            "source_kind": self.source_kind, "source_label": self.source_label,
            "source_owner": self.source_owner, "place_ref": self.place_ref,
            "place_name": self.place_name,
            "observed_at": self.observed_at, "received_at": self.received_at,
            "expires_at": self.expires_at, "time_basis": self.time_basis,
            "observed_persons": [{"person_ref": ref, "display_name": name} for ref, name in self.observed_persons],
            "kind": self.payload.to_payload()["kind"], "payload": self.payload.to_payload(),
        }

    def is_fresh(self, current_time: str) -> bool:
        current = parse_iso(current_time)
        skew = timedelta(seconds=CAPTURE_CLOCK_SKEW_SECONDS if self.origin_kind == "vision_capture" else 0)
        return parse_iso(self.observed_at) <= current + skew and current < parse_iso(self.expires_at)

    def to_prompt_payload(self, *, ref: str, source_ref: str, current_time: str) -> dict[str, Any]:
        payload = self.payload.to_payload()
        if isinstance(self.payload, EventReport):
            payload = {"kind": "event",
                       "monitoring_started_time_text": llm_local_time_text(self.payload.monitoring_started_at),
                       "last_occurred_time_text": (llm_local_time_text(self.payload.last_occurred_at)
                                            if self.payload.last_occurred_at is not None else None)}
        elapsed = int((parse_iso(current_time) - parse_iso(self.observed_at)).total_seconds())
        age_text = f"{elapsed}秒前" if elapsed >= 0 else f"撮影元の時計が{-elapsed}秒先（許容する時計差の範囲内）"
        return {
            "ref": ref, "source_ref": source_ref, "channel_id": self.channel_id,
            "origin_kind": self.origin_kind, "source_kind": self.source_kind,
            "source_label": self.source_label, "source_owner": self.source_owner,
            "place_ref": self.place_ref, "time_basis": self.time_basis,
            "place_name": self.place_name,
            "time_text": llm_local_time_text(self.observed_at),
            "age_text": age_text, "kind": payload["kind"], "payload": payload,
            "interpretation": None,
            "observed_persons": [{"person_ref": person, "display_name": name} for person, name in self.observed_persons],
        }


@dataclass(frozen=True, slots=True)
class SourceReport:
    source_ref: str
    channel_id: str | None
    source_label: str
    place_ref: str | None
    status: str
    status_at: str | None
    error_code: str | None

    def to_trace_payload(self) -> dict[str, Any]:
        return {"source_ref": self.source_ref, "channel_id": self.channel_id,
                "source_label": self.source_label, "place_ref": self.place_ref,
                "status": self.status, "status_at": self.status_at, "error_code": self.error_code}


class MediaBundle:
    """画像を保持する所有オブジェクト。記録用 serializer を持たない。"""

    __slots__ = ("_images", "_closed")

    def __init__(self) -> None:
        self._images: dict[str, str] = {}
        self._closed = False

    def __repr__(self) -> str:
        return f"MediaBundle(image_count={len(self._images)}, closed={self._closed})"

    def add(self, media_ref: str, data_uri: str) -> str:
        if self._closed or media_ref in self._images:
            raise ObservationError("invalid_media_reference", "Media ownership or reference is invalid.")
        header, separator, encoded = data_uri.partition(",")
        if header not in {"data:image/png;base64", "data:image/jpeg;base64",
                          "data:image/webp;base64", "data:image/gif;base64"} or not separator:
            raise ObservationError("invalid_images", "An image data URI is required.")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ObservationError("invalid_images", "Image base64 is invalid.") from exc
        if not decoded:
            raise ObservationError("invalid_images", "Image data must not be empty.")
        self._images[media_ref] = data_uri
        return header[5:-7]

    def image_parts(self, refs: tuple[str, ...]) -> list[dict[str, Any]]:
        if self._closed or len(refs) != len(set(refs)) or any(ref not in self._images for ref in refs):
            raise ObservationError("invalid_media_reference", "Images do not match this media bundle.")
        parts: list[dict[str, Any]] = []
        for ref in refs:
            parts.extend([{"type": "text", "text": f"画像参照: {ref}"},
                          {"type": "image_url", "image_url": {"url": self._images[ref]}}])
        return parts

    def close(self) -> None:
        self._images.clear()
        self._closed = True


@dataclass(frozen=True, slots=True)
class ObservationSnapshot:
    snapshot_id: str
    snapshot_at: str
    memory_set_id: str
    environment_observations: tuple[Observation, ...]
    input_material_observations: tuple[Observation, ...]
    source_reports: tuple[SourceReport, ...]
    media_bundle: MediaBundle = field(repr=False, compare=False)
    reference_texts: tuple[tuple[str, str, str], ...] = ()

    def __post_init__(self) -> None:
        observations = self.environment_observations + self.input_material_observations
        ids = [o.observation_id for o in observations]
        if len(ids) != len(set(ids)):
            raise ObservationError("invalid_observation_snapshot", "Observation references must be unique.")
        images = tuple(o.payload.media_ref for o in observations if isinstance(o.payload, Image))
        if len(images) > MAX_SNAPSHOT_IMAGES or len(observations) - len(images) > MAX_SENSOR_OBSERVATIONS:
            raise ObservationError("observation_limit_exceeded", "Observation snapshot exceeds its limit.")
        if any(not o.is_fresh(self.snapshot_at) for o in observations):
            raise ObservationError("invalid_observation_snapshot", "Snapshot contains an expired or future observation.")
        if images:
            self.media_bundle.image_parts(images)

    def to_trace_payload(self) -> dict[str, Any]:
        return {"snapshot_id": self.snapshot_id, "snapshot_at": self.snapshot_at,
                "memory_set_id": self.memory_set_id,
                "environment_observations": [o.to_trace_payload() for o in self.environment_observations],
                "input_material_observations": [o.to_trace_payload() for o in self.input_material_observations],
                "source_reports": [r.to_trace_payload() for r in self.source_reports]}


def observation_expires_at(observed_at: str, max_age_seconds: int) -> str:
    from otomekairo.memory.utils import local_datetime
    return (local_datetime(observed_at) + timedelta(seconds=max_age_seconds)).isoformat()
