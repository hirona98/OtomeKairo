#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable


PNG_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlAb9sAAAAASUVORK5CYII="
)
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WAIT_SERVER_TIMEOUT_SECONDS = 20.0
WAIT_QUEUE_DRAIN_TIMEOUT_SECONDS = 30.0
WAIT_CAPTURE_RECOVERY_TIMEOUT_SECONDS = 20.0
WAIT_RESTART_PENDING_TIMEOUT_SECONDS = 8.0
WAIT_DESKTOP_WATCH_PROBE_TIMEOUT_SECONDS = 20.0
WAIT_PENDING_INTENT_SEED_TIMEOUT_SECONDS = 20.0
WAIT_EXTERNAL_STATUS_PROBE_TIMEOUT_SECONDS = 20.0

PROFILE_DEFAULTS: dict[str, dict[str, int | float]] = {
    "smoke": {
        "run_seconds": 75,
        "conversation_interval_seconds": 6.0,
        "desktop_watch_interval_seconds": 2,
        "wake_interval_seconds": 60,
        "min_conversation_cycles": 4,
        "capture_timeout_failures": 1,
        "capture_mismatch_failures": 1,
        "capture_invalid_images_failures": 1,
        "capture_invalid_error_failures": 1,
        "capture_unknown_request_failures": 1,
        "restart_burst_conversations": 8,
        "multiple_client_pause_seconds": 7.0,
    },
    "soak": {
        "run_seconds": 600,
        "conversation_interval_seconds": 12.0,
        "desktop_watch_interval_seconds": 3,
        "wake_interval_seconds": 60,
        "min_conversation_cycles": 20,
        "capture_timeout_failures": 1,
        "capture_mismatch_failures": 1,
        "capture_invalid_images_failures": 1,
        "capture_invalid_error_failures": 1,
        "capture_unknown_request_failures": 1,
        "restart_burst_conversations": 12,
        "multiple_client_pause_seconds": 20.0,
    },
}


class SmokeError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"[long-smoke] {message}", flush=True)


class JsonApiClient:
    def __init__(self, *, host: str, port: int) -> None:
        self.base_url = f"https://{host}:{port}"
        self.token: str | None = None
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    def get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path, payload=None)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, payload=payload)

    def patch(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", path, payload=payload)

    def put(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", path, payload=payload)

    def post_expect_error(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        status_code: int,
        error_code: str,
    ) -> None:
        actual_status_code, envelope = self._request_envelope("POST", path, payload=payload)
        if actual_status_code != status_code:
            raise SmokeError(
                f"POST {path} returned HTTP {actual_status_code}, expected HTTP {status_code}."
            )
        if envelope.get("ok") is not False:
            raise SmokeError(f"POST {path} unexpectedly succeeded.")
        error = envelope.get("error")
        if not isinstance(error, dict):
            raise SmokeError(f"POST {path} returned an invalid error envelope.")
        actual_error_code = error.get("code")
        if actual_error_code != error_code:
            raise SmokeError(
                f"POST {path} returned error_code={actual_error_code}, expected {error_code}."
            )

    def post_expect_success_empty_data(self, path: str, payload: dict[str, Any]) -> None:
        status_code, envelope = self._request_envelope("POST", path, payload=payload)
        if status_code >= 400:
            raise SmokeError(f"POST {path} returned HTTP {status_code}, expected success.")
        if envelope.get("ok") is not True:
            raise SmokeError(f"POST {path} did not return a success envelope.")
        data = envelope.get("data")
        if data != {}:
            raise SmokeError(f"POST {path} did not return an empty data payload.")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        status_code, envelope = self._request_envelope(method, path, payload=payload)
        if status_code >= 400:
            message = envelope
            error = envelope.get("error", {})
            if isinstance(error, dict):
                error_code = error.get("code")
                error_message = error.get("message")
                if error_code or error_message:
                    message = f"{error_code}: {error_message}"
            raise SmokeError(f"{method} {path} failed: HTTP {status_code} {message}")
        if not isinstance(envelope, dict) or not envelope.get("ok"):
            raise SmokeError(f"{method} {path} returned an invalid envelope.")
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise SmokeError(f"{method} {path} returned a non-object data payload.")
        return data

    def _request_envelope(self, method: str, path: str, payload: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        body = None
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url=url, data=body, headers=headers, method=method)
        raw_body: str
        status_code: int
        try:
            with urllib.request.urlopen(request, context=self.ssl_context, timeout=10.0) as response:
                status_code = int(response.getcode())
                raw_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status_code = int(exc.code)
            raw_body = exc.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            raise SmokeError(f"{method} {path} failed: {exc.reason}") from exc

        try:
            envelope = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise SmokeError(f"{method} {path} returned invalid JSON.") from exc
        if not isinstance(envelope, dict):
            raise SmokeError(f"{method} {path} returned a non-object envelope.")
        return status_code, envelope


class SimpleWebSocketClient:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        token: str,
        on_event: Callable[[dict[str, Any]], None],
    ) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.on_event = on_event
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        self._socket: ssl.SSLSocket | None = None
        self._send_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self.error: str | None = None

    def connect(self, *, client_id: str, caps: list[dict[str, str]]) -> None:
        raw_socket = socket.create_connection((self.host, self.port), timeout=10.0)
        websocket = self.ssl_context.wrap_socket(raw_socket, server_hostname=self.host)
        websocket.settimeout(1.0)
        self._socket = websocket

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET /api/events/stream HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Authorization: Bearer {self.token}\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        websocket.sendall(request.encode("ascii"))
        status_code, headers = self._read_http_response()
        if status_code != 101:
            raise SmokeError(f"event stream handshake failed with HTTP {status_code}.")
        expected_accept = base64.b64encode(
            hashlib.sha1(f"{key}{WEBSOCKET_GUID}".encode("utf-8")).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected_accept:
            raise SmokeError("event stream handshake returned an invalid Sec-WebSocket-Accept.")

        self.send_json(
            {
                "type": "hello",
                "client_id": client_id,
                "caps": caps,
            }
        )
        self._reader_thread = threading.Thread(target=self._reader_loop, name="long-smoke-event-reader", daemon=True)
        self._reader_thread.start()

    def send_json(self, payload: dict[str, Any]) -> None:
        self._send_text(json.dumps(payload, ensure_ascii=False))

    def close(self) -> None:
        self._stop_event.set()
        websocket = self._socket
        if websocket is None:
            return
        try:
            self._send_frame(opcode=0x8, payload=b"")
        except (OSError, SmokeError):
            pass
        try:
            websocket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        websocket.close()
        self._socket = None
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)

    def _read_http_response(self) -> tuple[int, dict[str, str]]:
        websocket = self._require_socket()
        chunks = bytearray()
        while b"\r\n\r\n" not in chunks:
            try:
                chunk = websocket.recv(4096)
            except socket.timeout as exc:
                raise SmokeError("event stream handshake timed out.") from exc
            if not chunk:
                raise SmokeError("event stream handshake closed before headers completed.")
            chunks.extend(chunk)

        header_text = bytes(chunks).split(b"\r\n\r\n", 1)[0].decode("ascii")
        lines = header_text.split("\r\n")
        status_line = lines[0]
        try:
            status_code = int(status_line.split()[1])
        except (IndexError, ValueError) as exc:
            raise SmokeError(f"invalid event stream status line: {status_line}") from exc

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        return status_code, headers

    def _reader_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                opcode, payload = self._read_frame()
                if opcode == 0x8:
                    return
                if opcode == 0x9:
                    self._send_frame(opcode=0xA, payload=payload)
                    continue
                if opcode == 0xA:
                    continue
                if opcode != 0x1:
                    raise SmokeError(f"unsupported websocket opcode: {opcode}")
                event = json.loads(payload.decode("utf-8"))
                if not isinstance(event, dict):
                    raise SmokeError("event stream payload must be a JSON object.")
                self.on_event(event)
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            self._stop_event.set()

    def _send_text(self, text: str) -> None:
        self._send_frame(opcode=0x1, payload=text.encode("utf-8"))

    def _send_frame(self, *, opcode: int, payload: bytes) -> None:
        websocket = self._require_socket()
        mask = os.urandom(4)
        masked_payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        header = bytearray([0x80 | (opcode & 0x0F)])
        payload_length = len(payload)
        if payload_length < 126:
            header.append(0x80 | payload_length)
        elif payload_length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", payload_length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", payload_length))
        header.extend(mask)
        with self._send_lock:
            websocket.sendall(bytes(header) + masked_payload)

    def _read_frame(self) -> tuple[int, bytes]:
        header = self._read_exact(2)
        first_byte = header[0]
        second_byte = header[1]
        opcode = first_byte & 0x0F
        payload_length = second_byte & 0x7F
        masked = (second_byte & 0x80) != 0
        if masked:
            raise SmokeError("server websocket frame must not be masked.")
        if payload_length == 126:
            payload_length = struct.unpack("!H", self._read_exact(2))[0]
        elif payload_length == 127:
            payload_length = struct.unpack("!Q", self._read_exact(8))[0]
        return opcode, self._read_exact(payload_length)

    def _read_exact(self, size: int) -> bytes:
        websocket = self._require_socket()
        chunks = bytearray()
        while len(chunks) < size and not self._stop_event.is_set():
            try:
                chunk = websocket.recv(size - len(chunks))
            except socket.timeout:
                continue
            if not chunk:
                raise SmokeError("event stream socket closed unexpectedly.")
            chunks.extend(chunk)
        if len(chunks) < size:
            raise SmokeError("event stream socket closed before the frame completed.")
        return bytes(chunks)

    def _require_socket(self) -> ssl.SSLSocket:
        if self._socket is None:
            raise SmokeError("event stream is not connected.")
        return self._socket


