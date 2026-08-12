from __future__ import annotations

import json
import queue
import ssl
import struct
import threading
import time
import urllib.error
import urllib.request
from copy import deepcopy
from typing import Any

import numpy as np
import sounddevice
import soxr
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection, connect

from .config import AppConfig, find_runtime_access_token
from .log import emit_log


FRAME_BYTES = 640
CONFIG_POLL_SECONDS = 2.0
DEVICE_SCAN_SECONDS = 5.0
HEARTBEAT_SECONDS = 5.0


class ConnectorError(RuntimeError):
    pass


class ReconnectRequested(RuntimeError):
    pass


class AudioCapture:
    def __init__(
        self,
        *,
        device_index: int,
        source_sample_rate: float,
    ) -> None:
        self.source_sample_rate = source_sample_rate
        self.frames: queue.Queue[bytes] = queue.Queue(maxsize=50)
        self._error_lock = threading.Lock()
        self._error: str | None = None
        self._pcm_buffer = bytearray()
        self._resampler = (
            soxr.ResampleStream(
                source_sample_rate,
                16000.0,
                1,
                dtype="float32",
            )
            if source_sample_rate != 16000.0
            else None
        )
        self._stream = sounddevice.RawInputStream(
            device=device_index,
            samplerate=source_sample_rate,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )

    def start(self) -> None:
        self._stream.start()

    def close(self) -> None:
        try:
            self._stream.stop()
        finally:
            self._stream.close()
        self.clear_frames()

    def clear_frames(self) -> None:
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                return

    def error(self) -> str | None:
        with self._error_lock:
            if self._error is not None:
                return self._error
        if not self._stream.active:
            return "input_stream_inactive"
        return None

    def _callback(
        self,
        indata: Any,
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        del frames, time_info
        if status:
            self._set_error("portaudio_status")
            return
        try:
            samples = np.frombuffer(indata, dtype=np.float32).copy()
            if self._resampler is not None:
                # PortAudio の native rate を音声 stream 契約の 16 kHz へ揃える。
                samples = self._resampler.resample_chunk(samples, last=False)
            pcm = (
                np.clip(samples, -1.0, 1.0 - (1.0 / 32768.0))
                * 32768.0
            ).astype("<i2").tobytes()
            self._pcm_buffer.extend(pcm)
            while len(self._pcm_buffer) >= FRAME_BYTES:
                frame = bytes(self._pcm_buffer[:FRAME_BYTES])
                del self._pcm_buffer[:FRAME_BYTES]
                try:
                    self.frames.put_nowait(frame)
                except queue.Full:
                    self._set_error("capture_queue_overflow")
                    return
        except Exception:  # noqa: BLE001
            self._set_error("capture_callback_error")

    def _set_error(self, reason: str) -> None:
        with self._error_lock:
            self._error = reason


class MicrophoneConnector:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._access_token = config.server.access_token
        self._ssl_context = ssl.create_default_context()
        if not config.server.tls_verify:
            self._ssl_context.check_hostname = False
            self._ssl_context.verify_mode = ssl.CERT_NONE

    def run_forever(self) -> None:
        playback_thread = threading.Thread(
            target=self._run_playback_forever,
            name="otomekairo-local-audio-output",
            daemon=True,
        )
        playback_thread.start()
        while True:
            # 初回起動直後に server が token を生成する構成にも追従する。
            if self.config.server.access_token_env:
                refreshed_token = find_runtime_access_token(
                    self.config.server.access_token_env
                )
                if refreshed_token:
                    self._access_token = refreshed_token
            if not self._access_token:
                self._log("access tokenの発行を待機します。", level="INFO")
                time.sleep(self.config.server.reconnect_delay_seconds)
                continue
            try:
                self._run_connection()
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                self._log(f"接続を再試行します: {type(exc).__name__}", level="WARNING")
                time.sleep(self.config.server.reconnect_delay_seconds)

    def _run_connection(self) -> None:
        websocket_url = self.config.server.base_url.replace(
            "https://",
            "wss://",
            1,
        ) + "/api/audio/stream"
        with connect(
            websocket_url,
            ssl=self._ssl_context,
            additional_headers={
                "Authorization": f"Bearer {self._access_token}"
            },
            open_timeout=self.config.server.request_timeout_seconds,
            close_timeout=5.0,
        ) as websocket:
            self._log("音声streamへ接続しました。", level="INFO")
            self._run_session(websocket)

    def _run_session(self, websocket: ClientConnection) -> None:
        capture: AudioCapture | None = None
        selected_device: dict[str, str] | None = None
        lease_generation: int | None = None
        paused = True
        last_config_poll = 0.0
        last_device_scan = 0.0
        last_heartbeat = 0.0
        next_device_open = 0.0
        devices: list[dict[str, Any]] = []
        configured_enabled = False
        configured_device: dict[str, str] | None = None

        try:
            while True:
                now = time.monotonic()
                if now - last_config_poll >= CONFIG_POLL_SECONDS:
                    input_state = self._fetch_input_state()
                    configured_enabled = (
                        input_state["effective_source"]
                        == "local_microphone"
                    )
                    configured_device = input_state[
                        "local_input_device"
                    ]
                    last_config_poll = now
                    if (
                        capture is not None
                        and (
                            not configured_enabled
                            or configured_device != selected_device
                        )
                    ):
                        self._send_stop(websocket, lease_generation)
                        lease_generation = None
                        paused = True
                        capture.close()
                        capture = None
                        selected_device = None

                if now - last_device_scan >= DEVICE_SCAN_SECONDS:
                    devices = self._list_alsa_devices(direction="input")
                    output_devices = self._list_alsa_devices(direction="output")
                    # device index は再起動で変化するため wire へ出さない。
                    websocket.send(
                        json.dumps(
                            {
                                "type": "audio_device_catalog",
                                "client_id": self.config.client_id,
                                "input_devices": [
                                    {
                                        key: value
                                        for key, value in device.items()
                                        if key != "device_index"
                                    }
                                    for device in devices
                                ],
                                "output_devices": [
                                    {
                                        key: value
                                        for key, value in device.items()
                                        if key != "device_index"
                                    }
                                    for device in output_devices
                                ],
                            },
                            ensure_ascii=False,
                        )
                    )
                    last_device_scan = now

                if (
                    capture is None
                    and configured_enabled
                    and configured_device is not None
                    and now >= next_device_open
                ):
                    match = self._find_selected_device(
                        devices,
                        configured_device,
                    )
                    if match is None:
                        next_device_open = now + DEVICE_SCAN_SECONDS
                    else:
                        try:
                            capture = AudioCapture(
                                device_index=match["device_index"],
                                source_sample_rate=match[
                                    "default_sample_rate"
                                ],
                            )
                            capture.start()
                            selected_device = deepcopy(configured_device)
                            websocket.send(
                                json.dumps(
                                    self._audio_start_message(
                                        device=selected_device,
                                        source_sample_rate=(
                                            capture.source_sample_rate
                                        ),
                                    ),
                                    ensure_ascii=False,
                                )
                            )
                            paused = True
                            self._log("選択マイクを開始しました。", level="INFO")
                        except Exception as exc:  # noqa: BLE001
                            if capture is not None:
                                capture.close()
                            capture = None
                            selected_device = None
                            next_device_open = now + DEVICE_SCAN_SECONDS
                            self._log(
                                "選択マイクを開けませんでした: "
                                f"{type(exc).__name__}",
                                level="WARNING",
                            )

                control = self._receive_control(websocket)
                if control is not None:
                    control_type = control.get("type")
                    if control_type == "audio_started":
                        lease_generation = self._lease_generation(control)
                        paused_reason = control.get("paused_reason")
                        if (
                            "paused_reason" not in control
                            or (
                                paused_reason is not None
                                and not isinstance(paused_reason, str)
                            )
                        ):
                            raise ConnectorError(
                                "audio_started paused_reason is invalid."
                            )
                        paused = paused_reason is not None
                        last_heartbeat = now
                        if capture is not None:
                            capture.clear_frames()
                    elif control_type == "audio_paused":
                        paused = True
                        if capture is not None:
                            capture.clear_frames()
                        if control.get("reason") in {
                            "source_switched",
                            "settings_reloaded",
                        }:
                            raise ReconnectRequested(
                                "The local audio lease was revoked."
                            )
                    elif control_type == "audio_resumed":
                        if self._lease_generation(control) != lease_generation:
                            raise ConnectorError(
                                "audio_resumed generation mismatch."
                            )
                        paused = False
                        if capture is not None:
                            capture.clear_frames()
                    elif control_type == "audio_heartbeat_ack":
                        if self._lease_generation(control) != lease_generation:
                            raise ConnectorError(
                                "heartbeat generation mismatch."
                            )
                    elif control_type == "audio_stopped":
                        lease_generation = None
                        paused = True
                    elif control_type == "audio_error":
                        raise ReconnectRequested(
                            f"server audio error: {control.get('code')}"
                        )
                    else:
                        raise ConnectorError(
                            "server returned an unsupported audio control."
                        )

                if (
                    lease_generation is not None
                    and now - last_heartbeat >= HEARTBEAT_SECONDS
                ):
                    websocket.send(
                        json.dumps(
                            {
                                "type": "audio_heartbeat",
                                "lease_generation": lease_generation,
                            }
                        )
                    )
                    last_heartbeat = now

                if capture is not None:
                    capture_error = capture.error()
                    if capture_error is not None:
                        self._log(
                            f"選択マイクを再取得します: {capture_error}",
                            level="WARNING",
                        )
                        self._send_stop(websocket, lease_generation)
                        lease_generation = None
                        paused = True
                        capture.close()
                        capture = None
                        selected_device = None
                        next_device_open = now + DEVICE_SCAN_SECONDS
                    elif not paused and lease_generation is not None:
                        self._send_available_frames(websocket, capture)
                    else:
                        capture.clear_frames()

                time.sleep(0.005)
        finally:
            if capture is not None:
                capture.close()

    def _fetch_input_state(self) -> dict[str, Any]:
        request = urllib.request.Request(
            self.config.server.base_url + "/api/audio/input-state",
            method="GET",
            headers={
                "Accept": "application/json",
                "Authorization": (
                    f"Bearer {self._access_token}"
                ),
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                context=self._ssl_context,
                timeout=self.config.server.request_timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ConnectorError(
                "audio input state could not be fetched."
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("ok") is not True
            or not isinstance(payload.get("data"), dict)
        ):
            raise ConnectorError("audio input state response is invalid.")
        state = payload["data"]
        effective_source = state.get("effective_source")
        device = state.get("local_input_device")
        if effective_source not in {
            "local_microphone",
            "console_microphone",
            "web_microphone",
        }:
            raise ConnectorError("effective_source is invalid.")
        if device is not None and (
            not isinstance(device, dict)
            or set(device) != {"host_api", "name"}
            or not all(
                isinstance(device.get(key), str) and device[key]
                for key in ("host_api", "name")
            )
        ):
            raise ConnectorError("local_input_device is invalid.")
        return {
            "effective_source": effective_source,
            "local_input_device": deepcopy(device),
        }

    def _list_alsa_devices(self, *, direction: str) -> list[dict[str, Any]]:
        if direction not in {"input", "output"}:
            raise ValueError("Unknown audio device direction.")
        host_apis = sounddevice.query_hostapis()
        devices = sounddevice.query_devices()
        results: list[dict[str, Any]] = []
        channels_field = f"max_{direction}_channels"
        for device_index, device in enumerate(devices):
            max_channels = int(device[channels_field])
            host_api = host_apis[int(device["hostapi"])]["name"]
            if host_api != "ALSA" or max_channels < 1:
                continue
            results.append(
                {
                    "host_api": host_api,
                    "name": str(device["name"]),
                    channels_field: max_channels,
                    "default_sample_rate": float(
                        device["default_samplerate"]
                    ),
                    "device_index": device_index,
                }
            )
        return results

    def _run_playback_forever(self) -> None:
        while True:
            try:
                if self.config.server.access_token_env:
                    refreshed_token = find_runtime_access_token(
                        self.config.server.access_token_env
                    )
                    if refreshed_token:
                        self._access_token = refreshed_token
                if not self._access_token:
                    time.sleep(self.config.server.reconnect_delay_seconds)
                    continue
                output_state = self._fetch_output_state()
                selected = output_state["local_output_device"]
                devices = self._list_alsa_devices(direction="output")
                matched = (
                    self._find_selected_device(devices, selected)
                    if selected is not None
                    else None
                )
                # CocoroConsole 未接続時のローカル出力に備えて待機購読する。
                if output_state["destination"] not in {
                    "otomekairo",
                    "cocoro_console",
                } or matched is None:
                    time.sleep(CONFIG_POLL_SECONDS)
                    continue
                self._run_playback_connection(selected, matched["device_index"])
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                self._log(f"音声出力を再接続します: {type(exc).__name__}", level="WARNING")
                time.sleep(self.config.server.reconnect_delay_seconds)

    def _run_playback_connection(
        self,
        selected_device: dict[str, str],
        device_index: int,
    ) -> None:
        websocket_url = self.config.server.base_url.replace(
            "https://",
            "wss://",
            1,
        ) + "/api/events/stream"
        with connect(
            websocket_url,
            ssl=self._ssl_context,
            additional_headers={
                "Authorization": f"Bearer {self._access_token}"
            },
            open_timeout=self.config.server.request_timeout_seconds,
            close_timeout=5.0,
        ) as websocket:
            websocket.send(
                json.dumps(
                    {
                        "type": "hello",
                        "client_id": f"{self.config.client_id}:audio-output",
                        "client_kind": "otomekairo_audio",
                        "caps": [],
                        "event_subscriptions": ["assistant_audio"],
                    }
                )
            )
            pending_metadata: dict[str, Any] | None = None
            last_config_poll = 0.0
            while True:
                now = time.monotonic()
                if now - last_config_poll >= CONFIG_POLL_SECONDS:
                    current = self._fetch_output_state()
                    current_devices = self._list_alsa_devices(direction="output")
                    current_match = (
                        self._find_selected_device(
                            current_devices,
                            current["local_output_device"],
                        )
                        if current["local_output_device"] is not None
                        else None
                    )
                    if (
                        current["destination"] not in {
                            "otomekairo",
                            "cocoro_console",
                        }
                        or current["local_output_device"] != selected_device
                        or current_match is None
                    ):
                        raise ReconnectRequested("Audio output settings changed.")
                    device_index = current_match["device_index"]
                    last_config_poll = now
                try:
                    message = websocket.recv(timeout=CONFIG_POLL_SECONDS)
                except TimeoutError:
                    continue
                if isinstance(message, str):
                    event = json.loads(message)
                    if event.get("type") != "assistant_audio":
                        continue
                    data = event.get("data")
                    pending_metadata = (
                        data
                        if isinstance(data, dict)
                        and data.get("status") == "succeeded"
                        else None
                    )
                    continue
                if not isinstance(message, bytes) or pending_metadata is None:
                    raise ConnectorError("assistant_audio binary ordering is invalid.")
                metadata = pending_metadata
                pending_metadata = None
                if metadata.get("byte_count") != len(message):
                    raise ConnectorError("assistant_audio byte_count mismatch.")
                self._play_wav(message, device_index=device_index)

    def _fetch_output_state(self) -> dict[str, Any]:
        request = urllib.request.Request(
            self.config.server.base_url + "/api/audio/output-state",
            method="GET",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._access_token}",
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                context=self._ssl_context,
                timeout=self.config.server.request_timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ConnectorError("audio output state could not be fetched.") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or data.get("destination") not in {
            "otomekairo",
            "cocoro_console",
            "browser",
        }:
            raise ConnectorError("audio output state response is invalid.")
        device = data.get("local_output_device")
        if device is not None and (
            not isinstance(device, dict)
            or set(device) != {"host_api", "name"}
            or not all(
                isinstance(device.get(key), str) and device[key]
                for key in ("host_api", "name")
            )
        ):
            raise ConnectorError("local_output_device is invalid.")
        return {
            "destination": data["destination"],
            "local_output_device": deepcopy(device),
        }

    def _play_wav(self, payload: bytes, *, device_index: int) -> None:
        audio_format = channels = sample_rate = bits_per_sample = 0
        audio_data: bytes | None = None
        if len(payload) < 12 or payload[:4] != b"RIFF" or payload[8:12] != b"WAVE":
            raise ConnectorError("assistant_audio is not RIFF/WAVE.")
        offset = 12
        while offset + 8 <= len(payload):
            chunk_id = payload[offset : offset + 4]
            chunk_size = struct.unpack_from("<I", payload, offset + 4)[0]
            chunk_start = offset + 8
            chunk_end = chunk_start + chunk_size
            if chunk_end > len(payload):
                raise ConnectorError("assistant_audio chunk is invalid.")
            if chunk_id == b"fmt " and chunk_size >= 16:
                audio_format, channels, sample_rate, _, _, bits_per_sample = (
                    struct.unpack_from("<HHIIHH", payload, chunk_start)
                )
            elif chunk_id == b"data" and audio_data is None:
                audio_data = payload[chunk_start:chunk_end]
            offset = chunk_end + (chunk_size % 2)
        dtype = (
            "int16"
            if audio_format == 1 and bits_per_sample == 16
            else "float32"
            if audio_format == 3 and bits_per_sample == 32
            else None
        )
        if dtype is None or channels < 1 or sample_rate < 1 or not audio_data:
            raise ConnectorError("assistant_audio format is unsupported.")
        with sounddevice.RawOutputStream(
            device=device_index,
            samplerate=sample_rate,
            channels=channels,
            dtype=dtype,
        ) as output:
            output.write(audio_data)

    def _find_selected_device(
        self,
        devices: list[dict[str, Any]],
        selected: dict[str, str],
    ) -> dict[str, Any] | None:
        matches = [
            device
            for device in devices
            if device["host_api"] == selected["host_api"]
            and device["name"] == selected["name"]
        ]
        # 同名 device が複数ある場合は誤選択を避けて利用不能として扱う。
        return matches[0] if len(matches) == 1 else None

    def _audio_start_message(
        self,
        *,
        device: dict[str, str],
        source_sample_rate: float,
    ) -> dict[str, Any]:
        return {
            "type": "audio_start",
            "protocol_version": "2",
            "client_id": self.config.client_id,
            "input_source": "local_microphone",
            "input_session_id": None,
            "format": {
                "sample_rate": 16000,
                "channels": 1,
                "sample_format": "pcm_s16le",
                "frame_duration_ms": 20,
                "bytes_per_frame": FRAME_BYTES,
            },
            "device": device,
            "capture_settings": {
                "source_sample_rate": source_sample_rate,
            },
        }

    def _receive_control(
        self,
        websocket: ClientConnection,
    ) -> dict[str, Any] | None:
        try:
            message = websocket.recv(timeout=0.001)
        except TimeoutError:
            return None
        except ConnectionClosed as exc:
            raise ReconnectRequested("Audio WebSocket was closed.") from exc
        if not isinstance(message, str):
            raise ConnectorError("server control message must be text.")
        try:
            payload = json.loads(message)
        except json.JSONDecodeError as exc:
            raise ConnectorError("server control message is invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise ConnectorError("server control message must be an object.")
        return payload

    def _send_available_frames(
        self,
        websocket: ClientConnection,
        capture: AudioCapture,
    ) -> None:
        # callbackを塞がない範囲で現在queueを即時に送る。
        for _ in range(10):
            try:
                frame = capture.frames.get_nowait()
            except queue.Empty:
                return
            websocket.send(frame)

    def _send_stop(
        self,
        websocket: ClientConnection,
        lease_generation: int | None,
    ) -> None:
        if lease_generation is None:
            return
        try:
            websocket.send(
                json.dumps(
                    {
                        "type": "audio_stop",
                        "lease_generation": lease_generation,
                    }
                )
            )
        except ConnectionClosed:
            return

    def _lease_generation(self, payload: dict[str, Any]) -> int:
        value = payload.get("lease_generation")
        if type(value) is not int or value < 1:
            raise ConnectorError("lease_generation is invalid.")
        return value

    def _log(self, message: str, *, level: str = "INFO") -> None:
        emit_log("microphone-connector", message, level=level)
