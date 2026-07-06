from __future__ import annotations

import os
import time
from dataclasses import dataclass
from urllib.parse import quote


class CaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class RtspCameraConfig:
    host: str
    camera_username: str
    camera_password: str
    rtsp_port: int = 554
    rtsp_path: str = "stream1"
    rtsp_transport: str = "tcp"
    rtsp_open_timeout_seconds: float = 8.0
    opencv_ffmpeg_loglevel: str = "error"


class RtspFrameCapture:
    def __init__(self, config: RtspCameraConfig) -> None:
        self.config = config

    def capture_frame(self, *, timeout_seconds: float | None = None) -> object:
        try:
            import cv2  # type: ignore[import-not-found]
        except ImportError as exc:
            raise CaptureError("opencv-python is not installed.") from exc

        timeout = timeout_seconds or self.config.rtsp_open_timeout_seconds
        deadline = time.monotonic() + timeout
        old_options = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
        old_loglevel = os.environ.get("OPENCV_FFMPEG_LOGLEVEL")
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{self.config.rtsp_transport}"
        os.environ["OPENCV_FFMPEG_LOGLEVEL"] = self.config.opencv_ffmpeg_loglevel
        capture = cv2.VideoCapture(self._rtsp_url(), cv2.CAP_FFMPEG)
        try:
            while time.monotonic() < deadline:
                ok, frame = capture.read()
                if ok and frame is not None:
                    return frame
                time.sleep(0.05)
            raise CaptureError("rtsp_capture_timeout")
        finally:
            capture.release()
            if old_options is None:
                os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
            else:
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = old_options
            if old_loglevel is None:
                os.environ.pop("OPENCV_FFMPEG_LOGLEVEL", None)
            else:
                os.environ["OPENCV_FFMPEG_LOGLEVEL"] = old_loglevel

    def _rtsp_url(self) -> str:
        username = quote(self.config.camera_username, safe="")
        password = quote(self.config.camera_password, safe="")
        host = self.config.host
        path = quote(self.config.rtsp_path, safe="/")
        return f"rtsp://{username}:{password}@{host}:{self.config.rtsp_port}/{path}"
