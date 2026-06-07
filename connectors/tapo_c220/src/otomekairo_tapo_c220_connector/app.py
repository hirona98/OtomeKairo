from __future__ import annotations

import json
import sys
import time
from typing import Any

from .capture import CaptureError, RtspStillCapture
from .config import AppConfig
from .http import HttpError, JsonApiClient
from .ptz import PtzError, TapoPtzController
from .stream import EventStreamClient, StreamError


class TapoC220Connector:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.http = JsonApiClient(
            base_url=config.server.base_url,
            access_token=config.server.access_token,
            tls_verify=config.server.tls_verify,
            timeout_seconds=config.server.request_timeout_seconds,
        )
        self.capture = RtspStillCapture(config.camera)
        self.ptz = TapoPtzController(config.camera)

    def print_hello(self) -> None:
        print(json.dumps(self.config.hello_payload(), ensure_ascii=False, indent=2))

    def check_device(self) -> int:
        failed = False
        try:
            capability = self.ptz.motor_capability()
            self._log(
                "pytapo_motor_capability=ok"
                f" error_code={capability.get('error_code')}"
                f" has_motor={capability.get('has_motor')}"
            )
        except Exception as exc:  # noqa: BLE001
            failed = True
            self._log(f"pytapo_motor_capability=failed error={self._short_error(exc)}")

        try:
            image = self.capture.capture_data_uri(timeout_seconds=self.config.camera.rtsp_open_timeout_seconds)
            prefix = image.split(",", 1)[0]
            self._log(f"rtsp_capture=ok prefix={prefix} bytes={len(image)}")
        except Exception as exc:  # noqa: BLE001
            failed = True
            self._log(f"rtsp_capture=failed error={self._short_error(exc)}")

        return 1 if failed else 0

    def run_forever(self) -> None:
        self._log(
            "starting"
            f" client_id={self.config.connector.client_id}"
            f" vision_source_id={self.config.connector.vision_source_id}"
        )
        while True:
            stream = EventStreamClient(
                base_url=self.config.server.base_url,
                access_token=self.config.server.access_token,
                tls_verify=self.config.server.tls_verify,
                socket_timeout_seconds=self.config.server.request_timeout_seconds,
            )
            try:
                self._log("connecting event stream")
                stream.run(hello_payload=self.config.hello_payload(), on_event=self._handle_event)
            except (OSError, StreamError, json.JSONDecodeError) as exc:
                self._log(f"event stream disconnected: {self._short_error(exc)}")
            finally:
                stream.close()
            time.sleep(self.config.server.reconnect_delay_seconds)

    def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        data = event.get("data")
        if not isinstance(data, dict):
            return
        if event_type == "vision.capture_request":
            self._handle_capture_request(data)
            return
        if event_type == "camera.ptz_request":
            self._handle_ptz_request(data)
            return

    def _handle_capture_request(self, data: dict[str, Any]) -> None:
        request_id = self._request_id(data)
        if request_id is None:
            self._log("ignored vision.capture_request without request_id")
            return
        self._log(f"vision.capture_request received request_id={request_id}")
        client_context = self._source_client_context(data)
        try:
            timeout = self._timeout_seconds(data)
            image = self.capture.capture_data_uri(timeout_seconds=timeout)
            result = {
                "images": [image],
                "client_context": client_context,
                "error": None,
            }
            self._post_result(request_id=request_id, capability_id="vision.capture", result=result)
            self._log(f"vision.capture_result completed request_id={request_id}")
        except Exception as exc:  # noqa: BLE001
            result = {
                "images": [],
                "client_context": client_context,
                "error": self._short_error(exc),
            }
            self._post_result(request_id=request_id, capability_id="vision.capture", result=result)
            self._log(f"vision.capture_result failed request_id={request_id} error={self._short_error(exc)}")

    def _handle_ptz_request(self, data: dict[str, Any]) -> None:
        request_id = self._request_id(data)
        if request_id is None:
            self._log("ignored camera.ptz_request without request_id")
            return
        operation = data.get("operation")
        amount = data.get("amount")
        if not isinstance(operation, str) or not isinstance(amount, str):
            self._log(f"ignored camera.ptz_request with invalid operation request_id={request_id}")
            return
        self._log(f"camera.ptz_request received request_id={request_id} operation={operation} amount={amount}")
        client_context = self._source_client_context(data)
        try:
            self.ptz.move(operation=operation, amount=amount)
            result = {
                "status": "completed",
                "operation": operation,
                "amount": amount,
                "client_context": client_context,
                "error": None,
            }
            self._post_result(request_id=request_id, capability_id="camera.ptz", result=result)
            self._log(f"camera.ptz_result completed request_id={request_id}")
        except Exception as exc:  # noqa: BLE001
            result = {
                "status": "failed",
                "operation": operation,
                "amount": amount,
                "client_context": client_context,
                "error": self._short_error(exc),
            }
            self._post_result(request_id=request_id, capability_id="camera.ptz", result=result)
            self._log(f"camera.ptz_result failed request_id={request_id} error={self._short_error(exc)}")

    def _post_result(self, *, request_id: str, capability_id: str, result: dict[str, Any]) -> None:
        payload = {
            "request_id": request_id,
            "client_id": self.config.connector.client_id,
            "capability_id": capability_id,
            "result": result,
        }
        try:
            self.http.post("/api/capability/result", payload)
        except HttpError as exc:
            self._log(f"capability result post failed request_id={request_id} error={self._short_error(exc)}")

    def _source_client_context(self, data: dict[str, Any]) -> dict[str, str]:
        return {
            "vision_source_id": self._text(data.get("vision_source_id"), self.config.connector.vision_source_id),
            "source_kind": self._text(data.get("source_kind"), "camera"),
            "source_label": self._text(data.get("source_label"), self.config.connector.label),
        }

    def _request_id(self, data: dict[str, Any]) -> str | None:
        request_id = data.get("request_id")
        if isinstance(request_id, str) and request_id.strip():
            return request_id.strip()
        return None

    def _timeout_seconds(self, data: dict[str, Any]) -> float:
        timeout_ms = data.get("timeout_ms")
        if isinstance(timeout_ms, int) and timeout_ms > 0:
            return max(0.5, timeout_ms / 1000.0)
        return self.config.camera.rtsp_open_timeout_seconds

    def _text(self, value: Any, default: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return default

    def _short_error(self, exc: BaseException) -> str:
        text = str(exc).strip() or exc.__class__.__name__
        for secret in (
            self.config.server.access_token,
            self.config.camera.host,
            self.config.camera.username,
            self.config.camera.password,
            self.config.camera.rtsp_username,
            self.config.camera.rtsp_password,
        ):
            if secret:
                text = text.replace(secret, "***")
        return text.replace("\n", " ")[:120]

    def _log(self, message: str) -> None:
        print(f"[tapo-c220-connector] {message}", file=sys.stderr, flush=True)
