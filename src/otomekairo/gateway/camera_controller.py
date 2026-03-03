"""Camera controller abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# Block: Camera look request
@dataclass(frozen=True, slots=True)
class CameraLookRequest:
    cycle_id: str
    direction: str | None
    preset_id: str | None
    preset_name: str | None


# Block: Camera look response
@dataclass(frozen=True, slots=True)
class CameraLookResponse:
    movement_label: str
    raw_result_ref: dict[str, Any] | None
    adapter_trace_ref: dict[str, Any] | None


# Block: Camera controller protocol
class CameraController(Protocol):
    def is_available(self) -> bool:
        ...

    def move_view(self, request: CameraLookRequest) -> CameraLookResponse:
        ...
