"""Camera still-image sensor abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


# Block: Camera capture request
@dataclass(frozen=True, slots=True)
class CameraCaptureRequest:
    camera_connection_id: str | None


# Block: Camera capture response
@dataclass(frozen=True, slots=True)
class CameraCaptureResponse:
    camera_connection_id: str
    camera_display_name: str
    capture_id: str
    image_path: str
    image_url: str
    captured_at: int


# Block: Camera sensor protocol
class CameraSensor(Protocol):
    def is_available(self) -> bool:
        ...

    def capture_still_image(self, request: CameraCaptureRequest) -> CameraCaptureResponse:
        ...
