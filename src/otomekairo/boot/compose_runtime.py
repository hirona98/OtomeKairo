"""Runtime composition root."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from otomekairo.gateway.settings_editor_store import SettingsEditorStore
from otomekairo.gateway.search_client import SearchClient
from otomekairo.gateway.speech_synthesizer import SpeechSynthesizer
from otomekairo.gateway.camera_controller import CameraController
from otomekairo.gateway.camera_sensor import CameraSensor
from otomekairo.gateway.cognition_client import CognitionClient
from otomekairo.boot.compose_sqlite import (
    create_sqlite_adapter_bundle,
    default_db_path,
)
from otomekairo.runtime.main_loop import (
    DEFAULT_LEASE_HEARTBEAT_MS,
    DEFAULT_LEASE_TTL_MS,
    RuntimeLoop,
    RuntimeStores,
)
from otomekairo.schema.storage_paths import default_tts_audio_dir
from otomekairo.schema.settings import build_default_settings


# Block: Module logger
logger = logging.getLogger(__name__)


# Block: Runtime composition
def create_runtime_loop(*, db_path: Path | None = None) -> RuntimeLoop:
    resolved_db_path = db_path or default_db_path()
    logger.info("initializing runtime loop", extra={"db_path": str(resolved_db_path)})
    sqlite_bundle = create_sqlite_adapter_bundle(db_path=resolved_db_path)
    default_settings = build_default_settings()
    settings_editor_store = sqlite_bundle.settings_editor_store
    return RuntimeLoop(
        stores=RuntimeStores(
            runtime_query_store=sqlite_bundle.runtime_query_store,
            cycle_commit_store=sqlite_bundle.cycle_commit_store,
            settings_store=sqlite_bundle.settings_store,
            settings_editor_store=settings_editor_store,
            ui_event_store=sqlite_bundle.ui_event_store,
            memory_job_store=sqlite_bundle.memory_job_store,
            runtime_lease_store=sqlite_bundle.runtime_lease_store,
            write_memory_unit_of_work=sqlite_bundle.write_memory_unit_of_work,
        ),
        owner_token=_runtime_owner_token(),
        default_settings=default_settings,
        cognition_client=_build_default_cognition_client(),
        search_client=_build_default_search_client(),
        camera_controller=_build_default_camera_controller(
            settings_editor_store=settings_editor_store,
        ),
        camera_sensor=_build_default_camera_sensor(
            settings_editor_store=settings_editor_store,
        ),
        speech_synthesizer=_build_default_speech_synthesizer(),
        lease_heartbeat_ms=DEFAULT_LEASE_HEARTBEAT_MS,
        lease_ttl_ms=DEFAULT_LEASE_TTL_MS,
    )


# Block: Runtime owner token helper
def _runtime_owner_token() -> str:
    return f"runtime_{uuid.uuid4().hex}"


# Block: Cognition client factory
def _build_default_cognition_client() -> CognitionClient:
    from otomekairo.infra.litellm_cognition_client import LiteLLMCognitionClient

    return LiteLLMCognitionClient()


# Block: Search client factory
def _build_default_search_client() -> SearchClient:
    from otomekairo.infra.duckduckgo_search_client import DuckDuckGoSearchClient

    return DuckDuckGoSearchClient()


# Block: Speech synthesizer factory
def _build_default_speech_synthesizer() -> SpeechSynthesizer:
    from otomekairo.infra.aivis_cloud_speech_synthesizer import (
        AivisCloudSpeechSynthesizer,
    )
    from otomekairo.infra.style_bert_vits2_speech_synthesizer import (
        StyleBertVits2SpeechSynthesizer,
    )
    from otomekairo.infra.switching_speech_synthesizer import (
        SwitchingSpeechSynthesizer,
    )
    from otomekairo.infra.voicevox_speech_synthesizer import (
        VoicevoxSpeechSynthesizer,
    )

    audio_output_dir = default_tts_audio_dir()
    return SwitchingSpeechSynthesizer(
        provider_synthesizers={
            "aivis-cloud": AivisCloudSpeechSynthesizer(
                audio_output_dir=audio_output_dir,
            ),
            "voicevox": VoicevoxSpeechSynthesizer(
                audio_output_dir=audio_output_dir,
            ),
            "style-bert-vits2": StyleBertVits2SpeechSynthesizer(
                audio_output_dir=audio_output_dir,
            ),
        }
    )


# Block: Camera controller factory
def _build_default_camera_controller(
    *,
    settings_editor_store: SettingsEditorStore,
) -> CameraController:
    from otomekairo.infra.wifi_camera_controller import WiFiCameraController

    return WiFiCameraController(
        camera_connections_loader=settings_editor_store.read_enabled_camera_connections,
    )


# Block: Camera sensor factory
def _build_default_camera_sensor(
    *,
    settings_editor_store: SettingsEditorStore,
) -> CameraSensor:
    from otomekairo.infra.wifi_camera_sensor import WiFiCameraSensor

    return WiFiCameraSensor(
        camera_connections_loader=settings_editor_store.read_enabled_camera_connections,
    )
