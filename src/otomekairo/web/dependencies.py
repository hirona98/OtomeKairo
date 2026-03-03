"""Shared web-layer dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from otomekairo.gateway.camera_sensor import CameraSensor
from otomekairo.infra.sqlite_state_store import SqliteStateStore


# Block: API errors
class ApiError(Exception):
    def __init__(self, *, status_code: int, error_code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


# Block: Shared services
@dataclass(frozen=True, slots=True)
class AppServices:
    store: SqliteStateStore
    default_settings: dict[str, object]
    camera_sensor: CameraSensor
