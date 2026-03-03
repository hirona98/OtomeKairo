"""PyTapo-backed Wi-Fi camera still-image sensor."""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from otomekairo.gateway.camera_sensor import CameraCaptureResponse, CameraSensor
from otomekairo.infra.wifi_camera_common import (
    camera_capture_file_path,
    camera_capture_public_url,
    camera_capture_relative_path,
    create_tapo_stream_client,
    default_camera_capture_dir,
    read_camera_connection_settings,
    read_camera_stream_password,
)


# Block: Capture constants
CAPTURE_TIMEOUT_SECONDS = 10.0
CAPTURE_WAIT_SECONDS = 2.0
STREAM_WINDOW_SIZE = 16
STREAM_QUALITY = "HD"


# Block: Wi-Fi camera sensor
class WiFiCameraSensor(CameraSensor):
    def __init__(self) -> None:
        self._settings = read_camera_connection_settings()
        self._stream_password = read_camera_stream_password()
        self._capture_dir = default_camera_capture_dir()
        self._client: Any | None = None

    def is_available(self) -> bool:
        return self._settings is not None and self._stream_password is not None

    def capture_still_image(self) -> CameraCaptureResponse:
        if self._settings is None:
            raise RuntimeError("OTOMEKAIRO_CAMERA_HOST / USERNAME / PASSWORD を設定してください")
        if self._stream_password is None:
            raise RuntimeError("OTOMEKAIRO_CAMERA_CLOUD_PASSWORD を設定してください")
        return asyncio.run(
            _capture_still_image(
                camera=self._camera(),
                capture_dir=self._capture_dir,
            )
        )

    # Block: Camera client access
    def _camera(self) -> Any:
        if self._settings is None:
            raise RuntimeError("camera settings are not configured")
        if self._stream_password is None:
            raise RuntimeError("camera stream password is not configured")
        if self._client is None:
            self._client = create_tapo_stream_client(
                self._settings,
                stream_password=self._stream_password,
            )
        return self._client


# Block: Async capture helper
async def _capture_still_image(
    *,
    camera: Any,
    capture_dir: Path,
) -> CameraCaptureResponse:
    from pytapo.media_stream.streamer import Streamer

    ffmpeg_process = None
    streamer = Streamer(
        camera,
        mode="pipe",
        quality=STREAM_QUALITY,
        window_size=STREAM_WINDOW_SIZE,
        logLevel="error",
        ff_args={
            "-loglevel": "error",
            "-frames:v": "1",
            "-map-video": "0:v:0",
            "-c:v": "mjpeg",
            "-f": "image2pipe",
        },
    )
    try:
        stream_state = await streamer.start()
        ffmpeg_process = stream_state["ffmpegProcess"]
        output_stream = ffmpeg_process.stdout
        if output_stream is None:
            raise RuntimeError("ffmpeg の出力ストリームを開けませんでした")
        image_bytes = await asyncio.wait_for(
            output_stream.read(),
            timeout=CAPTURE_TIMEOUT_SECONDS,
        )
        return_code = await asyncio.wait_for(
            ffmpeg_process.wait(),
            timeout=CAPTURE_WAIT_SECONDS,
        )
        if return_code != 0:
            raise RuntimeError("ffmpeg による静止画生成に失敗しました")
        if not image_bytes:
            raise RuntimeError("カメラから静止画を取得できませんでした")
        capture_id = f"cap_{uuid.uuid4().hex}"
        file_path = camera_capture_file_path(capture_id)
        if file_path.parent != capture_dir:
            raise RuntimeError("camera capture directory is inconsistent")
        file_path.write_bytes(image_bytes)
        return CameraCaptureResponse(
            capture_id=capture_id,
            image_path=str(camera_capture_relative_path(capture_id)),
            image_url=camera_capture_public_url(capture_id),
            captured_at=_now_ms(),
        )
    except TimeoutError as error:
        raise RuntimeError("カメラ静止画の取得がタイムアウトしました") from error
    finally:
        await streamer.stop()
        if ffmpeg_process is not None and ffmpeg_process.returncode is None:
            ffmpeg_process.terminate()
            await ffmpeg_process.wait()


# Block: Time helper
def _now_ms() -> int:
    return int(time.time() * 1000)
