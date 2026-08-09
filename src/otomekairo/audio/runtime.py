from __future__ import annotations

import json
import math
import threading
import time
import unicodedata
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from otomekairo.audio.amivoice import AmiVoiceClient, AmiVoiceError, AmiVoiceResult
from otomekairo.audio.models import (
    AudioModelError,
    AudioModelRuntime,
    SpeakerIdentification,
)
from otomekairo.audio.segmenter import AudioSegmenter, FRAME_BYTES, SegmentedUtterance
from otomekairo.event_stream import ServerWebSocket, WebSocketProtocolError
from otomekairo.memory.utils import local_now, now_iso
from otomekairo.service.common import ServiceError, debug_log


AUDIO_PROTOCOL_VERSION = "2"
HEARTBEAT_INTERVAL_SECONDS = 5
LEASE_TIMEOUT_SECONDS = 15
ENROLLMENT_TIMEOUT_SECONDS = 120
NORMAL_ACTIVE_SECONDS = 60
MAX_WAITING_UTTERANCES = 4
ENROLLMENT_REQUIRED_SAMPLES = 3
ENROLLMENT_MINIMUM_SAMPLES = 32000

AUDIO_FORMAT = {
    "sample_rate": 16000,
    "channels": 1,
    "sample_format": "pcm_s16le",
    "frame_duration_ms": 20,
    "bytes_per_frame": FRAME_BYTES,
}


@dataclass
class AudioConnection:
    session_id: str
    websocket: ServerWebSocket
    endpoint_source: str
    client_id: str | None = None
    lease_generation: int | None = None
    last_heartbeat_monotonic: float | None = None
    last_heartbeat_at: str | None = None
    device: dict[str, str] | None = None
    capture_settings: dict[str, Any] | None = None


@dataclass(frozen=True)
class QueuedUtterance:
    utterance_seq: int
    source: str
    response_client_id: str
    lease_generation: int
    settings_generation: int
    work_generation: int
    explicit_input: bool
    utterance: SegmentedUtterance


