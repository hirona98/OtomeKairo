"""Camera controller abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# Block: Camera look request
@dataclass(frozen=True, slots=True)
class CameraLookRequest:
    cycle_id: str
    camera_connection_id: str
    direction: str | None
    preset_id: str | None
    preset_name: str | None


# Block: Camera look response
@dataclass(frozen=True, slots=True)
class CameraLookResponse:
    camera_connection_id: str
    camera_display_name: str
    movement_label: str
    raw_result_ref: dict[str, Any] | None
    adapter_trace_ref: dict[str, Any] | None


# Block: Camera preset candidate
@dataclass(frozen=True, slots=True)
class CameraPresetCandidate:
    preset_id: str
    preset_name: str


# Block: Camera candidate
@dataclass(frozen=True, slots=True)
class CameraCandidate:
    camera_connection_id: str
    display_name: str
    can_look: bool
    can_capture: bool
    presets: tuple[CameraPresetCandidate, ...]


# Block: Camera controller protocol
class CameraController(Protocol):
    def is_available(self) -> bool:
        ...

    def list_candidates(self) -> list[CameraCandidate]:
        ...

    def move_view(self, request: CameraLookRequest) -> CameraLookResponse:
        ...