class LongSmokeRunner:
    def __init__(self, *, repo_root: Path, artifact_dir: Path, args: argparse.Namespace) -> None:
        self.repo_root = repo_root
        self.artifact_dir = artifact_dir
        self.args = args
        self.host = "127.0.0.1"
        self.port = args.port if args.port is not None else self._find_free_port()
        self.data_dir = artifact_dir / "data"
        self.cert_file = artifact_dir / "cert.pem"
        self.key_file = artifact_dir / "key.pem"
        self.server_log_path = artifact_dir / "server.log"
        self.summary_path = artifact_dir / "summary.json"
        self.api = JsonApiClient(host=self.host, port=self.port)
        self.server_process: subprocess.Popen[str] | None = None
        self.server_log_handle: Any | None = None
        self.seed_data_dir: Path | None = args.seed_data_dir.resolve() if args.seed_data_dir is not None else None
        self.event_client: SimpleWebSocketClient | None = None
        self.secondary_event_client: SimpleWebSocketClient | None = None
        self.capture_request_count = 0
        self.capture_response_count = 0
        self.external_status_request_count = 0
        self.external_status_response_count = 0
        self.desktop_watch_event_count = 0
        self.conversation_cycle_ids: list[str] = []
        self.restart_probe_cycle_ids: list[str] = []
        self.pending_intent_seed_cycle_ids: list[str] = []
        self.capture_timeout_request_ids: list[str] = []
        self.capture_mismatch_request_ids: list[str] = []
        self.capture_invalid_images_request_ids: list[str] = []
        self.capture_invalid_error_request_ids: list[str] = []
        self.capture_unknown_request_ids: list[str] = []
        self.external_status_request_ids: list[str] = []
        self.capture_timeout_recovered = False
        self.remaining_capture_timeouts = args.capture_timeout_failures
        self.remaining_capture_mismatches = args.capture_mismatch_failures
        self.remaining_invalid_images_failures = args.capture_invalid_images_failures
        self.remaining_invalid_error_failures = args.capture_invalid_error_failures
        self.remaining_unknown_request_failures = args.capture_unknown_request_failures
        self.restart_count = 0
        self.restart_probe_pending_before_restart: int | None = None
        self.restart_probe_in_progress_before_restart = False
        self.multiple_client_pause_verified = False
        self.multiple_client_resume_verified = False
        self.desktop_watch_capability_probe_cycle_id: str | None = None
        self.desktop_watch_pending_intent_probe_cycle_id: str | None = None
        self.desktop_watch_capability_probe_verified = False
        self.desktop_watch_pending_intent_probe_verified = False
        self.external_status_probe_conversation_cycle_id: str | None = None
        self.external_status_probe_followup_cycle_id: str | None = None
        self.external_status_probe_verified = False
        self.external_status_multi_service_verified = False
        self.external_status_persisted_integration_keys: list[str] = []
        self.editor_state_mode_used = args.editor_state_mode
        self.selected_model_preset_id: str | None = None
        self.selected_memory_set_id: str | None = None
        self._capture_context_overrides: list[dict[str, Any]] = []
        self._external_status_overrides: list[dict[str, Any]] = []
        self._capture_lock = threading.Lock()
        self._external_status_lock = threading.Lock()

    def run(self) -> dict[str, Any]:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._prepare_data_dir()

        try:
            self._ensure_tls_material()
            self._start_server()
            self._wait_server_ready()
            self._bootstrap()
            self._configure_editor_state()
            self._connect_desktop_client()
            self._exercise_capture_timeout_recovery()
            self._run_restart_probe()
            self._exercise_desktop_watch_event_boundaries()
            self._exercise_multiple_client_boundary()
            self._exercise_external_status_followup()
            self._run_conversations()
            self._wait_for_memory_jobs_to_drain()
            summary = self._collect_summary()
            self._write_summary(summary)
            self._assert_summary(summary)
            return summary
        finally:
            if self.event_client is not None:
                self.event_client.close()
            if self.secondary_event_client is not None:
                self.secondary_event_client.close()
            self._stop_server()

    def _prepare_data_dir(self) -> None:
        if self.seed_data_dir is not None:
            if not self.seed_data_dir.exists() or not self.seed_data_dir.is_dir():
                raise SmokeError(f"seed data dir does not exist: {self.seed_data_dir}")
            if self.seed_data_dir.resolve() == self.data_dir.resolve():
                raise SmokeError("seed data dir must be different from the artifact data dir.")
            shutil.copytree(self.seed_data_dir, self.data_dir, dirs_exist_ok=True)
            log(f"seed data dir copied from {self.seed_data_dir}")
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_tls_material(self) -> None:
        if self.cert_file.exists() and self.key_file.exists():
            return
        openssl = shutil.which("openssl")
        if openssl is None:
            raise SmokeError("openssl is required to generate a temporary TLS certificate.")
        command = [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(self.key_file),
            "-out",
            str(self.cert_file),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=127.0.0.1",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise SmokeError(f"failed to generate TLS certificate: {result.stderr.strip()}")

    def _start_server(self) -> None:
        python_bin = self.repo_root / ".venv" / "bin" / "python"
        if not python_bin.exists():
            raise SmokeError(".venv/bin/python が見つかりません。先に ./scripts/setup_venv.sh を実行してください。")
        log_mode = "a" if self.server_log_path.exists() else "w"
        self.server_log_handle = self.server_log_path.open(log_mode, encoding="utf-8")
        env = os.environ.copy()
        env["OTOMEKAIRO_HOST"] = self.host
        env["OTOMEKAIRO_PORT"] = str(self.port)
        env["OTOMEKAIRO_TLS_CERT_FILE"] = str(self.cert_file)
        env["OTOMEKAIRO_TLS_KEY_FILE"] = str(self.key_file)
        env["OTOMEKAIRO_DATA_DIR"] = str(self.data_dir)
        # smoke では selected pending-intent を決定的に踏むため、再評価待機を短縮する。
        env["OTOMEKAIRO_PENDING_INTENT_NOT_BEFORE_MINUTES"] = "0"
        env["PYTHONPATH"] = str(self.repo_root / "src")
        self.server_process = subprocess.Popen(
            [str(python_bin), "-m", "otomekairo.run"],
            cwd=self.repo_root,
            env=env,
            stdout=self.server_log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log(f"server started on https://{self.host}:{self.port}")

    def _wait_server_ready(self) -> None:
        deadline = time.monotonic() + WAIT_SERVER_TIMEOUT_SECONDS
        last_error = "server did not respond"
        while time.monotonic() < deadline:
            self._assert_server_running()
            try:
                self.api.get("/api/bootstrap/probe")
                return
            except SmokeError as exc:
                last_error = str(exc)
                time.sleep(0.5)
        raise SmokeError(f"server did not become ready within {WAIT_SERVER_TIMEOUT_SECONDS:.0f}s: {last_error}")

    def _bootstrap(self) -> None:
        identity = self.api.get("/api/bootstrap/server-identity")
        if identity.get("console_access_token_issued") is True:
            if not isinstance(self.api.token, str) or not self.api.token:
                raise SmokeError("server is already registered and the smoke client does not hold a token.")
            log(
                "bootstrap reused existing token"
                f" server_id={identity.get('server_id')} token_issued=True"
            )
            return

        bootstrap = self.api.post("/api/bootstrap/register-first-console", {})
        token = bootstrap.get("console_access_token")
        if not isinstance(token, str) or not token:
            raise SmokeError("bootstrap did not return console_access_token.")
        self.api.token = token
        log(
            "bootstrap completed"
            f" server_id={identity.get('server_id')} token_issued={identity.get('console_access_token_issued')}"
        )

    def _configure_editor_state(self) -> None:
        editor_state = self.api.get("/api/config/editor-state")
        current = editor_state["current"]
        self.selected_model_preset_id = current["selected_model_preset_id"]
        self.selected_memory_set_id = current["selected_memory_set_id"]

        if self.args.editor_state_mode == "mock":
            for model_preset in editor_state["model_presets"]:
                if model_preset["model_preset_id"] != self.selected_model_preset_id:
                    continue
                for role_name, role_definition in model_preset["roles"].items():
                    role_definition["model"] = f"mock-{role_name}"
                    role_definition["api_key"] = ""

            for memory_set in editor_state["memory_sets"]:
                if memory_set["memory_set_id"] != self.selected_memory_set_id:
                    continue
                embedding = memory_set["embedding"]
                embedding["model"] = "mock-embedding"
                embedding["api_key"] = ""
        else:
            self._assert_current_editor_state_ready(editor_state)

        current["wake_policy"] = {
            "mode": "interval",
            "interval_seconds": self.args.wake_interval_seconds,
        }
        current["desktop_watch"] = {
            "enabled": True,
            "interval_seconds": self.args.desktop_watch_interval_seconds,
        }
        self.api.put("/api/config/editor-state", editor_state)

        status = self.api.get("/api/status")
        runtime_summary = status["runtime_summary"]
        if not runtime_summary.get("wake_scheduler_active"):
            raise SmokeError("wake scheduler did not become active after editor-state update.")
        if not runtime_summary.get("memory_job_worker_active"):
            raise SmokeError("memory worker is not active after server startup.")
        log(
            "editor-state applied"
            f" mode={self.args.editor_state_mode}"
            f" profile={self.args.profile}"
            f" selected_model_preset_id={self.selected_model_preset_id}"
            f" selected_memory_set_id={self.selected_memory_set_id}"
        )

    def _assert_current_editor_state_ready(self, editor_state: dict[str, Any]) -> None:
        selected_model_preset_id = self.selected_model_preset_id
        selected_memory_set_id = self.selected_memory_set_id
        if not isinstance(selected_model_preset_id, str) or not selected_model_preset_id:
            raise SmokeError("current editor-state does not have a selected_model_preset_id.")
        if not isinstance(selected_memory_set_id, str) or not selected_memory_set_id:
            raise SmokeError("current editor-state does not have a selected_memory_set_id.")

        selected_model_preset = None
        for model_preset in editor_state["model_presets"]:
            if model_preset["model_preset_id"] == selected_model_preset_id:
                selected_model_preset = model_preset
                break
        if not isinstance(selected_model_preset, dict):
            raise SmokeError(f"selected model preset was not found: {selected_model_preset_id}")

        selected_memory_set = None
        for memory_set in editor_state["memory_sets"]:
            if memory_set["memory_set_id"] == selected_memory_set_id:
                selected_memory_set = memory_set
                break
        if not isinstance(selected_memory_set, dict):
            raise SmokeError(f"selected memory set was not found: {selected_memory_set_id}")

        roles = selected_model_preset.get("roles", {})
        if not isinstance(roles, dict):
            raise SmokeError(f"selected model preset has invalid roles: {selected_model_preset_id}")
        for role_name, role_definition in roles.items():
            if not isinstance(role_definition, dict):
                raise SmokeError(f"selected model preset role is invalid: {role_name}")
            self._assert_role_definition_ready(
                role_definition=role_definition,
                label=f"model preset role {role_name}",
            )

        embedding_definition = selected_memory_set.get("embedding")
        if not isinstance(embedding_definition, dict):
            raise SmokeError(f"selected memory set embedding is invalid: {selected_memory_set_id}")
        self._assert_role_definition_ready(role_definition=embedding_definition, label="memory embedding")

    def _assert_role_definition_ready(self, *, role_definition: dict[str, Any], label: str) -> None:
        model = role_definition.get("model")
        if not isinstance(model, str) or not model.strip():
            raise SmokeError(f"{label} does not have a valid model.")
        normalized_model = model.strip()
        if normalized_model.startswith("mock"):
            return
        api_key = role_definition.get("api_key")
        if not isinstance(api_key, str) or not api_key.strip():
            raise SmokeError(f"{label} requires api_key in current editor-state mode: model={normalized_model}")

    def _connect_desktop_client(self) -> None:
        token = self.api.token
        if token is None:
            raise SmokeError("desktop client cannot connect before bootstrap.")
        self.event_client = self._connect_event_client(
            client_id=self.args.desktop_client_id,
            client_label="primary",
        )
        log(f"desktop client connected client_id={self.args.desktop_client_id}")

    def _connect_event_client(self, *, client_id: str, client_label: str) -> SimpleWebSocketClient:
        token = self.api.token
        if token is None:
            raise SmokeError("desktop client cannot connect before bootstrap.")
        client = SimpleWebSocketClient(
            host=self.host,
            port=self.port,
            token=token,
            on_event=lambda event: self._handle_server_event(
                client_label=client_label,
                connected_client_id=client_id,
                event=event,
            ),
        )
        caps = [{"id": "vision.capture", "version": "1"}]
        if client_label == "primary":
            caps.append({"id": "external.status", "version": "1"})
        client.connect(client_id=client_id, caps=caps)
        return client

    def _handle_server_event(
        self,
        *,
        client_label: str,
        connected_client_id: str,
        event: dict[str, Any],
    ) -> None:
        event_type = event.get("type")
        data = event.get("data", {})
        if event_type == "vision.capture_request":
            if client_label != "primary":
                raise SmokeError(f"{client_label} desktop client unexpectedly received capture_request.")
            request_id = data.get("request_id")
            capability_id = data.get("capability_id")
            if not isinstance(request_id, str) or not request_id:
                raise SmokeError("capture_request did not include request_id.")
            if capability_id != "vision.capture":
                raise SmokeError(f"capture_request capability_id was invalid: {capability_id}")
            with self._capture_lock:
                sequence = self.capture_request_count
                self.capture_request_count += 1
                if self.remaining_capture_timeouts > 0:
                    self.remaining_capture_timeouts -= 1
                    self.capture_timeout_request_ids.append(request_id)
                    log(f"intentionally dropped capture-response request_id={request_id}")
                    return
                should_inject_mismatch = self.remaining_capture_mismatches > 0
                if should_inject_mismatch:
                    self.remaining_capture_mismatches -= 1
                    self.capture_mismatch_request_ids.append(request_id)
                else:
                    should_inject_mismatch = False
                should_inject_invalid_images = self.remaining_invalid_images_failures > 0
                if should_inject_invalid_images:
                    self.remaining_invalid_images_failures -= 1
                    self.capture_invalid_images_request_ids.append(request_id)
                else:
                    should_inject_invalid_images = False
                should_inject_invalid_error = self.remaining_invalid_error_failures > 0
                if should_inject_invalid_error:
                    self.remaining_invalid_error_failures -= 1
                    self.capture_invalid_error_request_ids.append(request_id)
                else:
                    should_inject_invalid_error = False
                should_inject_unknown_request = self.remaining_unknown_request_failures > 0
                if should_inject_unknown_request:
                    self.remaining_unknown_request_failures -= 1
                    self.capture_unknown_request_ids.append(request_id)
                else:
                    should_inject_unknown_request = False
                override = self._capture_context_overrides.pop(0) if self._capture_context_overrides else None
            client_context = (
                dict(override["client_context"])
                if isinstance(override, dict) and isinstance(override.get("client_context"), dict)
                else {
                    "active_app": f"LongSmokeApp-{sequence % 3}",
                    "window_title": f"Long Smoke Window {sequence}",
                    "locale": "ja-JP",
                }
            )
            if should_inject_mismatch:
                self.api.post_expect_error(
                    "/api/capability/result",
                    {
                        "request_id": request_id,
                        "client_id": f"{connected_client_id}-mismatch",
                        "capability_id": "vision.capture",
                        "result": {
                            "images": [PNG_DATA_URI],
                            "client_context": client_context,
                            "error": None,
                        },
                    },
                    status_code=409,
                    error_code="capability_result_client_id_mismatch",
                )
                log(f"capture-response mismatch verified request_id={request_id}")
            if should_inject_invalid_images:
                self.api.post_expect_error(
                    "/api/capability/result",
                    {
                        "request_id": request_id,
                        "client_id": connected_client_id,
                        "capability_id": "vision.capture",
                        "result": {
                            "images": [""],
                            "client_context": client_context,
                            "error": None,
                        },
                    },
                    status_code=400,
                    error_code="invalid_capability_result",
                )
                log(f"capture-response invalid_images verified request_id={request_id}")
            if should_inject_invalid_error:
                self.api.post_expect_error(
                    "/api/capability/result",
                    {
                        "request_id": request_id,
                        "client_id": connected_client_id,
                        "capability_id": "vision.capture",
                        "result": {
                            "images": [PNG_DATA_URI],
                            "client_context": client_context,
                            "error": 123,
                        },
                    },
                    status_code=400,
                    error_code="invalid_capability_result",
                )
                log(f"capability-result invalid_capability_result verified request_id={request_id}")
            if should_inject_unknown_request:
                self.api.post_expect_success_empty_data(
                    "/api/capability/result",
                    {
                        "request_id": f"{request_id}-unknown",
                        "client_id": connected_client_id,
                        "capability_id": "vision.capture",
                        "result": {
                            "images": [PNG_DATA_URI],
                            "client_context": client_context,
                            "error": None,
                        },
                    },
                )
                log(f"capture-response unknown request ignored request_id={request_id}")
            self.api.post(
                "/api/capability/result",
                {
                    "request_id": request_id,
                    "client_id": connected_client_id,
                    "capability_id": "vision.capture",
                    "result": {
                        "images": [PNG_DATA_URI],
                        "client_context": client_context,
                        "error": None,
                    },
                },
            )
            self.capture_response_count += 1
            if self.capture_timeout_request_ids:
                self.capture_timeout_recovered = True
            return
        if event_type == "external.status_request":
            if client_label != "primary":
                raise SmokeError(f"{client_label} desktop client unexpectedly received external.status_request.")
            request_id = data.get("request_id")
            capability_id = data.get("capability_id")
            service = data.get("service")
            if not isinstance(request_id, str) or not request_id:
                raise SmokeError("external.status_request did not include request_id.")
            if capability_id != "external.status":
                raise SmokeError(f"external.status_request capability_id was invalid: {capability_id}")
            if not isinstance(service, str) or not service.strip():
                raise SmokeError("external.status_request did not include service.")
            with self._external_status_lock:
                self.external_status_request_count += 1
                self.external_status_request_ids.append(request_id)
                override = self._external_status_overrides.pop(0) if self._external_status_overrides else None
            status_text = (
                str(override.get("status_text")).strip()
                if isinstance(override, dict) and isinstance(override.get("status_text"), str) and override.get("status_text", "").strip()
                else f"{service.strip()} は正常に応答している。"
            )
            client_context = (
                dict(override["client_context"])
                if isinstance(override, dict) and isinstance(override.get("client_context"), dict)
                else {}
            )
            if not isinstance(client_context.get("external_service_summary"), str) or not client_context["external_service_summary"].strip():
                client_context["external_service_summary"] = status_text
            if not isinstance(client_context.get("device_state_summary"), str) or not client_context["device_state_summary"].strip():
                client_context["device_state_summary"] = "external.status を返せる desktop client が接続中。"
            if not isinstance(client_context.get("schedule_summary"), str) or not client_context["schedule_summary"].strip():
                client_context["schedule_summary"] = f"{service.strip()} の状態確認をこのまま進められる。"
            self.api.post(
                "/api/capability/result",
                {
                    "request_id": request_id,
                    "client_id": connected_client_id,
                    "capability_id": "external.status",
                    "result": {
                        "status_text": status_text,
                        "client_context": client_context,
                        "error": None,
                    },
                },
            )
            self.external_status_response_count += 1
            return
        if event_type == "desktop_watch":
            self.desktop_watch_event_count += 1
            return

    def _assert_event_clients_healthy(self) -> None:
        if self.event_client is not None and self.event_client.error is not None:
            raise SmokeError(f"desktop client failed: {self.event_client.error}")
        if self.secondary_event_client is not None and self.secondary_event_client.error is not None:
            raise SmokeError(f"secondary desktop client failed: {self.secondary_event_client.error}")

    def _exercise_capture_timeout_recovery(self) -> None:
        if self.args.capture_timeout_failures <= 0:
            return

        deadline = time.monotonic() + WAIT_CAPTURE_RECOVERY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            self._assert_server_running()
            self._assert_event_clients_healthy()
            if (
                len(self.capture_timeout_request_ids) >= self.args.capture_timeout_failures
                and self.capture_timeout_recovered
                and self.capture_response_count >= 1
            ):
                log(
                    "capture timeout recovery confirmed"
                    f" dropped={len(self.capture_timeout_request_ids)}"
                    f" recovered_responses={self.capture_response_count}"
                )
                return
            time.sleep(0.25)
        raise SmokeError("desktop_watch did not recover after the injected capture timeout.")

    def _run_restart_probe(self) -> None:
        if self.args.restart_burst_conversations <= 0:
            return

        for index in range(self.args.restart_burst_conversations):
            cycle_id = self._post_conversation(
                text=f"restart probe の会話です。memory worker 再投入確認 #{index + 1}",
                source="long_smoke_restart_probe",
                client_id="long-smoke-restart-probe",
                active_app="LongSmokeRestartProbe",
                window_title=f"Restart Probe {index + 1}",
            )
            self.restart_probe_cycle_ids.append(cycle_id)

        deadline = time.monotonic() + WAIT_RESTART_PENDING_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            self._assert_server_running()
            self._assert_event_clients_healthy()
            status = self.api.get("/api/status")
            runtime_summary = status["runtime_summary"]
            pending_count = int(runtime_summary.get("pending_memory_job_count", 0))
            in_progress = bool(runtime_summary.get("memory_job_in_progress"))
            if pending_count > 0 or in_progress:
                self.restart_probe_pending_before_restart = pending_count
                self.restart_probe_in_progress_before_restart = in_progress
                log(
                    "restart probe stopping server"
                    f" pending_jobs={pending_count}"
                    f" in_progress={in_progress}"
                )
                self._restart_server_preserving_state()
                self._wait_for_memory_jobs_to_drain()
                return
            time.sleep(0.25)
        raise SmokeError("restart probe could not observe queued or running memory jobs before restart.")

    def _exercise_desktop_watch_event_boundaries(self) -> None:
        self.desktop_watch_capability_probe_cycle_id = self._run_desktop_watch_probe(
            probe_name="capability_request",
            marker="LongSmokeReplyProbeWindow",
            active_app="LongSmokeReplyProbeApp",
            window_title="LongSmokeReplyProbeWindow",
            expected_result_kind="capability_request",
        )
        self.desktop_watch_capability_probe_verified = True
        pending_intent_marker = self._seed_pending_intent_probe_candidate()
        self.desktop_watch_pending_intent_probe_cycle_id = self._run_desktop_watch_probe(
            probe_name="pending_intent",
            marker=pending_intent_marker,
            active_app="LongSmokePendingIntentProbeApp",
            window_title=f"{pending_intent_marker} また今度あとで",
            expected_result_kind=None,
            extra_client_context={
                "schedule_summary": f"{pending_intent_marker} の見直し予定が近い。",
            },
        )
        self.desktop_watch_pending_intent_probe_verified = True

    def _run_desktop_watch_probe(
        self,
        *,
        probe_name: str,
        marker: str,
        active_app: str,
        window_title: str,
        expected_result_kind: str | None,
        extra_client_context: dict[str, str] | None = None,
    ) -> str:
        override_client_context = {
            "active_app": active_app,
            "window_title": window_title,
            "locale": "ja-JP",
        }
        if isinstance(extra_client_context, dict):
            override_client_context.update(extra_client_context)
        self._queue_capture_context_override(
            {
                "client_context": override_client_context,
            }
        )
        log(
            "desktop_watch probe queued"
            f" probe={probe_name}"
            f" expected_result_kind={expected_result_kind or 'any'}"
            f" marker={marker}"
        )

        deadline = time.monotonic() + WAIT_DESKTOP_WATCH_PROBE_TIMEOUT_SECONDS
        matched_trace: dict[str, Any] | None = None
        inspected_cycle_ids: set[str] = set()

        while time.monotonic() < deadline:
            self._assert_server_running()
            self._assert_event_clients_healthy()
            cycle_summaries = self.api.get("/api/inspection/cycle-summaries?limit=60").get("cycle_summaries", [])
            if not isinstance(cycle_summaries, list):
                raise SmokeError("cycle_summaries response was invalid during desktop_watch probe.")
            for cycle_summary in cycle_summaries:
                if not isinstance(cycle_summary, dict):
                    continue
                if cycle_summary.get("trigger_kind") != "desktop_watch":
                    continue
                cycle_id = cycle_summary.get("cycle_id")
                if not isinstance(cycle_id, str) or not cycle_id or cycle_id in inspected_cycle_ids:
                    continue
                inspected_cycle_ids.add(cycle_id)
                trace = self.api.get(f"/api/inspection/cycles/{cycle_id}")
                input_summary = ((trace.get("input_trace") or {}).get("normalized_input_summary"))
                if not isinstance(input_summary, str) or marker not in input_summary:
                    continue
                matched_trace = trace
                break

            if matched_trace is not None:
                cycle_id = matched_trace.get("cycle_id")
                result_kind = (matched_trace.get("cycle_summary") or {}).get("result_kind")
                if expected_result_kind is not None and result_kind != expected_result_kind:
                    raise SmokeError(
                        f"desktop_watch {probe_name} probe result_kind was {result_kind}."
                    )
                if expected_result_kind is None:
                    log(f"desktop_watch {probe_name} trace matched cycle_id={cycle_id} result_kind={result_kind}")
                else:
                    log(
                        f"desktop_watch {probe_name} {expected_result_kind} confirmed"
                        f" cycle_id={cycle_id}"
                    )
                return cycle_id
            time.sleep(0.25)

        raise SmokeError(f"desktop_watch probe timed out: {probe_name}")

    def _seed_pending_intent_probe_candidate(self) -> str:
        marker = "LongSmokePendingIntentProbeMarker"
        first_cycle_id = self._post_conversation(
            text=f"{marker} のレビュー相談を続けたいです。",
            source="long_smoke_pending_intent_seed",
            client_id="long-smoke-pending-intent-seed",
            active_app="LongSmokePendingIntentSeed",
            window_title=f"{marker} seed-1",
        )
        self.pending_intent_seed_cycle_ids.append(first_cycle_id)
        self._wait_for_memory_jobs_to_drain()

        second_cycle_id = self._post_conversation(
            text=f"{marker} の件はまた今度あとで確認したいです。",
            source="long_smoke_pending_intent_seed",
            client_id="long-smoke-pending-intent-seed",
            active_app="LongSmokePendingIntentSeed",
            window_title=f"{marker} seed-2",
        )
        self.pending_intent_seed_cycle_ids.append(second_cycle_id)

        deadline = time.monotonic() + WAIT_PENDING_INTENT_SEED_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            self._assert_server_running()
            self._assert_event_clients_healthy()
            trace = self.api.get(f"/api/inspection/cycles/{second_cycle_id}")
            decision_trace = trace.get("decision_trace", {})
            result_trace = trace.get("result_trace", {})
            if not isinstance(decision_trace, dict) or not isinstance(result_trace, dict):
                time.sleep(0.25)
                continue
            if decision_trace.get("result_kind") != "pending_intent":
                time.sleep(0.25)
                continue
            pending_intent_summary = result_trace.get("pending_intent_summary")
            if not isinstance(pending_intent_summary, dict):
                time.sleep(0.25)
                continue
            log(
                "pending_intent seed confirmed"
                f" cycle_id={second_cycle_id}"
                f" dedupe_key={pending_intent_summary.get('dedupe_key')}"
            )
            return marker
        raise SmokeError("pending_intent probe seed did not create a pending_intent candidate.")

    def _queue_capture_context_override(self, override: dict[str, Any]) -> None:
        with self._capture_lock:
            self._capture_context_overrides.append(override)

    def _queue_external_status_override(self, override: dict[str, Any]) -> None:
        with self._external_status_lock:
            self._external_status_overrides.append(override)

    def _run_external_status_probe(
        self,
        *,
        marker: str,
        conversation_text: str,
        source: str,
        client_id: str,
        active_app: str,
        window_title: str,
        override: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self._queue_external_status_override(override)
        conversation_cycle_id = self._post_conversation(
            text=conversation_text,
            source=source,
            client_id=client_id,
            active_app=active_app,
            window_title=window_title,
        )
        deadline = time.monotonic() + WAIT_EXTERNAL_STATUS_PROBE_TIMEOUT_SECONDS
        conversation_trace: dict[str, Any] | None = None
        request_id: str | None = None
        while time.monotonic() < deadline:
            self._assert_server_running()
            self._assert_event_clients_healthy()
            candidate = self.api.get(f"/api/inspection/cycles/{conversation_cycle_id}")
            request_summary = ((candidate.get("result_trace") or {}).get("capability_request_summary"))
            if (
                isinstance(request_summary, dict)
                and request_summary.get("capability_id") == "external.status"
                and request_summary.get("status") == "dispatched"
            ):
                request_id = request_summary.get("request_id")
                if isinstance(request_id, str) and request_id:
                    conversation_trace = candidate
                    break
            time.sleep(0.25)
        if conversation_trace is None or request_id is None:
            raise SmokeError("external.status probe did not dispatch a request.")

        followup_trace: dict[str, Any] | None = None
        inspected_cycle_ids: set[str] = set()
        while time.monotonic() < deadline:
            self._assert_server_running()
            self._assert_event_clients_healthy()
            cycle_summaries = self.api.get("/api/inspection/cycle-summaries?limit=80").get("cycle_summaries", [])
            if not isinstance(cycle_summaries, list):
                raise SmokeError("cycle_summaries response was invalid during external.status probe.")
            for cycle_summary in cycle_summaries:
                if not isinstance(cycle_summary, dict):
                    continue
                if cycle_summary.get("trigger_kind") != "capability_result":
                    continue
                cycle_id = cycle_summary.get("cycle_id")
                if not isinstance(cycle_id, str) or not cycle_id or cycle_id in inspected_cycle_ids:
                    continue
                inspected_cycle_ids.add(cycle_id)
                trace = self.api.get(f"/api/inspection/cycles/{cycle_id}")
                followup_summary = ((trace.get("result_trace") or {}).get("capability_result_followup_summary"))
                if not isinstance(followup_summary, dict):
                    continue
                source_request_summary = followup_summary.get("source_request_summary")
                if not isinstance(source_request_summary, dict):
                    continue
                if source_request_summary.get("request_id") != request_id:
                    continue
                observation_summary = ((trace.get("input_trace") or {}).get("observation_summary"))
                if not isinstance(observation_summary, dict):
                    continue
                observed_status_text = observation_summary.get("status_text")
                if not isinstance(observed_status_text, str) or marker not in observed_status_text:
                    continue
                followup_trace = trace
                break
            if followup_trace is not None:
                break
            time.sleep(0.25)
        if followup_trace is None:
            raise SmokeError("external.status probe did not produce a capability_result follow-up cycle.")

        return conversation_trace, followup_trace

    def _list_persisted_world_states(self, *, state_type: str) -> list[dict[str, Any]]:
        if not isinstance(self.selected_memory_set_id, str) or not self.selected_memory_set_id:
            raise SmokeError("selected_memory_set_id was not initialized.")
        memory_db_path = self.data_dir / "memory.db"
        if not memory_db_path.exists():
            raise SmokeError("memory.db was not created.")
        conn = sqlite3.connect(memory_db_path)
        try:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM world_states
                WHERE memory_set_id = ?
                  AND state_type = ?
                """,
                (self.selected_memory_set_id, state_type),
            ).fetchall()
        finally:
            conn.close()
        return [json.loads(row[0]) for row in rows]

    def _exercise_external_status_followup(self) -> None:
        github_marker = "LongSmokeExternalStatusProbeMarker"
        github_status_text = f"{github_marker}: GitHub の未確認レビューが 1 件ある。"
        conversation_trace, followup_trace = self._run_external_status_probe(
            marker=github_marker,
            conversation_text=f"GitHub の通知の状態を確認して教えて。{github_marker}",
            source="long_smoke_external_status_probe",
            client_id="long-smoke-external-status-probe",
            active_app="LongSmokeExternalStatusProbe",
            window_title=github_marker,
            override={
                "status_text": github_status_text,
                "client_context": {
                    "external_service_summary": github_status_text,
                    "device_state_summary": "external.status を返せる desktop client が接続中。",
                    "schedule_summary": "GitHub の通知確認をこのまま進められる。",
                },
            },
        )
        conversation_cycle_id = conversation_trace.get("cycle_id")
        if not isinstance(conversation_cycle_id, str) or not conversation_cycle_id:
            raise SmokeError("external.status probe conversation cycle_id was not recorded.")
        self.external_status_probe_conversation_cycle_id = conversation_cycle_id
        followup_cycle_id = followup_trace.get("cycle_id")
        if not isinstance(followup_cycle_id, str) or not followup_cycle_id:
            raise SmokeError("external.status follow-up cycle_id was not recorded.")
        self.external_status_probe_followup_cycle_id = followup_cycle_id

        calendar_marker = "LongSmokeCalendarStatusProbeMarker"
        calendar_status_text = f"{calendar_marker}: カレンダーに 30 分後の予定がある。"
        self._run_external_status_probe(
            marker=calendar_marker,
            conversation_text=f"カレンダーの状態を確認して教えて。{calendar_marker}",
            source="long_smoke_calendar_status_probe",
            client_id="long-smoke-calendar-status-probe",
            active_app="LongSmokeCalendarStatusProbe",
            window_title=calendar_marker,
            override={
                "status_text": calendar_status_text,
                "client_context": {
                    "external_service_summary": calendar_status_text,
                    "device_state_summary": "external.status を返せる desktop client が接続中。",
                    "schedule_summary": "このあとカレンダーの予定確認を進められる。",
                },
            },
        )
        persisted_external_service_states = self._list_persisted_world_states(state_type="external_service")
        integration_keys = sorted(
            {
                str(record.get("integration_key") or "").strip()
                for record in persisted_external_service_states
                if isinstance(record, dict) and isinstance(record.get("integration_key"), str) and record.get("integration_key", "").strip()
            }
        )
        self.external_status_persisted_integration_keys = integration_keys
        if "external_service:github" not in integration_keys or "external_service:calendar" not in integration_keys:
            raise SmokeError("external.status multi-service world_state integration was not persisted.")
        self.external_status_multi_service_verified = True
        self.external_status_probe_verified = True
        log(
            "external.status followup confirmed"
            f" conversation_cycle_id={conversation_cycle_id}"
            f" followup_cycle_id={followup_cycle_id}"
        )

    def _exercise_multiple_client_boundary(self) -> None:
        pause_seconds = max(self.args.multiple_client_pause_seconds, 0.0)
        if pause_seconds <= 0:
            self.multiple_client_pause_verified = True
            self.multiple_client_resume_verified = True
            return

        secondary_client_id = f"{self.args.desktop_client_id}-secondary"
        if self.secondary_event_client is not None:
            self.secondary_event_client.close()
            self.secondary_event_client = None
        self.secondary_event_client = self._connect_event_client(
            client_id=secondary_client_id,
            client_label="secondary",
        )
        log(f"secondary desktop client connected client_id={secondary_client_id}")

        settle_deadline = time.monotonic() + float(self.args.desktop_watch_interval_seconds) + 1.0
        while time.monotonic() < settle_deadline:
            self._assert_server_running()
            self._assert_event_clients_healthy()
            time.sleep(0.25)

        capture_request_baseline = self.capture_request_count
        pause_deadline = time.monotonic() + pause_seconds
        while time.monotonic() < pause_deadline:
            self._assert_server_running()
            self._assert_event_clients_healthy()
            if self.capture_request_count != capture_request_baseline:
                raise SmokeError("capture_request was emitted while multiple vision.capture clients were connected.")
            time.sleep(0.25)
        self.multiple_client_pause_verified = True
        log(
            "multiple desktop client pause confirmed"
            f" pause_seconds={pause_seconds:.1f}"
            f" capture_request_count={self.capture_request_count}"
        )

        self.secondary_event_client.close()
        self.secondary_event_client = None
        resume_deadline = time.monotonic() + WAIT_CAPTURE_RECOVERY_TIMEOUT_SECONDS
        while time.monotonic() < resume_deadline:
            self._assert_server_running()
            self._assert_event_clients_healthy()
            if self.capture_request_count > capture_request_baseline:
                self.multiple_client_resume_verified = True
                log(
                    "multiple desktop client resume confirmed"
                    f" capture_request_count={self.capture_request_count}"
                )
                return
            time.sleep(0.25)
        raise SmokeError("desktop_watch did not resume after secondary desktop client disconnected.")

    def _run_conversations(self) -> None:
        messages = [
            "こんにちは。今日は少し眠いです。",
            "この前の仕事の相談の続きをしたいです。",
            "コーヒーが好きで、朝に飲むことが多いです。",
            "また今度の約束、どこまで進めるか考えたいです。",
            "最近ちょっと距離感が気になっています。",
        ]
        deadline = time.monotonic() + self.args.run_seconds
        next_conversation_at = time.monotonic()
        sent_count = 0
        last_status_log_at = 0.0

        while time.monotonic() < deadline:
            self._assert_server_running()
            self._assert_event_clients_healthy()

            now = time.monotonic()
            if now >= next_conversation_at:
                cycle_id = self._post_conversation(
                    text=f"{messages[sent_count % len(messages)]} #{sent_count + 1}",
                    source="long_smoke",
                    client_id="long-smoke-conversation",
                    active_app="LongSmokeConversation",
                    window_title=f"Conversation {sent_count + 1}",
                    images=[PNG_DATA_URI] if sent_count == 0 else None,
                    extra_client_context=(
                        {
                            "external_service_summary": "GitHub の通知に未確認の更新がある。",
                            "device_state_summary": "デスクトップ client は利用可能な状態で接続中。",
                            "schedule_summary": "このあと long smoke の確認を続ける予定が近い。",
                        }
                        if sent_count == 0
                        else None
                    ),
                )
                sent_count += 1
                next_conversation_at = now + self.args.conversation_interval_seconds

            if now - last_status_log_at >= 5.0:
                status = self.api.get("/api/status")
                runtime_summary = status["runtime_summary"]
                log(
                    "runtime"
                    f" pending_jobs={runtime_summary.get('pending_memory_job_count')}"
                    f" in_progress={runtime_summary.get('memory_job_in_progress')}"
                    f" captures={self.capture_request_count}"
                    f" external_status={self.external_status_request_count}"
                    f" desktop_events={self.desktop_watch_event_count}"
                )
                last_status_log_at = now

            time.sleep(0.25)

    def _wait_for_memory_jobs_to_drain(self) -> None:
        deadline = time.monotonic() + WAIT_QUEUE_DRAIN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            self._assert_server_running()
            self._assert_event_clients_healthy()
            status = self.api.get("/api/status")
            runtime_summary = status["runtime_summary"]
            if runtime_summary.get("pending_memory_job_count") == 0 and not runtime_summary.get("memory_job_in_progress"):
                return
            time.sleep(0.5)
        raise SmokeError("memory postprocess queue did not drain within the timeout.")

    def _collect_summary(self) -> dict[str, Any]:
        status = self.api.get("/api/status")
        cycle_summaries = self.api.get("/api/inspection/cycle-summaries?limit=200").get("cycle_summaries", [])
        if not isinstance(cycle_summaries, list):
            raise SmokeError("cycle_summaries response was invalid.")

        trigger_counts: dict[str, int] = {}
        failed_cycle_ids: list[str] = []
        for cycle_summary in cycle_summaries:
            if not isinstance(cycle_summary, dict):
                continue
            trigger_kind = cycle_summary.get("trigger_kind", "unknown")
            trigger_counts[trigger_kind] = trigger_counts.get(trigger_kind, 0) + 1
            if cycle_summary.get("failed"):
                cycle_id = cycle_summary.get("cycle_id")
                if isinstance(cycle_id, str) and cycle_id:
                    failed_cycle_ids.append(cycle_id)

        conversation_traces: list[dict[str, Any]] = []
        for cycle_id in self.conversation_cycle_ids:
            trace = self.api.get(f"/api/inspection/cycles/{cycle_id}")
            conversation_traces.append(trace)

        restart_probe_traces: list[dict[str, Any]] = []
        for cycle_id in self.restart_probe_cycle_ids:
            trace = self.api.get(f"/api/inspection/cycles/{cycle_id}")
            restart_probe_traces.append(trace)

        desktop_watch_capability_probe_trace = None
        if isinstance(self.desktop_watch_capability_probe_cycle_id, str) and self.desktop_watch_capability_probe_cycle_id:
            desktop_watch_capability_probe_trace = self.api.get(
                f"/api/inspection/cycles/{self.desktop_watch_capability_probe_cycle_id}"
            )
        desktop_watch_pending_intent_probe_trace = None
        if (
            isinstance(self.desktop_watch_pending_intent_probe_cycle_id, str)
            and self.desktop_watch_pending_intent_probe_cycle_id
        ):
            desktop_watch_pending_intent_probe_trace = self.api.get(
                f"/api/inspection/cycles/{self.desktop_watch_pending_intent_probe_cycle_id}"
            )
        external_status_probe_conversation_trace = None
        if (
            isinstance(self.external_status_probe_conversation_cycle_id, str)
            and self.external_status_probe_conversation_cycle_id
        ):
            external_status_probe_conversation_trace = self.api.get(
                f"/api/inspection/cycles/{self.external_status_probe_conversation_cycle_id}"
            )
        external_status_probe_followup_trace = None
        if isinstance(self.external_status_probe_followup_cycle_id, str) and self.external_status_probe_followup_cycle_id:
            external_status_probe_followup_trace = self.api.get(
                f"/api/inspection/cycles/{self.external_status_probe_followup_cycle_id}"
            )

        return {
            "artifacts_dir": str(self.artifact_dir),
            "server_log_path": str(self.server_log_path),
            "seed_data_dir": str(self.seed_data_dir) if self.seed_data_dir is not None else None,
            "editor_state_mode": self.editor_state_mode_used,
            "selected_model_preset_id": self.selected_model_preset_id,
            "selected_memory_set_id": self.selected_memory_set_id,
            "status": status,
            "conversation_cycle_ids": self.conversation_cycle_ids,
            "restart_probe_cycle_ids": self.restart_probe_cycle_ids,
            "pending_intent_seed_cycle_ids": self.pending_intent_seed_cycle_ids,
            "trigger_counts": trigger_counts,
            "failed_cycle_ids": failed_cycle_ids,
            "capture_request_count": self.capture_request_count,
            "capture_response_count": self.capture_response_count,
            "external_status_request_count": self.external_status_request_count,
            "external_status_response_count": self.external_status_response_count,
            "external_status_request_ids": self.external_status_request_ids,
            "desktop_watch_event_count": self.desktop_watch_event_count,
            "capture_timeout_request_ids": self.capture_timeout_request_ids,
            "capture_mismatch_request_ids": self.capture_mismatch_request_ids,
            "capture_invalid_images_request_ids": self.capture_invalid_images_request_ids,
            "capture_invalid_error_request_ids": self.capture_invalid_error_request_ids,
            "capture_unknown_request_ids": self.capture_unknown_request_ids,
            "capture_timeout_recovered": self.capture_timeout_recovered,
            "restart_count": self.restart_count,
            "restart_probe_pending_before_restart": self.restart_probe_pending_before_restart,
            "restart_probe_in_progress_before_restart": self.restart_probe_in_progress_before_restart,
            "multiple_client_pause_verified": self.multiple_client_pause_verified,
            "multiple_client_resume_verified": self.multiple_client_resume_verified,
            "desktop_watch_capability_probe_cycle_id": self.desktop_watch_capability_probe_cycle_id,
            "desktop_watch_pending_intent_probe_cycle_id": self.desktop_watch_pending_intent_probe_cycle_id,
            "desktop_watch_capability_probe_verified": self.desktop_watch_capability_probe_verified,
            "desktop_watch_pending_intent_probe_verified": self.desktop_watch_pending_intent_probe_verified,
            "external_status_probe_conversation_cycle_id": self.external_status_probe_conversation_cycle_id,
            "external_status_probe_followup_cycle_id": self.external_status_probe_followup_cycle_id,
            "external_status_probe_verified": self.external_status_probe_verified,
            "external_status_multi_service_verified": self.external_status_multi_service_verified,
            "external_status_persisted_integration_keys": self.external_status_persisted_integration_keys,
            "desktop_watch_capability_probe_trace": desktop_watch_capability_probe_trace,
            "desktop_watch_pending_intent_probe_trace": desktop_watch_pending_intent_probe_trace,
            "external_status_probe_conversation_trace": external_status_probe_conversation_trace,
            "external_status_probe_followup_trace": external_status_probe_followup_trace,
            "conversation_traces": conversation_traces,
            "restart_probe_traces": restart_probe_traces,
        }

    def _assert_summary(self, summary: dict[str, Any]) -> None:
        runtime_summary = summary["status"]["runtime_summary"]
        wake_cycle_count = summary["trigger_counts"].get("wake", 0) + summary["trigger_counts"].get("background_wake", 0)
        if not runtime_summary.get("memory_job_worker_active"):
            raise SmokeError("memory worker was not active at the end of the smoke run.")
        if runtime_summary.get("pending_memory_job_count") != 0:
            raise SmokeError("pending_memory_job_count was not drained to zero.")
        if runtime_summary.get("memory_job_in_progress"):
            raise SmokeError("memory worker was still processing a job at the end of the smoke run.")
        if len(summary["conversation_cycle_ids"]) < self.args.min_conversation_cycles:
            raise SmokeError(
                "conversation cycles were too few:"
                f" {len(summary['conversation_cycle_ids'])} < {self.args.min_conversation_cycles}"
            )
        if wake_cycle_count < 1:
            raise SmokeError("no wake/background_wake cycle was recorded during the smoke run.")
        if summary["trigger_counts"].get("desktop_watch", 0) < 1:
            raise SmokeError("no desktop_watch cycle was recorded during the smoke run.")
        if summary["capture_request_count"] < 1:
            raise SmokeError("no vision.capture_request event was received.")
        if summary["external_status_request_count"] < 1:
            raise SmokeError("no external.status_request event was received.")
        expected_responses = summary["capture_request_count"] - len(summary["capture_timeout_request_ids"])
        if summary["capture_response_count"] != expected_responses:
            raise SmokeError("capture request / response counts did not match the injected timeout count.")
        if summary["external_status_response_count"] != summary["external_status_request_count"]:
            raise SmokeError("external.status request / response counts did not match.")
        expected_failed_cycle_ids: list[str] = []
        unexpected_failed_cycle_ids: list[str] = []
        for cycle_id in summary["failed_cycle_ids"]:
            trace = self.api.get(f"/api/inspection/cycles/{cycle_id}")
            if self._is_expected_capture_timeout_failure_trace(trace):
                expected_failed_cycle_ids.append(cycle_id)
                continue
            unexpected_failed_cycle_ids.append(cycle_id)
        if unexpected_failed_cycle_ids:
            raise SmokeError(f"failed cycles were recorded: {', '.join(unexpected_failed_cycle_ids)}")
        if self.args.capture_timeout_failures > 0:
            if len(summary["capture_timeout_request_ids"]) != self.args.capture_timeout_failures:
                raise SmokeError("capture timeout injection count did not match the requested failure count.")
            if not summary["capture_timeout_recovered"]:
                raise SmokeError("desktop_watch did not recover after the injected capture timeout.")
            if len(expected_failed_cycle_ids) != self.args.capture_timeout_failures:
                raise SmokeError("desktop_watch timeout failure cycles did not match the injected timeout count.")
        if self.args.capture_mismatch_failures > 0:
            if len(summary["capture_mismatch_request_ids"]) != self.args.capture_mismatch_failures:
                raise SmokeError("capture client_id mismatch injection count did not match the requested failure count.")
        if self.args.capture_invalid_images_failures > 0:
            if len(summary["capture_invalid_images_request_ids"]) != self.args.capture_invalid_images_failures:
                raise SmokeError("capture invalid_images injection count did not match the requested failure count.")
        if self.args.capture_invalid_error_failures > 0:
            if len(summary["capture_invalid_error_request_ids"]) != self.args.capture_invalid_error_failures:
                raise SmokeError("capture invalid_capability_result injection count did not match the requested failure count.")
        if self.args.capture_unknown_request_failures > 0:
            if len(summary["capture_unknown_request_ids"]) != self.args.capture_unknown_request_failures:
                raise SmokeError("capture unknown request injection count did not match the requested failure count.")
        if self.args.restart_burst_conversations > 0:
            if summary["restart_count"] < 1:
                raise SmokeError("restart probe did not restart the server.")
            if summary["restart_probe_pending_before_restart"] is None and not summary["restart_probe_in_progress_before_restart"]:
                raise SmokeError("restart probe did not observe a queued or running memory job before restart.")
        if not summary["multiple_client_pause_verified"]:
            raise SmokeError("multiple desktop client pause boundary was not verified.")
        if not summary["multiple_client_resume_verified"]:
            raise SmokeError("multiple desktop client resume boundary was not verified.")
        if not summary["desktop_watch_capability_probe_verified"]:
            raise SmokeError("desktop_watch capability_request boundary was not verified.")
        if not summary["desktop_watch_pending_intent_probe_verified"]:
            raise SmokeError("desktop_watch pending_intent boundary was not verified.")
        if not summary["external_status_probe_verified"]:
            raise SmokeError("external.status follow-up boundary was not verified.")
        if not summary["external_status_multi_service_verified"]:
            raise SmokeError("external.status multi-service integration boundary was not verified.")
        self._assert_desktop_watch_probe_trace(summary.get("desktop_watch_capability_probe_trace"), "capability_request")
        self._assert_desktop_watch_probe_trace(summary.get("desktop_watch_pending_intent_probe_trace"), "pending_intent")
        self._assert_external_status_probe_trace(
            summary.get("external_status_probe_conversation_trace"),
            summary.get("external_status_probe_followup_trace"),
        )

        for trace in summary["conversation_traces"]:
            cycle_id = trace.get("cycle_id")
            memory_trace = trace.get("memory_trace", {})
            if memory_trace.get("turn_consolidation_status") != "succeeded":
                raise SmokeError(f"conversation cycle {cycle_id} did not complete turn consolidation.")
            vector_status = (memory_trace.get("vector_index_sync") or {}).get("result_status")
            reflective_status = (memory_trace.get("reflective_consolidation") or {}).get("result_status")
            if vector_status != "succeeded":
                raise SmokeError(f"conversation cycle {cycle_id} vector_index_sync was {vector_status}.")
            if reflective_status == "failed":
                raise SmokeError(f"conversation cycle {cycle_id} reflective_consolidation failed.")

        for trace in summary["restart_probe_traces"]:
            cycle_id = trace.get("cycle_id")
            memory_trace = trace.get("memory_trace", {})
            if memory_trace.get("turn_consolidation_status") != "succeeded":
                raise SmokeError(f"restart probe cycle {cycle_id} did not complete turn consolidation.")
            vector_status = (memory_trace.get("vector_index_sync") or {}).get("result_status")
            if vector_status != "succeeded":
                raise SmokeError(f"restart probe cycle {cycle_id} vector_index_sync was {vector_status}.")

    def _assert_desktop_watch_probe_trace(self, trace: Any, label: str) -> None:
        if not isinstance(trace, dict):
            raise SmokeError(f"desktop_watch {label} probe trace was not collected.")
        input_trace = trace.get("input_trace", {})
        if not isinstance(input_trace, dict):
            raise SmokeError(f"desktop_watch {label} probe input_trace was invalid.")
        cycle_summary = trace.get("cycle_summary", {})
        if not isinstance(cycle_summary, dict):
            raise SmokeError(f"desktop_watch {label} probe cycle_summary was invalid.")
        result_trace = trace.get("result_trace", {})
        if not isinstance(result_trace, dict):
            raise SmokeError(f"desktop_watch {label} probe result_trace was invalid.")
        world_state_trace = trace.get("world_state_trace", {})
        if not isinstance(world_state_trace, dict):
            raise SmokeError(f"desktop_watch {label} probe world_state_trace was invalid.")
        state_type_hooks = world_state_trace.get("source_pack_state_type_hooks", {})
        if not isinstance(state_type_hooks, dict):
            raise SmokeError(f"desktop_watch {label} probe source_pack_state_type_hooks was invalid.")
        normalized_candidate_policies = world_state_trace.get("normalized_candidate_policies", [])
        if not isinstance(normalized_candidate_policies, list):
            raise SmokeError(f"desktop_watch {label} probe normalized_candidate_policies was invalid.")
        screen_hook = state_type_hooks.get("screen", {})
        if not isinstance(screen_hook, dict):
            raise SmokeError(f"desktop_watch {label} probe screen hook was not recorded.")
        if screen_hook.get("capability_id") != "vision.capture":
            raise SmokeError(f"desktop_watch {label} probe screen hook capability_id was invalid.")
        if screen_hook.get("summary_source") != "visual_summary_text":
            raise SmokeError(f"desktop_watch {label} probe screen hook summary_source was invalid.")
        signal_fields = screen_hook.get("signal_fields", [])
        if not isinstance(signal_fields, list) or "visual_summary_text" not in signal_fields:
            raise SmokeError(f"desktop_watch {label} probe screen hook signal_fields was invalid.")
        screen_policy = next(
            (
                item
                for item in normalized_candidate_policies
                if isinstance(item, dict) and item.get("state_type") == "screen"
            ),
            None,
        )
        if not isinstance(screen_policy, dict):
            raise SmokeError(f"desktop_watch {label} probe screen policy was not recorded.")
        if screen_policy.get("summary_source") != "visual_summary_text":
            raise SmokeError(f"desktop_watch {label} probe screen policy summary_source was invalid.")
        if screen_policy.get("effective_ttl_seconds") != 600:
            raise SmokeError(f"desktop_watch {label} probe screen policy TTL was invalid.")
        if screen_policy.get("integration_key") != "screen:foreground":
            raise SmokeError(f"desktop_watch {label} probe screen policy integration_key was invalid.")
        if label == "capability_request":
            capability_request_summary = result_trace.get("capability_request_summary", {})
            if not isinstance(capability_request_summary, dict):
                raise SmokeError("desktop_watch capability_request probe capability_request_summary was invalid.")
            if capability_request_summary.get("capability_id") != "vision.capture":
                raise SmokeError("desktop_watch capability_request probe capability_id was invalid.")
            if capability_request_summary.get("status") != "dispatched":
                raise SmokeError("desktop_watch capability_request probe capability_request status was invalid.")
            if not isinstance(capability_request_summary.get("request_id"), str) or not capability_request_summary["request_id"]:
                raise SmokeError("desktop_watch capability_request probe request_id was not recorded.")
            if not isinstance(capability_request_summary.get("timeout_ms"), int):
                raise SmokeError("desktop_watch capability_request probe timeout_ms was not recorded.")
            ongoing_action_transition_summary = result_trace.get("ongoing_action_transition_summary", {})
            if not isinstance(ongoing_action_transition_summary, dict):
                raise SmokeError("desktop_watch capability_request probe ongoing_action_transition_summary was invalid.")
            transition_sequence = ongoing_action_transition_summary.get("transition_sequence", [])
            if not isinstance(transition_sequence, list) or len(transition_sequence) != 2:
                raise SmokeError("desktop_watch capability_request probe transition_sequence was invalid.")
            if transition_sequence[0] not in {"started", "continued"}:
                raise SmokeError(
                    "desktop_watch capability_request probe first transition was invalid:"
                    f" {transition_sequence[0]}"
                )
            if transition_sequence[1] != "completed":
                raise SmokeError("desktop_watch capability_request probe final transition was invalid.")
            if ongoing_action_transition_summary.get("last_capability_id") != "vision.capture":
                raise SmokeError("desktop_watch capability_request probe last_capability_id was invalid.")
            return
        pending_intent_selection = input_trace.get("pending_intent_selection", {})
        if not isinstance(pending_intent_selection, dict):
            raise SmokeError("desktop_watch pending_intent probe selection trace was invalid.")
        if int(pending_intent_selection.get("candidate_pool_count", 0)) < 1:
            raise SmokeError("desktop_watch pending_intent probe candidate_pool_count was invalid.")
        if int(pending_intent_selection.get("eligible_candidate_count", 0)) < 1:
            raise SmokeError("desktop_watch pending_intent probe eligible_candidate_count was invalid.")
        if pending_intent_selection.get("result_status") != "succeeded":
            raise SmokeError("desktop_watch pending_intent probe selection result_status was invalid.")
        if pending_intent_selection.get("selected_candidate_ref") == "none":
            raise SmokeError("desktop_watch pending_intent probe did not select a candidate.")
        schedule_hook = state_type_hooks.get("schedule", {})
        if not isinstance(schedule_hook, dict):
            raise SmokeError("desktop_watch pending_intent probe schedule hook was not recorded.")
        pending_slot_key = schedule_hook.get("pending_intent_slot_key")
        if not isinstance(pending_slot_key, str) or not pending_slot_key.strip():
            raise SmokeError("desktop_watch pending_intent probe pending_intent_slot_key was invalid.")
        schedule_signal_fields = schedule_hook.get("signal_fields", [])
        if not isinstance(schedule_signal_fields, list) or "pending_intent" not in schedule_signal_fields:
            raise SmokeError("desktop_watch pending_intent probe schedule signal_fields were invalid.")
        schedule_policy = next(
            (
                item
                for item in normalized_candidate_policies
                if isinstance(item, dict) and item.get("state_type") == "schedule"
            ),
            None,
        )
        if not isinstance(schedule_policy, dict):
            raise SmokeError("desktop_watch pending_intent probe schedule policy was not recorded.")
        if schedule_policy.get("integration_mode") != "schedule_slot":
            raise SmokeError("desktop_watch pending_intent probe schedule integration_mode was invalid.")
        if schedule_policy.get("integration_key") != f"schedule:{pending_slot_key}":
            raise SmokeError("desktop_watch pending_intent probe schedule integration_key was invalid.")
        if schedule_policy.get("ttl_capped_by") != "pending_intent.expires_at":
            raise SmokeError("desktop_watch pending_intent probe schedule TTL cap was invalid.")
        persisted_schedule_keys = sorted(
            {
                str(state.get("integration_key") or "").strip()
                for state in self._list_persisted_world_states(state_type="schedule")
                if isinstance(state, dict) and str(state.get("integration_key") or "").strip()
            }
        )
        if f"schedule:{pending_slot_key}" not in persisted_schedule_keys:
            raise SmokeError("desktop_watch pending_intent probe schedule integration_key was not persisted.")

    def _assert_external_status_probe_trace(
        self,
        conversation_trace: Any,
        followup_trace: Any,
    ) -> None:
        if not isinstance(conversation_trace, dict):
            raise SmokeError("external.status probe conversation trace was not collected.")
        if not isinstance(followup_trace, dict):
            raise SmokeError("external.status probe follow-up trace was not collected.")

        result_trace = conversation_trace.get("result_trace", {})
        if not isinstance(result_trace, dict):
            raise SmokeError("external.status probe conversation result_trace was invalid.")
        capability_request_summary = result_trace.get("capability_request_summary", {})
        if not isinstance(capability_request_summary, dict):
            raise SmokeError("external.status probe conversation capability_request_summary was invalid.")
        if capability_request_summary.get("capability_id") != "external.status":
            raise SmokeError("external.status probe conversation capability_id was invalid.")
        if capability_request_summary.get("status") != "dispatched":
            raise SmokeError("external.status probe conversation capability_request status was invalid.")
        request_id = capability_request_summary.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise SmokeError("external.status probe conversation request_id was not recorded.")
        ongoing_action_transition_summary = result_trace.get("ongoing_action_transition_summary", {})
        if not isinstance(ongoing_action_transition_summary, dict):
            raise SmokeError("external.status probe conversation ongoing_action_transition_summary was invalid.")
        transition_sequence = ongoing_action_transition_summary.get("transition_sequence", [])
        if not isinstance(transition_sequence, list) or len(transition_sequence) != 1:
            raise SmokeError("external.status probe conversation transition_sequence was invalid.")
        if transition_sequence[0] not in {"started", "continued"}:
            raise SmokeError("external.status probe conversation transition kind was invalid.")
        if ongoing_action_transition_summary.get("final_state") != "waiting_result":
            raise SmokeError("external.status probe conversation final_state was invalid.")
        if ongoing_action_transition_summary.get("last_capability_id") != "external.status":
            raise SmokeError("external.status probe conversation last_capability_id was invalid.")

        followup_cycle_summary = followup_trace.get("cycle_summary", {})
        if not isinstance(followup_cycle_summary, dict):
            raise SmokeError("external.status probe follow-up cycle_summary was invalid.")
        if followup_cycle_summary.get("trigger_kind") != "capability_result":
            raise SmokeError("external.status probe follow-up trigger_kind was invalid.")
        if followup_cycle_summary.get("result_kind") != "reply":
            raise SmokeError("external.status probe follow-up result_kind was invalid.")
        input_trace = followup_trace.get("input_trace", {})
        if not isinstance(input_trace, dict):
            raise SmokeError("external.status probe follow-up input_trace was invalid.")
        observation_summary = input_trace.get("observation_summary", {})
        if not isinstance(observation_summary, dict):
            raise SmokeError("external.status probe follow-up observation_summary was invalid.")
        if observation_summary.get("capability_id") != "external.status":
            raise SmokeError("external.status probe follow-up observation capability_id was invalid.")
        status_text = observation_summary.get("status_text")
        if not isinstance(status_text, str) or "LongSmokeExternalStatusProbeMarker" not in status_text:
            raise SmokeError("external.status probe follow-up status_text was invalid.")
        world_state_trace = followup_trace.get("world_state_trace", {})
        if not isinstance(world_state_trace, dict):
            raise SmokeError("external.status probe follow-up world_state_trace was invalid.")
        source_pack_contexts = world_state_trace.get("source_pack_contexts", {})
        if not isinstance(source_pack_contexts, dict):
            raise SmokeError("external.status probe follow-up source_pack_contexts was invalid.")
        state_type_hooks = world_state_trace.get("source_pack_state_type_hooks", {})
        if not isinstance(state_type_hooks, dict):
            raise SmokeError("external.status probe follow-up source_pack_state_type_hooks was invalid.")
        normalized_candidate_policies = world_state_trace.get("normalized_candidate_policies", [])
        if not isinstance(normalized_candidate_policies, list):
            raise SmokeError("external.status probe follow-up normalized_candidate_policies was invalid.")
        external_service_context = source_pack_contexts.get("external_service_context", {})
        if not isinstance(external_service_context, dict):
            raise SmokeError("external.status probe follow-up external_service_context was invalid.")
        if external_service_context.get("status_text") != status_text:
            raise SmokeError("external.status probe follow-up external_service_context.status_text was invalid.")
        external_service_hook = state_type_hooks.get("external_service", {})
        if not isinstance(external_service_hook, dict):
            raise SmokeError("external.status probe follow-up external_service hook was invalid.")
        if external_service_hook.get("capability_id") != "external.status":
            raise SmokeError("external.status probe follow-up external_service hook capability_id was invalid.")
        if external_service_hook.get("summary_source") != "status_text":
            raise SmokeError("external.status probe follow-up external_service hook summary_source was invalid.")
        if external_service_hook.get("service") != "github":
            raise SmokeError("external.status probe follow-up external_service hook service was invalid.")
        external_signal_fields = external_service_hook.get("signal_fields", [])
        if not isinstance(external_signal_fields, list) or "status_text" not in external_signal_fields:
            raise SmokeError("external.status probe follow-up external_service hook signal_fields was invalid.")
        external_service_policy = next(
            (
                item
                for item in normalized_candidate_policies
                if isinstance(item, dict) and item.get("state_type") == "external_service"
            ),
            None,
        )
        if not isinstance(external_service_policy, dict):
            raise SmokeError("external.status probe follow-up external_service policy was invalid.")
        if external_service_policy.get("summary_source") != "status_text":
            raise SmokeError("external.status probe follow-up external_service policy summary_source was invalid.")
        if external_service_policy.get("effective_ttl_seconds") != 7200:
            raise SmokeError("external.status probe follow-up external_service policy TTL was invalid.")
        if external_service_policy.get("integration_mode") != "external_service_service":
            raise SmokeError("external.status probe follow-up external_service policy integration_mode was invalid.")
        if external_service_policy.get("integration_key") != "external_service:github":
            raise SmokeError("external.status probe follow-up external_service policy integration_key was invalid.")
        device_hook = state_type_hooks.get("device", {})
        if not isinstance(device_hook, dict):
            raise SmokeError("external.status probe follow-up device hook was invalid.")
        if device_hook.get("summary_source") != "device_state_summary":
            raise SmokeError("external.status probe follow-up device hook summary_source was invalid.")
        schedule_hook = state_type_hooks.get("schedule", {})
        if not isinstance(schedule_hook, dict):
            raise SmokeError("external.status probe follow-up schedule hook was invalid.")
        if schedule_hook.get("summary_source") != "schedule_summary":
            raise SmokeError("external.status probe follow-up schedule hook summary_source was invalid.")
        schedule_policy = next(
            (
                item
                for item in normalized_candidate_policies
                if isinstance(item, dict) and item.get("state_type") == "schedule"
            ),
            None,
        )
        if not isinstance(schedule_policy, dict):
            raise SmokeError("external.status probe follow-up schedule policy was invalid.")
        if schedule_policy.get("summary_source") != "schedule_summary":
            raise SmokeError("external.status probe follow-up schedule policy summary_source was invalid.")
        if schedule_policy.get("effective_ttl_seconds") != 5400:
            raise SmokeError("external.status probe follow-up schedule policy TTL was invalid.")
        if schedule_policy.get("integration_key") != "schedule:self":
            raise SmokeError("external.status probe follow-up schedule policy integration_key was invalid.")
        foreground_world_state = input_trace.get("foreground_world_state", [])
        if not isinstance(foreground_world_state, list):
            raise SmokeError("external.status probe follow-up foreground_world_state was invalid.")
        if not any(
            isinstance(item, dict)
            and item.get("state_type") == "external_service"
            and item.get("summary_text") == status_text
            for item in foreground_world_state
        ):
            raise SmokeError("external.status probe follow-up external_service world_state was not reflected.")
        followup_result_trace = followup_trace.get("result_trace", {})
        if not isinstance(followup_result_trace, dict):
            raise SmokeError("external.status probe follow-up result_trace was invalid.")
        capability_result_followup_summary = followup_result_trace.get("capability_result_followup_summary", {})
        if not isinstance(capability_result_followup_summary, dict):
            raise SmokeError("external.status probe follow-up summary was invalid.")
        if capability_result_followup_summary.get("capability_id") != "external.status":
            raise SmokeError("external.status probe follow-up summary capability_id was invalid.")
        source_request_summary = capability_result_followup_summary.get("source_request_summary", {})
        if not isinstance(source_request_summary, dict) or source_request_summary.get("request_id") != request_id:
            raise SmokeError("external.status probe follow-up source_request_summary was invalid.")
        followup_result_summary = capability_result_followup_summary.get("followup_result_summary", {})
        if not isinstance(followup_result_summary, dict) or followup_result_summary.get("result_kind") != "reply":
            raise SmokeError("external.status probe follow-up result summary was invalid.")
        transition_summary = capability_result_followup_summary.get("transition_summary", {})
        if not isinstance(transition_summary, dict):
            raise SmokeError("external.status probe follow-up transition summary was invalid.")
        if transition_summary.get("reason_code") != "followup_reply":
            raise SmokeError("external.status probe follow-up reason_code was invalid.")
        if transition_summary.get("final_state") != "completed":
            raise SmokeError("external.status probe follow-up final_state was invalid.")

    def _is_expected_capture_timeout_failure_trace(self, trace: Any) -> bool:
        if not isinstance(trace, dict):
            return False
        cycle_summary = trace.get("cycle_summary", {})
        if not isinstance(cycle_summary, dict) or cycle_summary.get("trigger_kind") != "desktop_watch":
            return False
        result_trace = trace.get("result_trace", {})
        if not isinstance(result_trace, dict) or result_trace.get("result_kind") != "internal_failure":
            return False
        capability_dispatch_summary = result_trace.get("capability_dispatch_summary", {})
        if not isinstance(capability_dispatch_summary, dict):
            return False
        request_summary = capability_dispatch_summary.get("request_summary", {})
        transition_summary = capability_dispatch_summary.get("transition_summary", {})
        if not isinstance(request_summary, dict) or not isinstance(transition_summary, dict):
            return False
        return (
            request_summary.get("capability_id") == "vision.capture"
            and request_summary.get("status") == "request_timeout"
            and transition_summary.get("reason_code") == "request_timeout"
        )

    def _write_summary(self, summary: dict[str, Any]) -> None:
        self.summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log(
            "summary"
            f" conversations={len(summary['conversation_cycle_ids'])}"
            f" restart_probe={len(summary['restart_probe_cycle_ids'])}"
            f" wake={summary['trigger_counts'].get('wake', 0)}"
            f" background_wake={summary['trigger_counts'].get('background_wake', 0)}"
            f" desktop_watch={summary['trigger_counts'].get('desktop_watch', 0)}"
            f" captures={summary['capture_request_count']}"
            f" external_status={summary['external_status_request_count']}"
            f" dropped={len(summary['capture_timeout_request_ids'])}"
            f" mismatch={len(summary['capture_mismatch_request_ids'])}"
            f" invalid_images={len(summary['capture_invalid_images_request_ids'])}"
            f" invalid_error={len(summary['capture_invalid_error_request_ids'])}"
            f" unknown={len(summary['capture_unknown_request_ids'])}"
        )

    def _stop_server(self) -> None:
        if self.event_client is not None:
            self.event_client.close()
            self.event_client = None
        if self.secondary_event_client is not None:
            self.secondary_event_client.close()
            self.secondary_event_client = None
        if self.server_process is not None:
            if self.server_process.poll() is None:
                self.server_process.terminate()
                try:
                    self.server_process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    self.server_process.kill()
                    self.server_process.wait(timeout=5.0)
            self.server_process = None
        if self.server_log_handle is not None:
            self.server_log_handle.flush()
            self.server_log_handle.close()
            self.server_log_handle = None

    def _assert_server_running(self) -> None:
        if self.server_process is None:
            raise SmokeError("server process is not running.")
        return_code = self.server_process.poll()
        if return_code is not None:
            raise SmokeError(f"server process exited unexpectedly with code {return_code}.")

    def _restart_server_preserving_state(self) -> None:
        self._stop_server()
        self.restart_count += 1
        time.sleep(0.5)
        self._start_server()
        self._wait_server_ready()
        self._bootstrap()
        status = self.api.get("/api/status")
        runtime_summary = status["runtime_summary"]
        if not runtime_summary.get("wake_scheduler_active"):
            raise SmokeError("wake scheduler did not become active after restart.")
        if not runtime_summary.get("memory_job_worker_active"):
            raise SmokeError("memory worker is not active after restart.")
        self._connect_desktop_client()
        log("server restarted and desktop client reconnected")

    def _post_conversation(
        self,
        *,
        text: str,
        source: str,
        client_id: str,
        active_app: str,
        window_title: str,
        images: list[str] | None = None,
        extra_client_context: dict[str, str] | None = None,
    ) -> str:
        payload = {
            "text": text,
            "client_context": {
                "source": source,
                "client_id": client_id,
                "active_app": active_app,
                "window_title": window_title,
                "locale": "ja-JP",
            },
        }
        if isinstance(extra_client_context, dict):
            payload["client_context"].update(extra_client_context)
        if images:
            payload["images"] = images
        response = self.api.post(
            "/api/conversation",
            payload,
        )
        cycle_id = response.get("cycle_id")
        if not isinstance(cycle_id, str) or not cycle_id:
            raise SmokeError("conversation input did not return cycle_id.")
        self.conversation_cycle_ids.append(cycle_id)
        return cycle_id

    def _find_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((self.host, 0))
            return int(probe.getsockname()[1])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="background wake / desktop_watch / memory worker をまとめて回す隔離 long smoke",
    )
    parser.add_argument("--profile", choices=tuple(PROFILE_DEFAULTS.keys()), default="smoke", help="既定値 preset")
    parser.add_argument("--run-seconds", type=int, help="入力を流し続ける秒数")
    parser.add_argument("--conversation-interval-seconds", type=float, help="会話投入間隔")
    parser.add_argument("--desktop-watch-interval-seconds", type=int, help="desktop_watch 間隔")
    parser.add_argument("--wake-interval-seconds", type=int, help="background wake 間隔")
    parser.add_argument("--min-conversation-cycles", type=int, help="最低会話サイクル数")
    parser.add_argument("--capture-timeout-failures", type=int, help="意図的に落とす capture-response 回数")
    parser.add_argument(
        "--capture-mismatch-failures",
        type=int,
        help="意図的に 409 capability_result_client_id_mismatch を起こす回数",
    )
    parser.add_argument(
        "--capture-invalid-images-failures",
        type=int,
        help="意図的に 400 invalid_capability_result を起こす回数",
    )
    parser.add_argument(
        "--capture-invalid-error-failures",
        type=int,
        help="意図的に 400 invalid_capability_result を起こす回数",
    )
    parser.add_argument(
        "--capture-unknown-request-failures",
        type=int,
        help="pending request が無い capture-response を無視できることを確認する回数",
    )
    parser.add_argument("--restart-burst-conversations", type=int, help="再起動前に一気に流す会話数")
    parser.add_argument(
        "--multiple-client-pause-seconds",
        type=float,
        help="複数 desktop client 接続中に capture が止まることを確認する秒数",
    )
    parser.add_argument(
        "--editor-state-mode",
        choices=("mock", "current"),
        default="mock",
        help="mock へ差し替えるか、seed/current editor-state をそのまま使うか",
    )
    parser.add_argument(
        "--seed-data-dir",
        type=Path,
        help="isolated data dir の初期内容としてコピーする既存 data dir",
    )
    parser.add_argument("--desktop-client-id", default="long-smoke-desktop-client", help="擬似 desktop client_id")
    parser.add_argument("--artifact-dir", type=Path, help="成果物を残すディレクトリ")
    parser.add_argument("--keep-artifacts", action="store_true", help="成功時も成果物を削除しない")
    parser.add_argument("--port", type=int, help="固定ポートを使う場合に指定する")
    args = parser.parse_args()
    profile_defaults = PROFILE_DEFAULTS[args.profile]
    for key, value in profile_defaults.items():
        if getattr(args, key) is None:
            setattr(args, key, value)
    return args


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    artifact_dir = args.artifact_dir
    created_temp_dir = False
    if artifact_dir is None:
        artifact_dir = Path(tempfile.mkdtemp(prefix="otomekairo-long-smoke-"))
        created_temp_dir = True
    else:
        artifact_dir = artifact_dir.resolve()

    success = False
    try:
        summary = LongSmokeRunner(repo_root=repo_root, artifact_dir=artifact_dir, args=args).run()
        success = True
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except SmokeError as exc:
        log(f"FAILED: {exc}")
        log(f"artifacts kept at {artifact_dir}")
        return 1
    finally:
        if created_temp_dir and success and not args.keep_artifacts:
            shutil.rmtree(artifact_dir, ignore_errors=True)
        else:
            log(f"artifacts kept at {artifact_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