class AudioRuntime:
    def __init__(self, service: Any) -> None:
        self._service = service
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._connections: dict[str, AudioConnection] = {}
        self._active_connection: AudioConnection | None = None
        self._lease_generation = 0
        self._settings_generation = 1
        self._work_generation = 1
        self._utterance_seq = 0
        self._segmenter: AudioSegmenter | None = None
        self._waiting: deque[QueuedUtterance] = deque()
        self._processing: QueuedUtterance | None = None
        self._paused_reason: str | None = None
        self._device_catalog_client_id: str | None = None
        self._device_catalog: list[dict[str, Any]] = []
        self._web_input_session: dict[str, str] | None = None
        self._enrollment: dict[str, Any] | None = None
        self._normal_active_until_monotonic: float | None = None
        self._normal_active_until: str | None = None
        self._last_utterance_result: dict[str, Any] | None = None
        self._vad_probability: float | None = None
        self._vad_speaking = False
        self._dbfs: float | None = None
        self._last_metric_publish_monotonic = 0.0
        self._last_published_fingerprint: str | None = None
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._parallel_executor: ThreadPoolExecutor | None = None
        self._amivoice = AmiVoiceClient()
        self._settings = self._read_audio_settings()

        try:
            self._models = AudioModelRuntime()
            self._available = True
            self._unavailable_reason: str | None = None
            debug_log(
                "Audio",
                (
                    f"model runtime ready vad={self._models.silero_model_id} "
                    f"speaker={self._models.speaker_model_id}"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._models = None
            self._available = False
            self._unavailable_reason = f"{type(exc).__name__}: {exc}"
            debug_log(
                "Audio",
                f"model runtime unavailable reason={type(exc).__name__}",
                level="ERROR",
            )

    def close(self) -> None:
        # 音声専用threadと接続を停止し、server全体のshutdownへ合流する。
        self._stop_event.set()
        with self._condition:
            connections = list(self._connections.values())
            self._connections.clear()
            self._active_connection = None
            self._condition.notify_all()
        for connection in connections:
            connection.websocket.close()
        for thread in (self._worker_thread, self._watchdog_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=5.0)
        if self._parallel_executor is not None:
            self._parallel_executor.shutdown(wait=False, cancel_futures=True)

    def register_connection(
        self,
        websocket: ServerWebSocket,
        *,
        endpoint_source: str,
    ) -> str:
        if endpoint_source not in {
            "local_microphone",
            "console_microphone",
            "web_microphone",
        }:
            raise ValueError("Unknown audio endpoint source.")
        session_id = f"audio_stream_session:{uuid.uuid4().hex}"
        with self._lock:
            self._ensure_threads_locked()
            self._connections[session_id] = AudioConnection(
                session_id=session_id,
                websocket=websocket,
                endpoint_source=endpoint_source,
            )
        return session_id

    def unregister_connection(self, session_id: str) -> None:
        publish = False
        with self._condition:
            connection = self._connections.pop(session_id, None)
            if connection is None:
                return
            if connection.endpoint_source == "local_microphone":
                publish = True
            if self._active_connection is connection:
                self._release_active_locked()
                publish = True
            if (
                connection.endpoint_source == "web_microphone"
                and self._web_input_session is not None
                and connection.client_id
                == self._web_input_session["owner_client_id"]
            ):
                self._web_input_session = None
                publish = True
            if (
                self._enrollment is not None
                and connection.endpoint_source == "web_microphone"
                and connection.client_id == self._enrollment["owner_client_id"]
            ):
                self._cancel_enrollment_locked()
                publish = True
        if publish:
            self._publish_state(force=True)

    def handle_message(
        self,
        session_id: str,
        message_kind: str,
        message_payload: bytes,
    ) -> None:
        with self._lock:
            connection = self._connections.get(session_id)
        if connection is None:
            raise WebSocketProtocolError("Audio stream session does not exist.")
        if message_kind == "binary":
            self._handle_binary(connection, message_payload)
            return
        if message_kind != "text":
            raise WebSocketProtocolError("Unsupported audio message kind.")
        try:
            payload = json.loads(message_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ServiceError(
                400,
                "invalid_audio_control",
                "Audio control message must be a JSON object.",
            ) from exc
        if not isinstance(payload, dict):
            raise ServiceError(
                400,
                "invalid_audio_control",
                "Audio control message must be a JSON object.",
            )
        message_type = payload.get("type")
        if message_type == "audio_start":
            self._handle_audio_start(connection, payload)
        elif message_type == "audio_heartbeat":
            self._handle_heartbeat(connection, payload)
        elif message_type == "audio_stop":
            self._handle_audio_stop(connection, payload)
        elif message_type == "audio_device_catalog":
            self._handle_device_catalog(connection, payload)
        else:
            raise ServiceError(
                400,
                "invalid_audio_control",
                "Audio control message type is unsupported.",
            )

    def send_error(
        self,
        session_id: str,
        *,
        code: str,
        message: str,
    ) -> None:
        with self._lock:
            connection = self._connections.get(session_id)
        if connection is not None:
            connection.websocket.send_json(
                {
                    "type": "audio_error",
                    "code": code,
                    "message": message,
                }
            )

    def reload_settings(self) -> None:
        new_settings = self._read_audio_settings()
        with self._condition:
            if new_settings == self._settings:
                return
            self._settings = new_settings
            self._settings_generation += 1
            # Web入力sessionは開始時の保存済み入力元に固定するため、
            # 音声設定が変わった時点で終了し、旧設定の所有権を残さない。
            self._web_input_session = None
            active = self._active_connection
            if active is not None:
                active.websocket.send_json(
                    {
                        "type": "audio_paused",
                        "lease_generation": active.lease_generation,
                        "reason": "settings_reloaded",
                    }
                )
                active.lease_generation = None
            self._active_connection = None
            self._paused_reason = None
            self._discard_audio_locked()
        self._publish_state(force=True)

    def on_event_client_disconnected(self, client_id: str) -> None:
        with self._condition:
            if (
                self._enrollment is not None
                and self._enrollment["owner_client_id"] == client_id
            ):
                self._cancel_enrollment_locked()
            if (
                self._web_input_session is not None
                and self._web_input_session["owner_client_id"] == client_id
            ):
                self._web_input_session = None
                self._revoke_active_locked("source_switched")
            active = self._active_connection
            if (
                active is not None
                and self._response_client_id_locked(active) == client_id
            ):
                self._set_paused_locked("response_client_unavailable")
                self._discard_audio_locked()
        self._publish_state(force=True)

    def on_event_client_connected(self, client_id: str) -> None:
        # hello確定後にresponse client条件を再評価する。
        with self._condition:
            active = self._active_connection
            if (
                active is not None
                and self._response_client_id_locked(active) == client_id
            ):
                self._refresh_pause_state_locked()
        self._publish_state(force=True)

    def list_input_devices(self) -> dict[str, Any]:
        with self._lock:
            connector_connected = any(
                connection.endpoint_source == "local_microphone"
                for connection in self._connections.values()
            )
            return {
                "connector_client_id": self._device_catalog_client_id,
                "connector_connected": connector_connected,
                "devices": deepcopy(self._device_catalog),
            }

    def input_state(self) -> dict[str, Any]:
        with self._lock:
            microphone = self._settings["microphone_settings"]
            return {
                "configured_source": microphone["input_source"],
                "effective_source": self._effective_source_locked(),
                "local_input_device": deepcopy(
                    microphone["local_input_device"]
                ),
                "console": deepcopy(microphone["console"]),
            }

    def start_web_input_session(
        self,
        payload: dict[str, Any],
    ) -> dict[str, str]:
        if not isinstance(payload, dict) or set(payload) != {"owner_client_id"}:
            raise ServiceError(
                400,
                "invalid_audio_input_session",
                "Web audio input session fields are invalid.",
            )
        owner_client_id = payload.get("owner_client_id")
        if not isinstance(owner_client_id, str) or not owner_client_id.strip():
            raise ServiceError(
                400,
                "invalid_audio_input_session",
                "owner_client_id is required.",
            )
        owner_client_id = owner_client_id.strip()
        with self._condition:
            if not self._service._event_stream_registry.is_client_connected(
                owner_client_id
            ):
                raise ServiceError(
                    409,
                    "audio_input_session_owner_unavailable",
                    "The Web audio input session owner is not connected.",
                )
            if self._web_input_session is not None:
                raise ServiceError(
                    409,
                    "audio_input_busy",
                    "Another Web audio input session is active.",
                )
            # 入力元の選択は保存設定を唯一の正本にする。
            input_source = self._settings["microphone_settings"]["input_source"]
            session = {
                "input_session_id": f"web_audio_input:{uuid.uuid4().hex}",
                "owner_client_id": owner_client_id,
                "input_source": input_source,
            }
            self._web_input_session = session
            self._revoke_active_locked("source_switched")
        self._publish_state(force=True)
        return deepcopy(session)

    def stop_web_input_session(
        self,
        input_session_id: str,
    ) -> dict[str, str]:
        with self._condition:
            if (
                self._web_input_session is None
                or self._web_input_session["input_session_id"]
                != input_session_id
            ):
                raise ServiceError(
                    404,
                    "audio_input_session_not_found",
                    "The Web audio input session does not exist.",
                )
            session = deepcopy(self._web_input_session)
            self._web_input_session = None
            self._revoke_active_locked("source_switched")
        self._publish_state(force=True)
        return session

    def list_speakers(self) -> dict[str, Any]:
        return {"speakers": self._service.store.list_voice_speakers()}

    def start_enrollment(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_enrollment_payload(payload)
        owner_client_id = payload["owner_client_id"].strip()
        with self._condition:
            if self._enrollment is not None:
                raise ServiceError(
                    409,
                    "speaker_enrollment_busy",
                    "Another speaker enrollment is active.",
                )
            if not self._service._event_stream_registry.is_client_connected(
                owner_client_id
            ):
                raise ServiceError(
                    409,
                    "speaker_enrollment_owner_unavailable",
                    "The enrollment owner client is not connected.",
                )
            active = self._active_connection
            if active is None or self._response_client_id_locked(active) != owner_client_id:
                raise ServiceError(
                    409,
                    "speaker_enrollment_source_unavailable",
                    "The enrollment owner does not own the current audio source.",
                )

            existing_person_ref = payload.get("person_ref")
            if existing_person_ref is not None:
                existing = self._service.store.get_voice_speaker(
                    existing_person_ref
                )
                if existing is None:
                    raise ServiceError(
                        404,
                        "speaker_not_found",
                        "The requested speaker does not exist.",
                    )
                conversation_display_name_id = existing[
                    "conversation_display_name_id"
                ]
                display_name = existing["display_name"]
                target_person_ref = existing_person_ref
                public_person_ref = existing_person_ref
            else:
                conversation_display_name_id = payload[
                    "conversation_display_name_id"
                ].strip()
                definition = self._service.store.get_conversation_display_name(
                    conversation_display_name_id
                )
                if definition is None:
                    raise ServiceError(
                        404,
                        "conversation_display_name_not_found",
                        "The conversation display name does not exist.",
                    )
                if any(
                    speaker["conversation_display_name_id"]
                    == conversation_display_name_id
                    for speaker in self._service.store.list_voice_speakers()
                ):
                    raise ServiceError(
                        409,
                        "conversation_display_name_already_assigned",
                        "The conversation display name is assigned to another speaker.",
                    )
                display_name = definition["display_name"]
                target_person_ref = f"person:voice:{uuid.uuid4()}"
                public_person_ref = None

            started_at = local_now()
            self._enrollment = {
                "enrollment_id": f"speaker_enrollment:{uuid.uuid4().hex}",
                "owner_client_id": owner_client_id,
                "target_person_ref": target_person_ref,
                "public_person_ref": public_person_ref,
                "conversation_display_name_id": conversation_display_name_id,
                "display_name": display_name,
                "embeddings": [],
                "expires_monotonic": time.monotonic()
                + ENROLLMENT_TIMEOUT_SECONDS,
                "expires_at": (
                    started_at + timedelta(seconds=ENROLLMENT_TIMEOUT_SECONDS)
                ).isoformat(),
            }
            self._discard_audio_locked()
            self._refresh_pause_state_locked()
            summary = self._enrollment_summary_locked()
        self._publish_state(force=True)
        return summary

    def cancel_enrollment(
        self,
        enrollment_id: str,
        *,
        owner_client_id: str | None,
    ) -> dict[str, Any]:
        with self._condition:
            if (
                self._enrollment is None
                or self._enrollment["enrollment_id"] != enrollment_id
            ):
                raise ServiceError(
                    404,
                    "speaker_enrollment_not_found",
                    "The requested speaker enrollment does not exist.",
                )
            if (
                owner_client_id is not None
                and self._enrollment["owner_client_id"] != owner_client_id
            ):
                raise ServiceError(
                    409,
                    "speaker_enrollment_owner_mismatch",
                    "Only the enrollment owner can cancel the session.",
                )
            summary = self._enrollment_summary_locked()
            self._cancel_enrollment_locked()
        self._publish_state(force=True)
        return summary

    def assign_speaker_conversation_display_name(
        self,
        person_ref: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if set(payload) != {"conversation_display_name_id"}:
            raise ServiceError(
                400,
                "invalid_conversation_display_name_assignment",
                "conversation_display_name_id is required.",
            )
        display_name_id = payload.get("conversation_display_name_id")
        if not isinstance(display_name_id, str) or not display_name_id.strip():
            raise ServiceError(
                400,
                "invalid_conversation_display_name_assignment",
                "conversation_display_name_id must be a non-empty string.",
            )
        normalized_display_name_id = display_name_id.strip()
        if self._service.store.get_conversation_display_name(
            normalized_display_name_id
        ) is None:
            raise ServiceError(
                404,
                "conversation_display_name_not_found",
                "The conversation display name does not exist.",
            )
        try:
            speaker = self._service.store.assign_voice_speaker_conversation_display_name(
                person_ref=person_ref,
                conversation_display_name_id=normalized_display_name_id,
            )
        except ValueError as exc:
            if str(exc) == "conversation_display_name_assignment_conflict":
                raise ServiceError(
                    409,
                    "conversation_display_name_already_assigned",
                    "The conversation display name is assigned to another speaker.",
                ) from exc
            raise
        if speaker is None:
            raise ServiceError(
                404,
                "speaker_not_found",
                "The requested speaker does not exist.",
            )
        return speaker

    def unregister_speaker(self, person_ref: str) -> dict[str, Any]:
        speaker = self._service.store.unregister_voice_speaker(person_ref)
        if speaker is None:
            raise ServiceError(
                404,
                "speaker_not_found",
                "The requested speaker does not exist.",
            )
        with self._condition:
            self._discard_audio_locked()
            self._refresh_pause_state_locked()
        self._publish_state(force=True)
        return speaker

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            active = self._active_connection
            normal_state = (
                "active"
                if self._normal_active_until_monotonic is not None
                and self._normal_active_until_monotonic > time.monotonic()
                else "waiting"
            )
            models = {
                "silero_vad": (
                    self._models.silero_model_id
                    if self._models is not None
                    else None
                ),
                "wespeaker": (
                    self._models.speaker_model_id
                    if self._models is not None
                    else None
                ),
            }
            return {
                "available": self._available,
                "unavailable_reason": self._unavailable_reason,
                "model_ids": models,
                "configured_source": self._settings[
                    "microphone_settings"
                ]["input_source"],
                "effective_source": self._effective_source_locked(),
                "response_client_id": self._effective_response_client_id_locked(),
                "selected_device": deepcopy(
                    active.device if active is not None else None
                ),
                "connector": {
                    "client_id": self._device_catalog_client_id,
                    "connected": any(
                        connection.endpoint_source == "local_microphone"
                        for connection in self._connections.values()
                    ),
                },
                "active_source": (
                    active.endpoint_source if active is not None else None
                ),
                "lease_generation": (
                    active.lease_generation if active is not None else None
                ),
                "last_heartbeat_at": (
                    active.last_heartbeat_at if active is not None else None
                ),
                "settings_generation": self._settings_generation,
                "mode": "enrollment" if self._enrollment is not None else "normal",
                "paused_reason": self._paused_reason,
                "vad": {
                    "speaking": self._vad_speaking,
                    "probability": self._vad_probability,
                    "dbfs": self._dbfs,
                },
                "normal_activation": {
                    "state": normal_state,
                    "active_until": self._normal_active_until,
                },
                "queue": {
                    "processing": (
                        self._processing.utterance_seq
                        if self._processing is not None
                        else None
                    ),
                    "waiting": [
                        item.utterance_seq for item in self._waiting
                    ],
                },
                "enrollment": (
                    self._enrollment_inspection_locked()
                    if self._enrollment is not None
                    else None
                ),
                "last_utterance_result": deepcopy(
                    self._last_utterance_result
                ),
            }

    def _handle_audio_start(
        self,
        connection: AudioConnection,
        payload: dict[str, Any],
    ) -> None:
        normalized = self._validate_audio_start(connection, payload)
        with self._condition:
            effective_source = self._effective_source_locked()
            if connection.endpoint_source != effective_source:
                raise ServiceError(
                    409,
                    "audio_source_not_selected",
                    "The audio source is not currently selected.",
                )
            if connection.endpoint_source == "web_microphone":
                session = self._web_input_session
                if (
                    session is None
                    or session["input_source"] != "web_microphone"
                    or session["owner_client_id"] != normalized["client_id"]
                    or session["input_session_id"]
                    != normalized["input_session_id"]
                ):
                    raise ServiceError(
                        409,
                        "audio_input_session_not_found",
                        "The Web audio input session is not active.",
                    )
            if connection.endpoint_source == "console_microphone":
                console = self._settings["microphone_settings"]["console"]
                if (
                    console is None
                    or console["client_id"] != normalized["client_id"]
                ):
                    raise ServiceError(
                        409,
                        "audio_source_not_selected",
                        "This CocoroConsole is not the selected audio source.",
                    )
            active = self._active_connection
            if active is not None and active is not connection:
                raise ServiceError(
                    409,
                    "audio_input_busy",
                    "The selected audio source already owns the input lease.",
                )
            elif active is connection:
                self._release_active_locked()

            self._lease_generation += 1
            connection.client_id = normalized["client_id"]
            connection.lease_generation = self._lease_generation
            connection.last_heartbeat_monotonic = time.monotonic()
            connection.last_heartbeat_at = now_iso()
            connection.device = normalized["device"]
            connection.capture_settings = normalized["capture_settings"]
            self._active_connection = connection
            self._paused_reason = None
            self._segmenter = (
                AudioSegmenter(
                    self._models.vad,
                    probability_threshold=float(
                        self._settings["microphone_settings"][
                            "vad_probability_threshold"
                        ]
                    ),
                )
                if self._models is not None
                else None
            )
            self._reset_vad_metrics_locked()
            initial_pause_reason = self._evaluate_pause_reason_locked(connection)
            if (
                initial_pause_reason is None
                and len(self._waiting) >= MAX_WAITING_UTTERANCES
            ):
                initial_pause_reason = "queue_full"
            self._paused_reason = initial_pause_reason
            connection.websocket.send_json(
                {
                    "type": "audio_started",
                    "protocol_version": AUDIO_PROTOCOL_VERSION,
                    "lease_generation": connection.lease_generation,
                    "input_source": connection.endpoint_source,
                    "mode": (
                        "enrollment"
                        if self._enrollment is not None
                        else "normal"
                    ),
                    "paused_reason": initial_pause_reason,
                    "heartbeat_interval_seconds": HEARTBEAT_INTERVAL_SECONDS,
                    "lease_timeout_seconds": LEASE_TIMEOUT_SECONDS,
                }
            )
        self._publish_state(force=True)

    def _handle_binary(
        self,
        connection: AudioConnection,
        pcm16le: bytes,
    ) -> None:
        with self._condition:
            if connection.client_id is None:
                raise ServiceError(
                    400,
                    "invalid_audio_frame",
                    "Binary audio is not accepted before audio_started.",
                )
            if connection is not self._active_connection:
                raise ServiceError(
                    409,
                    "audio_lease_revoked",
                    "The audio input lease is no longer active.",
                )
            if connection.lease_generation is None:
                raise ServiceError(
                    409,
                    "audio_lease_revoked",
                    "The audio input lease is no longer active.",
                )
            if self._paused_reason is not None:
                raise ServiceError(
                    409,
                    "audio_input_paused",
                    "The audio input source is paused.",
                )
            if len(pcm16le) != FRAME_BYTES:
                raise ServiceError(
                    400,
                    "invalid_audio_frame",
                    "Audio frame must contain exactly 640 bytes.",
                )
            if self._segmenter is None:
                raise ServiceError(
                    503,
                    "audio_runtime_unavailable",
                    "The audio model runtime is unavailable.",
                )
            update = self._segmenter.feed_frame(pcm16le)
            self._vad_probability = update.vad_probability
            self._vad_speaking = update.vad_speaking
            self._dbfs = update.dbfs if math.isfinite(update.dbfs) else None
            for utterance in update.utterances:
                self._enqueue_utterance_locked(connection, utterance)
            self._publish_metrics_locked()

    def _handle_heartbeat(
        self,
        connection: AudioConnection,
        payload: dict[str, Any],
    ) -> None:
        if set(payload) != {"type", "lease_generation"}:
            raise ServiceError(
                400,
                "invalid_audio_control",
                "audio_heartbeat fields are invalid.",
            )
        with self._lock:
            if (
                connection is not self._active_connection
                or payload.get("lease_generation") != connection.lease_generation
            ):
                raise ServiceError(
                    409,
                    "audio_lease_revoked",
                    "The audio input lease is no longer active.",
                )
            connection.last_heartbeat_monotonic = time.monotonic()
            connection.last_heartbeat_at = now_iso()
            connection.websocket.send_json(
                {
                    "type": "audio_heartbeat_ack",
                    "lease_generation": connection.lease_generation,
                }
            )

    def _handle_audio_stop(
        self,
        connection: AudioConnection,
        payload: dict[str, Any],
    ) -> None:
        if set(payload) != {"type", "lease_generation"}:
            raise ServiceError(
                400,
                "invalid_audio_control",
                "audio_stop fields are invalid.",
            )
        with self._condition:
            if (
                connection is not self._active_connection
                or payload.get("lease_generation") != connection.lease_generation
            ):
                raise ServiceError(
                    409,
                    "audio_lease_revoked",
                    "The audio input lease is no longer active.",
                )
            lease_generation = connection.lease_generation
            connection.websocket.send_json(
                {
                    "type": "audio_stopped",
                    "lease_generation": lease_generation,
                }
            )
            if (
                self._enrollment is not None
                and connection.endpoint_source == "web_microphone"
                and connection.client_id == self._enrollment["owner_client_id"]
            ):
                self._cancel_enrollment_locked()
            self._release_active_locked()
        self._publish_state(force=True)

    def _handle_device_catalog(
        self,
        connection: AudioConnection,
        payload: dict[str, Any],
    ) -> None:
        if connection.endpoint_source != "local_microphone":
            raise ServiceError(
                400,
                "invalid_audio_control",
                "Web microphone cannot publish an input device catalog.",
            )
        if set(payload) != {"type", "client_id", "devices"}:
            raise ServiceError(
                400,
                "invalid_audio_control",
                "audio_device_catalog fields are invalid.",
            )
        client_id = payload.get("client_id")
        devices = payload.get("devices")
        if not isinstance(client_id, str) or not client_id.strip():
            raise ServiceError(
                400,
                "invalid_audio_control",
                "audio_device_catalog.client_id is required.",
            )
        if not isinstance(devices, list):
            raise ServiceError(
                400,
                "invalid_audio_control",
                "audio_device_catalog.devices must be an array.",
            )
        normalized_devices = [
            self._normalize_catalog_device(device)
            for device in devices
        ]
        identities = [
            (device["host_api"], device["name"])
            for device in normalized_devices
        ]
        for device in normalized_devices:
            identity = (device["host_api"], device["name"])
            device["ambiguous"] = identities.count(identity) > 1
        with self._lock:
            connection.client_id = client_id.strip()
            self._device_catalog_client_id = client_id.strip()
            self._device_catalog = normalized_devices
        self._publish_state(force=True)

    def _enqueue_utterance_locked(
        self,
        connection: AudioConnection,
        utterance: SegmentedUtterance,
    ) -> None:
        if len(self._waiting) >= MAX_WAITING_UTTERANCES:
            self._set_paused_locked("queue_full")
            return
        response_client_id = self._response_client_id_locked(connection)
        if response_client_id is None:
            self._set_paused_locked("response_client_unavailable")
            self._discard_audio_locked()
            return
        self._utterance_seq += 1
        self._waiting.append(
            QueuedUtterance(
                utterance_seq=self._utterance_seq,
                source=connection.endpoint_source,
                response_client_id=response_client_id,
                lease_generation=connection.lease_generation or 0,
                settings_generation=self._settings_generation,
                work_generation=self._work_generation,
                explicit_input=self._web_input_session is not None,
                utterance=utterance,
            )
        )
        if len(self._waiting) >= MAX_WAITING_UTTERANCES:
            self._set_paused_locked("queue_full")
        self._condition.notify()
        self._publish_state(force=True)

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._condition:
                while not self._waiting and not self._stop_event.is_set():
                    self._condition.wait(timeout=1.0)
                if self._stop_event.is_set():
                    return
                item = self._waiting.popleft()
                self._processing = item
                if self._paused_reason == "queue_full":
                    self._refresh_pause_state_locked()
            self._publish_state(force=True)
            try:
                if self._enrollment is not None:
                    self._process_enrollment_utterance(item)
                else:
                    self._process_normal_utterance(item)
            except Exception as exc:  # noqa: BLE001
                debug_log(
                    "Audio",
                    (
                        f"utterance={item.utterance_seq} source={item.source} "
                        f"result=internal_error error={type(exc).__name__}"
                    ),
                    level="ERROR",
                )
                with self._lock:
                    if self._item_is_current_locked(item):
                        self._last_utterance_result = {
                            "utterance_seq": item.utterance_seq,
                            "result_code": "internal_error",
                        }
            finally:
                with self._condition:
                    if self._processing is item:
                        self._processing = None
                    self._refresh_pause_state_locked()
                self._publish_state(force=True)

    def _process_enrollment_utterance(self, item: QueuedUtterance) -> None:
        if item.utterance.voiced_samples < ENROLLMENT_MINIMUM_SAMPLES:
            with self._lock:
                if self._item_is_current_locked(item):
                    self._last_utterance_result = {
                        "utterance_seq": item.utterance_seq,
                        "result_code": "enrollment_sample_too_short",
                        "voiced_duration_seconds": (
                            item.utterance.voiced_duration_seconds
                        ),
                    }
            return
        if self._models is None:
            return
        started = time.monotonic()
        embedding = self._models.extract_speaker_embedding(
            item.utterance.speaker_pcm16le
        )
        elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)
        with self._condition:
            if not self._item_is_current_locked(item) or self._enrollment is None:
                return
            self._enrollment["embeddings"].append(embedding)
            completed = len(self._enrollment["embeddings"])
            result_code = "enrollment_sample_accepted"
            if completed == ENROLLMENT_REQUIRED_SAMPLES:
                mean_embedding = self._models.mean_embedding(
                    self._enrollment["embeddings"]
                )
                self._service.store.replace_voice_speaker_registration(
                    person_ref=self._enrollment["target_person_ref"],
                    conversation_display_name_id=self._enrollment[
                        "conversation_display_name_id"
                    ],
                    embedding=mean_embedding,
                    model_id=self._models.speaker_model_id,
                )
                self._enrollment = None
                result_code = "enrollment_completed"
            self._last_utterance_result = {
                "utterance_seq": item.utterance_seq,
                "result_code": result_code,
                "voiced_duration_seconds": (
                    item.utterance.voiced_duration_seconds
                ),
                "speaker_processing_ms": elapsed_ms,
            }
            if result_code == "enrollment_completed":
                self._discard_audio_locked(keep_processing=True)
            self._refresh_pause_state_locked()
        debug_log(
            "Audio",
            (
                f"utterance={item.utterance_seq} source={item.source} "
                f"voiced_ms={item.utterance.voiced_duration_seconds * 1000:.0f} "
                f"speaker_ms={elapsed_ms:.1f} result={result_code} "
                f"settings_generation={item.settings_generation}"
            ),
            level="DEBUG",
        )

    def _process_normal_utterance(self, item: QueuedUtterance) -> None:
        if self._models is None or self._parallel_executor is None:
            return
        settings = self._settings
        stt = settings["stt"]
        speakers = self._service.store.list_voice_speakers(
            registered_only=True,
            include_embedding=True,
        )
        stt_future = self._parallel_executor.submit(
            self._timed_outcome,
            self._amivoice.recognize,
            item.utterance.amivoice_pcm16le,
            api_key=stt["api_key"],
            profile_id=stt["profile_id"],
        )
        speaker_future = self._parallel_executor.submit(
            self._timed_outcome,
            self._models.extract_speaker_embedding,
            item.utterance.speaker_pcm16le,
        )
        stt_result: AmiVoiceResult | None = None
        stt_error: AmiVoiceError | None = None
        embedding: list[float] | None = None
        speaker_error: Exception | None = None
        stt_ms = 0.0
        speaker_ms = 0.0
        stt_value, stt_exception, stt_ms = stt_future.result()
        if isinstance(stt_exception, AmiVoiceError):
            stt_error = stt_exception
        elif stt_exception is not None:
            stt_error = AmiVoiceError("internal_error", str(stt_exception))
        else:
            stt_result = stt_value
        speaker_value, speaker_exception, speaker_ms = speaker_future.result()
        if speaker_exception is not None:
            speaker_error = speaker_exception
        else:
            embedding = speaker_value

        identification = (
            self._models.identify_speaker(
                embedding,
                speakers,
                threshold=float(
                    settings["microphone_settings"][
                        "speaker_recognition_threshold"
                    ]
                ),
            )
            if embedding is not None
            else SpeakerIdentification(
                person_ref=None,
                accepted=False,
                top1_person_ref=None,
                top1_similarity=None,
                top2_person_ref=None,
                top2_similarity=None,
            )
        )
        result_code = "accepted"
        if stt_error is not None:
            result_code = (
                "stt_configuration_error"
                if self._is_stt_configuration_error(stt_error)
                else "stt_transient_error"
            )
        elif speaker_error is not None:
            result_code = "speaker_processing_error"
        elif not identification.accepted:
            result_code = "speaker_unidentified"

        matched_wake_word: str | None = None
        if result_code == "accepted" and stt_result is not None:
            matched, matched_wake_word = self._wake_accepts(
                explicit_input=item.explicit_input,
                transcript=stt_result.text,
                wake_words=settings["wake_words"],
            )
            if not matched:
                result_code = "wake_word_not_matched"

        with self._condition:
            if not self._item_is_current_locked(item):
                return
            self._last_utterance_result = self._build_last_result(
                item=item,
                result_code=result_code,
                stt_ms=stt_ms,
                speaker_ms=speaker_ms,
                stt_error=stt_error,
                identification=identification,
            )
            if result_code == "stt_configuration_error":
                self._set_paused_locked("stt_configuration_error")
                self._discard_audio_locked(keep_processing=True)
        self._log_utterance_result(
            item=item,
            result_code=result_code,
            stt_ms=stt_ms,
            speaker_ms=speaker_ms,
            stt_error=stt_error,
            identification=identification,
        )
        if (
            result_code != "accepted"
            or stt_result is None
            or identification.person_ref is None
        ):
            return
        self._deliver_conversation(
            item=item,
            transcript=stt_result.text,
            person_ref=identification.person_ref,
            matched_wake_word=matched_wake_word,
        )

    def _deliver_conversation(
        self,
        *,
        item: QueuedUtterance,
        transcript: str,
        person_ref: str,
        matched_wake_word: str | None,
    ) -> None:
        speaker = self._service.store.get_voice_speaker(person_ref)
        if speaker is None:
            return
        target_client_id = item.response_client_id
        if not self._response_client_available(target_client_id):
            with self._condition:
                if self._item_is_current_locked(item):
                    self._set_paused_locked("response_client_unavailable")
                    self._discard_audio_locked(keep_processing=True)
            return
        voice_id = person_ref.removeprefix("person:voice:")
        interaction_ref = f"interaction:voice:direct:{voice_id}"
        conversation_event = {
            "event_id": self._service._next_stream_event_id(),
            "type": "conversation_input",
            "data": {
                "utterance_seq": item.utterance_seq,
                "source_kind": item.source,
                "message": transcript,
                "interaction_ref": interaction_ref,
                "speaker_ref": person_ref,
                "participant_refs": [person_ref],
                "display_name": speaker["display_name"],
            },
        }
        if not self._service._event_stream_registry.send_to_client(
            target_client_id,
            conversation_event,
        ):
            return

        client_context: dict[str, Any] = {
            "client_id": target_client_id,
            "source_kind": item.source,
            "utterance_seq": item.utterance_seq,
        }
        if matched_wake_word is not None:
            client_context["matched_wake_word"] = matched_wake_word
        state = self._service.store.read_state()
        response = self._service.handle_conversation(
            state.get("console_access_token"),
            {
                "text": transcript,
                "client_context": client_context,
                "interaction_context": {
                    "interaction_ref": interaction_ref,
                    "speaker_ref": person_ref,
                    "participants": [
                        {
                            "person_ref": person_ref,
                            "display_name": speaker["display_name"],
                        }
                    ],
                },
            },
            defer_audio_delivery=True,
        )
        if not item.explicit_input:
            with self._lock:
                if self._item_is_current_locked(item):
                    self._normal_active_until_monotonic = (
                        time.monotonic() + NORMAL_ACTIVE_SECONDS
                    )
                    self._normal_active_until = (
                        local_now()
                        + timedelta(seconds=NORMAL_ACTIVE_SECONDS)
                    ).isoformat()
        speech = response.get("speech")
        if isinstance(speech, dict) and isinstance(speech.get("text"), str):
            _, audio_delivery = self._service._emit_assistant_message_with_audio(
                target_client_id=target_client_id,
                event_data={
                    "cycle_id": response.get("cycle_id"),
                    "source_kind": "conversation",
                    "interaction_ref": interaction_ref,
                    "recipient_person_refs": [person_ref],
                },
                speech_text=speech["text"],
            )
            speech["audio_delivery"] = audio_delivery
        with self._lock:
            if self._item_is_current_locked(item):
                self._last_utterance_result = {
                    **(self._last_utterance_result or {}),
                    "result_code": (
                        "conversation_completed"
                        if response.get("result_kind") != "internal_failure"
                        else "conversation_failed"
                    ),
                    "cycle_id": response.get("cycle_id"),
                }
        self._publish_state(force=True)

    def _watchdog_loop(self) -> None:
        while not self._stop_event.wait(1.0):
            timed_out: AudioConnection | None = None
            publish = False
            with self._condition:
                active = self._active_connection
                if (
                    active is not None
                    and active.last_heartbeat_monotonic is not None
                    and time.monotonic() - active.last_heartbeat_monotonic
                    >= LEASE_TIMEOUT_SECONDS
                ):
                    timed_out = active
                    active.websocket.send_json(
                        {
                            "type": "audio_error",
                            "code": "audio_lease_timeout",
                            "message": "The audio input lease heartbeat timed out.",
                        }
                    )
                    self._release_active_locked()
                    publish = True
                if (
                    self._enrollment is not None
                    and time.monotonic()
                    >= self._enrollment["expires_monotonic"]
                ):
                    self._cancel_enrollment_locked()
                    publish = True
                if (
                    self._normal_active_until_monotonic is not None
                    and time.monotonic()
                    >= self._normal_active_until_monotonic
                ):
                    self._normal_active_until_monotonic = None
                    self._normal_active_until = None
                    publish = True
            if timed_out is not None:
                timed_out.websocket.close()
            if publish:
                self._publish_state(force=True)

    def _refresh_pause_state_locked(self) -> None:
        active = self._active_connection
        if active is None:
            self._paused_reason = None
            return
        reason = self._evaluate_pause_reason_locked(active)
        if reason is None and len(self._waiting) >= MAX_WAITING_UTTERANCES:
            reason = "queue_full"
        if reason is None:
            if self._paused_reason is not None:
                self._paused_reason = None
                active.websocket.send_json(
                    {
                        "type": "audio_resumed",
                        "lease_generation": active.lease_generation,
                    }
                )
            return
        self._set_paused_locked(reason)

    def _evaluate_pause_reason_locked(
        self,
        connection: AudioConnection,
    ) -> str | None:
        if not self._available:
            return "audio_runtime_unavailable"
        microphone = self._settings["microphone_settings"]
        if connection.endpoint_source == "local_microphone":
            if (
                microphone["local_input_device"] is None
                or connection.device != microphone["local_input_device"]
            ):
                return "microphone_device_unavailable"
        if connection.endpoint_source == "console_microphone":
            console = microphone["console"]
            if console is None or connection.device != console["input_device"]:
                return "microphone_device_unavailable"
        response_client_id = self._response_client_id_locked(connection)
        if (
            response_client_id is None
            or not self._response_client_available(response_client_id)
        ):
            return "response_client_unavailable"
        if self._enrollment is not None:
            return None
        stt = self._settings["stt"]
        if not stt["enabled"]:
            return "stt_disabled"
        if not stt["api_key"]:
            return "stt_configuration_error"
        current_model_speakers = [
            speaker
            for speaker in self._service.store.list_voice_speakers(
                registered_only=True
            )
            if self._models is not None
            and speaker["model_id"] == self._models.speaker_model_id
        ]
        if not current_model_speakers:
            return "speaker_enrollment_required"
        return None

    def _response_client_id_locked(
        self,
        connection: AudioConnection,
    ) -> str | None:
        return self._effective_response_client_id_locked()

    def _response_client_available(self, client_id: str) -> bool:
        registry = self._service._event_stream_registry
        return (
            registry.is_client_connected(client_id)
            and registry.client_accepts_event(client_id, "conversation_input")
            and registry.client_accepts_event(client_id, "assistant_message")
        )

    def _set_paused_locked(self, reason: str) -> None:
        if self._paused_reason == reason:
            return
        self._paused_reason = reason
        active = self._active_connection
        if active is not None:
            active.websocket.send_json(
                {
                    "type": "audio_paused",
                    "lease_generation": active.lease_generation,
                    "reason": reason,
                }
            )

    def _release_active_locked(self) -> None:
        if self._active_connection is not None:
            self._active_connection.lease_generation = None
        self._active_connection = None
        self._paused_reason = None
        self._discard_audio_locked()

    def _revoke_active_locked(self, reason: str) -> None:
        active = self._active_connection
        if active is not None and active.lease_generation is not None:
            active.websocket.send_json(
                {
                    "type": "audio_paused",
                    "lease_generation": active.lease_generation,
                    "reason": reason,
                }
            )
            active.lease_generation = None
        self._active_connection = None
        self._paused_reason = None
        self._discard_audio_locked()

    def _effective_source_locked(self) -> str:
        if self._web_input_session is not None:
            return self._web_input_session["input_source"]
        return self._settings["microphone_settings"]["input_source"]

    def _effective_response_client_id_locked(self) -> str | None:
        if self._web_input_session is not None:
            return self._web_input_session["owner_client_id"]
        microphone = self._settings["microphone_settings"]
        # ブラウザ入力は明示的なWeb入力sessionだけが応答先を持つ。
        if microphone["input_source"] == "web_microphone":
            return None
        console = microphone["console"]
        if console is None:
            return None
        return console["client_id"]

    def _discard_audio_locked(self, *, keep_processing: bool = False) -> None:
        # 実行中の外部処理結果もwork generationで失効させる。
        self._work_generation += 1
        self._waiting.clear()
        if not keep_processing:
            self._processing = None
        if self._segmenter is not None:
            self._segmenter.reset()
        self._reset_vad_metrics_locked()
        self._condition.notify_all()

    def _reset_vad_metrics_locked(self) -> None:
        self._vad_probability = None
        self._vad_speaking = False
        self._dbfs = None

    def _cancel_enrollment_locked(self) -> None:
        self._enrollment = None
        self._discard_audio_locked()
        self._refresh_pause_state_locked()

    def _enrollment_summary_locked(self) -> dict[str, Any]:
        if self._enrollment is None:
            raise RuntimeError("Speaker enrollment is not active.")
        return {
            "enrollment_id": self._enrollment["enrollment_id"],
            "owner_client_id": self._enrollment["owner_client_id"],
            "person_ref": self._enrollment["public_person_ref"],
            "conversation_display_name_id": self._enrollment[
                "conversation_display_name_id"
            ],
            "display_name": self._enrollment["display_name"],
            "required_samples": ENROLLMENT_REQUIRED_SAMPLES,
            "completed_samples": len(self._enrollment["embeddings"]),
            "expires_at": self._enrollment["expires_at"],
        }

    def _enrollment_inspection_locked(self) -> dict[str, Any]:
        if self._enrollment is None:
            raise RuntimeError("Speaker enrollment is not active.")
        return {
            "enrollment_id": self._enrollment["enrollment_id"],
            "owner_client_id": self._enrollment["owner_client_id"],
            "target_person_ref": self._enrollment["public_person_ref"],
            "required_samples": ENROLLMENT_REQUIRED_SAMPLES,
            "completed_samples": len(self._enrollment["embeddings"]),
            "expires_at": self._enrollment["expires_at"],
        }

    def _item_is_current_locked(self, item: QueuedUtterance) -> bool:
        active = self._active_connection
        return (
            item.settings_generation == self._settings_generation
            and item.work_generation == self._work_generation
            and active is not None
            and item.lease_generation == active.lease_generation
        )

    def _wake_accepts(
        self,
        *,
        explicit_input: bool,
        transcript: str,
        wake_words: list[str],
    ) -> tuple[bool, str | None]:
        if explicit_input or not wake_words:
            return True, None
        now = time.monotonic()
        if (
            self._normal_active_until_monotonic is not None
            and self._normal_active_until_monotonic > now
        ):
            return True, None
        normalized_transcript = unicodedata.normalize(
            "NFKC",
            transcript,
        ).casefold()
        for wake_word in wake_words:
            normalized_wake_word = unicodedata.normalize(
                "NFKC",
                wake_word,
            ).casefold()
            if normalized_transcript.startswith(normalized_wake_word):
                return True, wake_word
        return False, None

    def _build_last_result(
        self,
        *,
        item: QueuedUtterance,
        result_code: str,
        stt_ms: float,
        speaker_ms: float,
        stt_error: AmiVoiceError | None,
        identification: SpeakerIdentification,
    ) -> dict[str, Any]:
        # しきい値超過は最終受理（margin 条件を含む）と独立して返す。
        threshold = float(
            self._settings["microphone_settings"][
                "speaker_recognition_threshold"
            ]
        )
        top1_similarity = identification.top1_similarity
        top2_similarity = identification.top2_similarity
        threshold_met = (
            top1_similarity is not None and top1_similarity >= threshold
        )
        result: dict[str, Any] = {
            "utterance_seq": item.utterance_seq,
            "source": item.source,
            "voiced_duration_seconds": (
                item.utterance.voiced_duration_seconds
            ),
            "result_code": result_code,
            "stt_processing_ms": stt_ms,
            "speaker_processing_ms": speaker_ms,
            "amivoice_result_code": (
                stt_error.provider_code
                if stt_error is not None
                else ""
            ),
            "top1_similarity": top1_similarity,
            "top2_similarity": top2_similarity,
            "threshold_met": threshold_met,
        }
        if result_code == "speaker_unidentified":
            result["speaker_candidates"] = [
                {
                    "person_ref": person_ref,
                    "similarity": similarity,
                }
                for person_ref, similarity in (
                    (
                        identification.top1_person_ref,
                        identification.top1_similarity,
                    ),
                    (
                        identification.top2_person_ref,
                        identification.top2_similarity,
                    ),
                )
                if person_ref is not None and similarity is not None
            ]
        return result

    def _log_utterance_result(
        self,
        *,
        item: QueuedUtterance,
        result_code: str,
        stt_ms: float,
        speaker_ms: float,
        stt_error: AmiVoiceError | None,
        identification: SpeakerIdentification,
    ) -> None:
        queue_size = len(self._waiting)
        top1 = (
            f"{identification.top1_similarity:.4f}"
            if identification.top1_similarity is not None
            else "-"
        )
        top2 = (
            f"{identification.top2_similarity:.4f}"
            if identification.top2_similarity is not None
            else "-"
        )
        debug_log(
            "Audio",
            (
                f"utterance={item.utterance_seq} source={item.source} "
                f"voiced_ms={item.utterance.voiced_duration_seconds * 1000:.0f} "
                f"queue={queue_size} stt_ms={stt_ms:.1f} "
                f"speaker_ms={speaker_ms:.1f} "
                f"amivoice_code={(stt_error.provider_code if stt_error else '-')} "
                f"top1={top1} top2={top2} result={result_code} "
                f"settings_generation={item.settings_generation}"
            ),
            level="DEBUG",
        )

    def _is_stt_configuration_error(self, error: AmiVoiceError) -> bool:
        return (
            error.code in {"missing_api_key", "provider_error"}
            or error.http_status in {400, 401, 403}
        )

    def _timed_outcome(
        self,
        callable_object: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Any | None, Exception | None, float]:
        started = time.monotonic()
        try:
            result = callable_object(*args, **kwargs)
            error: Exception | None = None
        except Exception as exc:  # noqa: BLE001
            result = None
            error = exc
        elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)
        return result, error, elapsed_ms

    def _validate_audio_start(
        self,
        connection: AudioConnection,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        fields = {
            "type",
            "protocol_version",
            "client_id",
            "input_source",
            "input_session_id",
            "format",
            "device",
            "capture_settings",
        }
        if set(payload) != fields:
            raise ServiceError(
                400,
                "invalid_audio_start",
                "audio_start fields are invalid.",
            )
        if payload.get("protocol_version") != AUDIO_PROTOCOL_VERSION:
            raise ServiceError(
                400,
                "invalid_audio_start",
                "audio_start.protocol_version is unsupported.",
            )
        if payload.get("input_source") != connection.endpoint_source:
            raise ServiceError(
                400,
                "invalid_audio_start",
                "audio_start.input_source does not match the endpoint.",
            )
        client_id = payload.get("client_id")
        if (
            not isinstance(client_id, str)
            or not client_id.strip()
            or len(client_id.strip()) > 128
        ):
            raise ServiceError(
                400,
                "invalid_audio_start",
                "audio_start.client_id is invalid.",
            )
        if payload.get("format") != AUDIO_FORMAT:
            raise ServiceError(
                400,
                "invalid_audio_start",
                "audio_start.format is unsupported.",
            )
        input_session_id = payload.get("input_session_id")
        if connection.endpoint_source == "web_microphone":
            if not isinstance(input_session_id, str) or not input_session_id:
                raise ServiceError(
                    400,
                    "invalid_audio_start",
                    "audio_start.input_session_id is required.",
                )
        elif input_session_id is not None:
            raise ServiceError(
                400,
                "invalid_audio_start",
                "audio_start.input_session_id must be null.",
            )
        device = self._normalize_start_device(
            payload.get("device"),
            source=connection.endpoint_source,
        )
        capture_settings = self._normalize_capture_settings(
            payload.get("capture_settings"),
            source=connection.endpoint_source,
        )
        return {
            "client_id": client_id.strip(),
            "input_session_id": input_session_id,
            "device": device,
            "capture_settings": capture_settings,
        }

    def _normalize_start_device(
        self,
        value: Any,
        *,
        source: str,
    ) -> dict[str, str] | None:
        if source == "web_microphone":
            if value is None:
                return None
            raise ServiceError(
                400,
                "invalid_audio_start",
                "audio_start.device must be null for web_microphone.",
            )
        expected_fields = (
            {"device_id", "name"}
            if source == "console_microphone"
            else {"host_api", "name"}
        )
        if not isinstance(value, dict) or set(value) != expected_fields:
            raise ServiceError(
                400,
                "invalid_audio_start",
                "audio_start.device is invalid.",
            )
        normalized: dict[str, str] = {}
        for field_name in expected_fields:
            field_value = value.get(field_name)
            if not isinstance(field_value, str) or not field_value.strip():
                raise ServiceError(
                    400,
                    "invalid_audio_start",
                    f"audio_start.device.{field_name} is invalid.",
                )
            normalized[field_name] = field_value.strip()
        return normalized

    def _normalize_capture_settings(
        self,
        value: Any,
        *,
        source: str,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ServiceError(
                400,
                "invalid_audio_start",
                "audio_start.capture_settings must be an object.",
            )
        fields = {"source_sample_rate"}
        if source == "web_microphone":
            fields |= {
                "echo_cancellation",
                "noise_suppression",
                "auto_gain_control",
                "device_id_present",
            }
        if set(value) != fields:
            raise ServiceError(
                400,
                "invalid_audio_start",
                "audio_start.capture_settings fields are invalid.",
            )
        source_sample_rate = value.get("source_sample_rate")
        if (
            type(source_sample_rate) not in {int, float}
            or float(source_sample_rate) <= 0
        ):
            raise ServiceError(
                400,
                "invalid_audio_start",
                "capture_settings.source_sample_rate is invalid.",
            )
        if source == "web_microphone":
            for field_name in fields - {
                "source_sample_rate",
                "device_id_present",
            }:
                if value.get(field_name) is not None and not isinstance(
                    value.get(field_name),
                    bool,
                ):
                    raise ServiceError(
                        400,
                        "invalid_audio_start",
                        f"capture_settings.{field_name} must be a boolean or null.",
                    )
            if not isinstance(value.get("device_id_present"), bool):
                raise ServiceError(
                    400,
                    "invalid_audio_start",
                    "capture_settings.device_id_present must be a boolean.",
                )
        return deepcopy(value)

    def _normalize_catalog_device(self, value: Any) -> dict[str, Any]:
        fields = {
            "host_api",
            "name",
            "max_input_channels",
            "default_sample_rate",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ServiceError(
                400,
                "invalid_audio_control",
                "audio_device_catalog device fields are invalid.",
            )
        host_api = value.get("host_api")
        name = value.get("name")
        channels = value.get("max_input_channels")
        sample_rate = value.get("default_sample_rate")
        if (
            not isinstance(host_api, str)
            or not host_api.strip()
            or not isinstance(name, str)
            or not name.strip()
            or type(channels) is not int
            or channels < 1
            or type(sample_rate) not in {int, float}
            or float(sample_rate) <= 0
        ):
            raise ServiceError(
                400,
                "invalid_audio_control",
                "audio_device_catalog device is invalid.",
            )
        return {
            "host_api": host_api.strip(),
            "name": name.strip(),
            "max_input_channels": channels,
            "default_sample_rate": float(sample_rate),
        }

    def _validate_enrollment_payload(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise ServiceError(
                400,
                "invalid_speaker_enrollment",
                "Speaker enrollment body must be an object.",
            )
        new_fields = {"owner_client_id", "conversation_display_name_id"}
        existing_fields = {"owner_client_id", "person_ref"}
        if frozenset(payload) not in {
            frozenset(new_fields),
            frozenset(existing_fields),
        }:
            raise ServiceError(
                400,
                "invalid_speaker_enrollment",
                "Speaker enrollment fields are invalid.",
            )
        owner_client_id = payload.get("owner_client_id")
        if not isinstance(owner_client_id, str) or not owner_client_id.strip():
            raise ServiceError(
                400,
                "invalid_speaker_enrollment",
                "owner_client_id is required.",
            )
        if "conversation_display_name_id" in payload:
            display_name_id = payload.get("conversation_display_name_id")
            if not isinstance(display_name_id, str) or not display_name_id.strip():
                raise ServiceError(
                    400,
                    "invalid_speaker_enrollment",
                    "conversation_display_name_id is required for a new speaker.",
                )
        if "person_ref" in payload:
            person_ref = payload.get("person_ref")
            if (
                not isinstance(person_ref, str)
                or not person_ref.startswith("person:voice:")
                or person_ref == "person:voice:"
            ):
                raise ServiceError(
                    400,
                    "invalid_speaker_enrollment",
                    "person_ref is invalid.",
                )

    def _read_audio_settings(self) -> dict[str, Any]:
        state = self._service.store.read_state()
        selected_avatar = state["avatars"][state["selected_avatar_id"]]
        selected_persona = state["personas"][state["selected_persona_id"]]
        return {
            "selected_avatar_id": state["selected_avatar_id"],
            "selected_persona_id": state["selected_persona_id"],
            "microphone_settings": deepcopy(state["microphone_settings"]),
            "stt": deepcopy(selected_avatar["stt"]),
            # 音声起動ワードは選択中人格設定の運用値。
            "wake_words": list(selected_persona["wake_words"]),
        }

    def _ensure_threads_locked(self) -> None:
        if self._worker_thread is not None:
            return
        self._parallel_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="otomekairo-audio-parallel",
        )
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="otomekairo-audio-worker",
            daemon=True,
        )
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name="otomekairo-audio-watchdog",
            daemon=True,
        )
        self._worker_thread.start()
        self._watchdog_thread.start()

    def _publish_metrics_locked(self) -> None:
        current = time.monotonic()
        if current - self._last_metric_publish_monotonic < 0.2:
            return
        self._last_metric_publish_monotonic = current
        self._publish_state(force=False)

    def _publish_state(self, *, force: bool) -> None:
        snapshot = self.snapshot()
        fingerprint = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock:
            if fingerprint == self._last_published_fingerprint:
                return
            self._last_published_fingerprint = fingerprint
        self._service._event_stream_registry.send_to_subscribers(
            "audio_runtime_state",
            {
                "event_id": self._service._next_stream_event_id(),
                "type": "audio_runtime_state",
                "data": snapshot,
            },
        )
