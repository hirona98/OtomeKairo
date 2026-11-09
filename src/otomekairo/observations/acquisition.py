from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from otomekairo.observations.context import ObservationContext
from otomekairo.observations.models import Image, MediaBundle, Observation, ObservationSnapshot, SourceReport, observation_expires_at


@dataclass(slots=True)
class ObservationAcquisition:
    memory_set_id: str
    image_max_age_seconds: int
    allocate_id: Callable[[str], str] = field(repr=False)
    media: MediaBundle = field(default_factory=MediaBundle, repr=False)
    images: list[Observation] = field(default_factory=list)
    targets: list[dict[str, Any]] = field(default_factory=list)
    comparisons: list[dict[str, Any]] = field(default_factory=list)
    reports: list[SourceReport] = field(default_factory=list)
    reference_texts: list[tuple[str, str, str]] = field(default_factory=list, repr=False)

    def add_image(self, *, data_uri: str, source_ref: str, source_kind: str, source_label: str,
                  source_owner: str | None, place_ref: str | None, origin_kind: str,
                  observed_at: str, received_at: str, time_basis: str, target: dict[str, Any],
                  comparison: dict[str, Any] | None = None, place_name: str | None = None) -> None:
        observation_id = self.allocate_id("visual_observation")
        media_ref = f"media:{uuid.uuid4().hex}"
        media_type = self.media.add(media_ref, data_uri)
        self.images.append(Observation(
            observation_id, source_ref, None, origin_kind, source_kind, source_label, source_owner,
            place_ref, observed_at, received_at, observation_expires_at(observed_at, self.image_max_age_seconds),
            time_basis, Image(media_ref, media_type),
            tuple((p["person_ref"], p["display_name"]) for p in target.get("observed_persons", [])),
            place_name,
        ))
        self.targets.append(target)
        self.comparisons.append(comparison or {})

    def freeze(self, *, snapshot_at: str, sensor_observations: tuple[Observation, ...],
               sensor_reports: tuple[SourceReport, ...]) -> ObservationContext:
        environment = tuple(o for o in self.images if o.origin_kind == "vision_capture") + sensor_observations
        materials = tuple(o for o in self.images if o.origin_kind != "vision_capture")
        valid_environment = []
        valid_materials = []
        reports = list(self.reports) + list(sensor_reports)
        for observations, valid in ((environment, valid_environment), (materials, valid_materials)):
            for observation in observations:
                if observation.is_fresh(snapshot_at):
                    valid.append(observation)
                else:
                    reports.append(SourceReport(observation.source_ref, observation.channel_id, observation.source_label,
                                                observation.place_ref, "stale", observation.observed_at, None))
        snapshot = ObservationSnapshot(f"snapshot:{uuid.uuid4().hex}", snapshot_at, self.memory_set_id,
                                       tuple(valid_environment), tuple(valid_materials), tuple(reports), self.media,
                                       tuple(self.reference_texts))
        context = ObservationContext(snapshot)
        comparison_by_id = {o.observation_id: c for o, c in zip(self.images, self.comparisons, strict=True)}
        comparisons = [{"observation_ref": ref, **comparison_by_id[o.observation_id]}
                       for ref, o in zip(context.observation_refs(), context.observations, strict=True)
                       if comparison_by_id.get(o.observation_id)]
        return ObservationContext(snapshot, comparisons_json=json.dumps(comparisons, ensure_ascii=False))

    def apply(self, context: ObservationContext, interpretations: list[dict[str, Any]]) -> ObservationContext:
        values = {item["observation_ref"]: item for item in interpretations}
        by_id = {o.observation_id: values[ref] for ref, o in zip(context.observation_refs(), context.observations, strict=True)}
        for observation, target in zip(self.images, self.targets, strict=True):
            if observation.observation_id not in by_id:
                target.update(status="failed", failure_code="stale", image_interpreted=False)
                continue
            value = by_id[observation.observation_id]
            target.update(visual_observation_id=observation.observation_id, observed_at=observation.observed_at,
                          image_interpreted=True, visual_summary_text=value["summary_text"],
                          visual_confidence_hint=value["confidence_hint"], change_state=value["change_state"],
                          change_basis=value["change_basis"], change_reason_summary=value["change_reason_summary"])
        return ObservationContext(context.snapshot,
                                  tuple((ref, json.dumps(value, ensure_ascii=False)) for ref, value in values.items()),
                                  context.comparisons_json)

    def close(self) -> None:
        self.media.close()
