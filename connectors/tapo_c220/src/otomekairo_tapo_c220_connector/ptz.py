from __future__ import annotations

import threading
from typing import Any

from .config import CameraConfig


class PtzError(RuntimeError):
    pass


class TapoPtzController:
    def __init__(self, config: CameraConfig) -> None:
        self.config = config
        self._client: Any | None = None
        self._lock = threading.Lock()

    def move(self, *, operation: str, amount: str) -> None:
        with self._lock:
            vector = self.config.operation_vectors.get(operation)
            if vector is None:
                raise PtzError("unsupported_operation")
            step = self._step_for_amount(amount)
            x = vector[0] * step
            y = vector[1] * step
            response = self._tapo_client().moveMotor(x, y)
            if isinstance(response, dict) and response.get("error_code") == 0:
                return
            if isinstance(response, dict) and "error_code" in response:
                raise PtzError("camera_rejected")
            raise PtzError("unexpected_camera_response")

    def _step_for_amount(self, amount: str) -> int:
        if amount == "small":
            return self.config.small_step
        if amount == "medium":
            return self.config.medium_step
        raise PtzError("unsupported_amount")

    def _tapo_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from pytapo import Tapo  # type: ignore[import-not-found]
        except ImportError as exc:
            raise PtzError("pytapo is not installed.") from exc
        self._client = Tapo(self.config.host, self.config.username, self.config.password)
        return self._client
