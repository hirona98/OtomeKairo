"""Shared web-layer dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from otomekairo.gateway.camera_sensor import CameraSensor
from otomekairo.gateway.cycle_commit_store import CycleCommitStore
from otomekairo.gateway.runtime_query_store import RuntimeQueryStore
from otomekairo.gateway.settings_editor_store import SettingsEditorStore
from otomekairo.gateway.settings_store import SettingsStore
from otomekairo.gateway.speech_recognizer import SpeechRecognizer
from otomekairo.gateway.ui_event_store import UiEventStore


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
    runtime_query_store: RuntimeQueryStore
    cycle_commit_store: CycleCommitStore
    settings_store: SettingsStore
    settings_editor_store: SettingsEditorStore
    ui_event_store: UiEventStore
    default_settings: dict[str, Any]
    camera_sensor: CameraSensor
    speech_recognizer: SpeechRecognizer
