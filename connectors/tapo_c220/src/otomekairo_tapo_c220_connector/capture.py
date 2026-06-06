from __future__ import annotations

import base64
import os
import time
from urllib.parse import quote

from .config import CameraConfig


class CaptureError(RuntimeError):
    pass


class RtspStillCapture:
    def __init__(self, config: CameraConfig) -> None:
        self.config = config

    def capture_data_uri(self, *, timeout_seconds: float | None = None) -> str:
        try:
            import cv2  # type: ignore[import-not-found]
        except ImportError as exc:
            raise CaptureError("opencv-python is not installed.") from exc

        timeout = timeout_seconds or self.config.rtsp_open_timeout_seconds
        deadline = time.monotonic() + timeout
        old_options = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{self.config.rtsp_transport}"
        capture = cv2.VideoCapture(self._rtsp_url(), cv2.CAP_FFMPEG)
        try:
            while time.monotonic() < deadline:
                ok, frame = capture.read()
                if ok and frame is not None:
                    encode_ok, encoded = cv2.imencode(
                        ".jpg",
                        frame,
                        [int(cv2.IMWRITE_JPEG_QUALITY), self.config.jpeg_quality],
                    )
                    if not encode_ok:
                        raise CaptureError("jpeg_encode_failed")
                    image_bytes = encoded.tobytes()
                    return "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
                time.sleep(0.05)
            raise CaptureError("rtsp_capture_timeout")
        finally:
            capture.release()
            if old_options is None:
                os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
            else:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = old_options

    def _rtsp_url(self) -> str:
        username = quote(self.config.rtsp_username, safe="")
        password = quote(self.config.rtsp_password, safe="")
        host = self.config.host
        path = quote(self.config.rtsp_path, safe="/")
        return f"rtsp://{username}:{password}@{host}:{self.config.rtsp_port}/{path}"
