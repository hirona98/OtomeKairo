"""Camera still-image sensor abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


# Block: Camera capture response
@dataclass(frozen=True, slots=True)
class CameraCaptureResponse:
    capture_id: str
    image_path: str
    image_url: str
    captured_at: int


# Block: Camera sensor protocol
class CameraSensor(Protocol):
    def is_available(self) -> bool:
        ...

    def capture_still_image(self) -> CameraCaptureResponse:
        ...
