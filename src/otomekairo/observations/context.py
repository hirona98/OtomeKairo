from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from otomekairo.memory.utils import llm_local_time_text
from otomekairo.observations.models import Image, ObservationSnapshot


@dataclass(frozen=True, slots=True)
class ObservationContext:
    snapshot: ObservationSnapshot
    interpretations: tuple[tuple[str, str], ...] = ()
    comparisons_json: str = "[]"
    _interpretation_map: dict[str, Any] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        import json
        object.__setattr__(self, "_interpretation_map", {ref: json.loads(value) for ref, value in self.interpretations})

    @property
    def observations(self):
        return self.snapshot.environment_observations + self.snapshot.input_material_observations

    def isolated_view(self) -> ObservationContext:
        return ObservationContext(ObservationSnapshot(
            self.snapshot.snapshot_id, self.snapshot.snapshot_at, self.snapshot.memory_set_id,
            (), (), (), self.snapshot.media_bundle,
        ))

    def observation_refs(self) -> tuple[str, ...]:
        return tuple(observation.observation_id for observation in self.observations)

    def to_prompt_payload(self) -> dict[str, Any]:
        import json
        comparisons = {row["observation_ref"]: row for row in json.loads(self.comparisons_json)}
        comparison_items = []
        source_refs: dict[str, str] = {}
        def source_ref(source_id):
            if source_id not in source_refs:
                source_refs[source_id] = f"source:{len(source_refs) + 1}"
            return source_refs[source_id]
        items = []
        for ref, observation in zip(self.observation_refs(), self.observations, strict=True):
            item = observation.to_prompt_payload(ref=ref, source_ref=source_ref(observation.source_ref),
                                                 current_time=self.snapshot.snapshot_at)
            from copy import deepcopy
            item["interpretation"] = deepcopy(self._interpretation_map.get(ref))
            items.append(item)
            comparison_items.append({
                "observation_ref": ref,
                "source_identity_changed": False,
                "previous_observation_context": None,
                "last_prompted_observation_context": None,
                **comparisons.get(ref, {}),
            })
        env_count = len(self.snapshot.environment_observations)
        reports = []
        for report in self.snapshot.source_reports:
            reports.append({"source_ref": source_ref(report.source_ref), "channel_id": report.channel_id,
                            "source_label": report.source_label, "place_ref": report.place_ref,
                            "status": report.status, "error_code": report.error_code,
                            "status_time_text": llm_local_time_text(report.status_at) if report.status_at else None})
        return {"surroundings": {"snapshot_ref": self.snapshot.snapshot_id,
                                 "snapshot_time_text": llm_local_time_text(self.snapshot.snapshot_at),
                                 "observations": items[:env_count], "source_reports": reports, "state_summaries": []},
                "input_materials": {"observations": items[env_count:], "reference_texts": [
                    {"ref": f"reference_text:{index}", "label": label, "text": value,
                     "time_text": llm_local_time_text(stamp), "time_basis": "resolved"}
                    for index, (label, value, stamp) in enumerate(self.snapshot.reference_texts, start=1)]},
                "observation_comparisons": comparison_items}

    def image_parts(self) -> list[dict[str, Any]]:
        refs = tuple(o.payload.media_ref for o in self.observations if isinstance(o.payload, Image))
        return self.snapshot.media_bundle.image_parts(refs) if refs else []

    def to_trace_payload(self) -> dict[str, Any]:
        result = self.snapshot.to_trace_payload()
        result["observation_interpretations"] = self.to_prompt_payload()
        result["observation_interpretations"]["input_materials"]["reference_texts"] = [
            {key: value for key, value in item.items() if key != "text"}
            for item in result["observation_interpretations"]["input_materials"]["reference_texts"]]
        return result
