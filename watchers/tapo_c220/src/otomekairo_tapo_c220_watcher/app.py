from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .capture import CaptureError, RtspCameraConfig, RtspFrameCapture
from .config import AppConfig
from .diff import FrameDiffer
from .http import HttpError, JsonApiClient


class TapoC220Watcher:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.http = JsonApiClient(
            base_url=config.server.base_url,
            access_token=config.server.access_token,
            tls_verify=config.server.tls_verify,
            timeout_seconds=config.server.request_timeout_seconds,
        )
        self.last_wake_monotonic: float | None = None
        self.redaction_values: list[str] = [config.server.access_token]

    def run_forever(self) -> None:
        self._log(f"starting watcher_id={self.config.watcher.watcher_id}")
        while True:
            try:
                runtime = self._fetch_runtime_config()
                self._run_runtime(runtime)
            except (HttpError, CaptureError, OSError, RuntimeError, ValueError) as exc:
                self._log(f"watch loop failed error={self._short_error(exc)}")
                time.sleep(self.config.server.reconnect_delay_seconds)

    def _run_runtime(self, runtime: dict[str, Any]) -> None:
        watcher = self._object(runtime.get("watcher"), "runtime.watcher")
        camera_source = self._object(runtime.get("camera_source"), "runtime.camera_source")
        if watcher.get("enabled") is not True or camera_source.get("enabled") is not True:
            self._log("watcher disabled")
            time.sleep(self.config.server.reconnect_delay_seconds)
            return

        camera_config = self._camera_config(camera_source)
        self.redaction_values = [
            self.config.server.access_token,
            camera_config.host,
            camera_config.camera_username,
            camera_config.camera_password,
        ]
        differ = FrameDiffer(
            resize_width=self._int_value(watcher, "resize_width"),
            pixel_diff_threshold=self._int_value(watcher, "pixel_diff_threshold"),
            motion_ratio_threshold=self._float_value(watcher, "motion_ratio_threshold"),
        )
        capture = RtspFrameCapture(camera_config)
        poll_interval = self._float_value(watcher, "poll_interval_seconds")
        min_wake_interval = self._float_value(watcher, "min_wake_interval_seconds")
        jpeg_quality = self._int_value(watcher, "jpeg_quality")
        snapshot_dir = Path(self._text_value(runtime, "snapshot_dir"))
        label = self._text_value(camera_source, "label")

        self._log(f"watching vision_source_id={camera_source.get('vision_source_id')} interval={poll_interval}")
        while True:
            refreshed = self._fetch_runtime_config()
            refreshed_watcher = self._object(refreshed.get("watcher"), "runtime.watcher")
            refreshed_camera_source = self._object(refreshed.get("camera_source"), "runtime.camera_source")
            if refreshed_watcher != watcher or refreshed_camera_source != camera_source:
                self._log("runtime config changed")
                return

            frame = capture.capture_frame(timeout_seconds=camera_config.rtsp_open_timeout_seconds)
            diff = differ.compare(frame)
            if diff.changed and self._wake_due(min_wake_interval):
                snapshot_path = self._snapshot_path(snapshot_dir)
                differ.save_snapshot(frame=frame, path=snapshot_path, jpeg_quality=jpeg_quality)
                self.last_wake_monotonic = time.monotonic()
                self._post_wake(
                    snapshot_path=snapshot_path,
                    label=label,
                    changed_ratio=diff.changed_ratio,
                    threshold=self._float_value(watcher, "motion_ratio_threshold"),
                )
                self._prune_snapshots(snapshot_dir)
            time.sleep(poll_interval)

    def _fetch_runtime_config(self) -> dict[str, Any]:
        encoded_watcher_id = quote(self.config.watcher.watcher_id, safe="")
        return self.http.get(f"/api/config/watchers/{encoded_watcher_id}/runtime-config")

    def _camera_config(self, camera_source: dict[str, Any]) -> RtspCameraConfig:
        connection = self._object(camera_source.get("connection"), "runtime.camera_source.connection")
        return RtspCameraConfig(
            host=self._text_value(connection, "host"),
            camera_username=self._text_value(connection, "camera_username"),
            camera_password=self._text_value(connection, "camera_password"),
        )

    def _wake_due(self, min_wake_interval: float) -> bool:
        if self.last_wake_monotonic is None:
            return True
        return time.monotonic() - self.last_wake_monotonic >= min_wake_interval

    def _snapshot_path(self, snapshot_dir: Path) -> Path:
        timestamp_ms = int(time.time() * 1000)
        return snapshot_dir / f"snapshot-{timestamp_ms}-{uuid.uuid4().hex}.jpg"

    def _post_wake(
        self,
        *,
        snapshot_path: Path,
        label: str,
        changed_ratio: float,
        threshold: float,
    ) -> None:
        payload = {
            "client_context": {
                "source": "tapo_c220_watcher",
                "client_id": self.config.watcher.watcher_id,
                "locale": "ja-JP",
            },
            "reference": {
                "uri": str(snapshot_path),
                "label": f"{label} の変化",
                "reason_summary": (
                    "軽量画像差分が変化を検出した。"
                    f"changed_ratio={changed_ratio:.3f} threshold={threshold:.3f}"
                ),
                "content_hint": "image",
            },
        }
        self.http.post("/api/wake", payload)
        self._log(f"wake posted snapshot={snapshot_path.name} changed_ratio={changed_ratio:.3f}")

    def _prune_snapshots(self, snapshot_dir: Path, *, max_count: int = 200, max_age_hours: float = 24.0) -> None:
        if not snapshot_dir.is_dir():
            return
        now = time.time()
        snapshots = sorted(snapshot_dir.glob("snapshot-*.jpg"), key=lambda path: path.stat().st_mtime, reverse=True)
        for index, snapshot in enumerate(snapshots):
            try:
                too_old = now - snapshot.stat().st_mtime > max_age_hours * 3600.0
                if index >= max_count or too_old:
                    snapshot.unlink()
            except OSError:
                continue

    def _object(self, value: Any, label: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError(f"{label} must be an object.")
        return value

    def _text_value(self, definition: dict[str, Any], key: str) -> str:
        value = definition.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string.")
        return value.strip()

    def _int_value(self, definition: dict[str, Any], key: str) -> int:
        value = definition.get(key)
        if type(value) is not int:
            raise ValueError(f"{key} must be an integer.")
        return value

    def _float_value(self, definition: dict[str, Any], key: str) -> float:
        value = definition.get(key)
        if type(value) not in {int, float}:
            raise ValueError(f"{key} must be a number.")
        return float(value)

    def _short_error(self, exc: BaseException) -> str:
        text = str(exc).strip() or exc.__class__.__name__
        for secret in self.redaction_values:
            if secret:
                text = text.replace(secret, "***")
        return text.replace("\n", " ")[:160]

    def _log(self, message: str) -> None:
        print(f"[tapo-c220-watcher] {message}", file=sys.stderr, flush=True)
