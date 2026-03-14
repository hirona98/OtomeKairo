"""Web composition root."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from otomekairo.boot.compose_sqlite import (
    create_sqlite_adapter_bundle,
    default_db_path,
)
from otomekairo.infra.amivoice_speech_recognizer import AmivoiceSpeechRecognizer
from otomekairo.infra.wifi_camera_sensor import WiFiCameraSensor
from otomekairo.schema.settings import build_default_settings
from otomekairo.web.app import build_app
from otomekairo.web.dependencies import AppServices


# Block: Web composition
def create_app(*, db_path: Path | None = None) -> FastAPI:
    sqlite_bundle = create_sqlite_adapter_bundle(
        db_path=db_path or default_db_path(),
    )
    default_settings = build_default_settings()
    settings_editor_store = sqlite_bundle.settings_editor_store
    services = AppServices(
        runtime_query_store=sqlite_bundle.runtime_query_store,
        cycle_commit_store=sqlite_bundle.cycle_commit_store,
        settings_store=sqlite_bundle.settings_store,
        settings_editor_store=settings_editor_store,
        ui_event_store=sqlite_bundle.ui_event_store,
        default_settings=default_settings,
        camera_sensor=WiFiCameraSensor(
            camera_connections_loader=settings_editor_store.read_enabled_camera_connections,
        ),
        speech_recognizer=AmivoiceSpeechRecognizer(),
    )
    return build_app(services=services)
