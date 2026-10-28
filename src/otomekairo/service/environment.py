from __future__ import annotations

from copy import deepcopy
from typing import Any

from otomekairo.observations import ObservationError, validate_environment_config
from otomekairo.service.common import ServiceError
from otomekairo.service.config.locking import serialize_config_update


class ServiceEnvironmentMixin:
    def get_environment_editor_state(self, token: str | None) -> dict[str, Any]:
        return deepcopy(self._require_token(token)["environment_settings"])

    @serialize_config_update
    def replace_environment_editor_state(self, token: str | None, payload: Any) -> dict[str, Any]:
        state = self._require_token(token)
        try:
            definition = validate_environment_config(payload, camera_sources=state["camera_sources"])
        except ObservationError as exc:
            raise ServiceError(exc.status, exc.code, str(exc)) from exc
        state["environment_settings"] = definition
        self.store.write_state(state)
        invalidated = self._environment_runtime.configure(definition)
        if invalidated:
            for memory_set_id in state["memory_sets"]:
                self.store.invalidate_observation_states(memory_set_id=memory_set_id, sources=list(invalidated))
        with self._runtime_state_lock:
            for key in list(self._observation_history):
                if key[1:] in invalidated:
                    del self._observation_history[key]
        return deepcopy(definition)

    def submit_sensor_observations(self, token: str | None, payload: Any) -> dict[str, Any]:
        self._require_token(token)
        received_at = self._now_iso()
        try:
            return self._environment_runtime.submit(payload, received_at=received_at)
        except ObservationError as exc:
            raise ServiceError(exc.status, exc.code, str(exc)) from exc

    def _sensor_environment_inspection(self, *, current_time: str, memory_set_id: str) -> dict[str, Any]:
        observations, reports = self._environment_runtime.snapshot(current_time)
        return {"generated_at": current_time,
                "observations": [o.to_trace_payload() for o in observations],
                "source_reports": [r.to_trace_payload() for r in reports],
                "recent_visual_records": [{key: record[key] for key in (
                    "visual_observation_id", "observed_at", "source_kind", "source_label", "vision_source_id", "detailed_summary_text")}
                    for record in self.store.list_recent_visual_observation_records(memory_set_id=memory_set_id)]}
