from __future__ import annotations

import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import quote


class CaptureError(RuntimeError):
    pass


# FFmpeg/libavcodec が RTSP デコード時に stderr へ出す既知ノイズ。
# 例: [h264 @ 0x29cbd800] SEI type 764 size 66 truncated at 65
_H264_SEI_TRUNCATED_RE = re.compile(
    rb"^\[h264 @ 0x[0-9a-fA-F]+\] SEI type \d+ size \d+ truncated at \d+$"
)
_stderr_filter_lock = threading.Lock()


def _is_h264_sei_truncated_line(line: bytes) -> bool:
    return _H264_SEI_TRUNCATED_RE.fullmatch(line.strip()) is not None


@contextmanager
def _suppress_h264_sei_truncated_stderr() -> Iterator[None]:
    """fd 2 上の h264 SEI truncated 行だけを落とし、他の stderr はそのまま通す。"""
    with _stderr_filter_lock:
        read_fd, write_fd = os.pipe()
        saved_stderr_fd = os.dup(2)
        os.dup2(write_fd, 2)
        os.close(write_fd)
        done = threading.Event()

        def _pump() -> None:
            buffer = b""
            try:
                while True:
                    try:
                        chunk = os.read(read_fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    buffer += chunk
                    while True:
                        newline_at = buffer.find(b"\n")
                        if newline_at < 0:
                            break
                        line = buffer[: newline_at + 1]
                        buffer = buffer[newline_at + 1 :]
                        if not _is_h264_sei_truncated_line(line):
                            os.write(saved_stderr_fd, line)
                if buffer and not _is_h264_sei_truncated_line(buffer):
                    os.write(saved_stderr_fd, buffer)
            finally:
                try:
                    os.close(read_fd)
                except OSError:
                    pass
                done.set()

        thread = threading.Thread(target=_pump, name="h264-sei-stderr-filter", daemon=True)
        thread.start()
        try:
            yield
        finally:
            # パイプ書き込み端（現在の fd 2）を閉じて pump を EOF させる。
            # saved_stderr_fd は pump が書き終わるまで閉じない。
            os.dup2(saved_stderr_fd, 2)
            done.wait(timeout=2.0)
            thread.join(timeout=0.1)
            os.close(saved_stderr_fd)


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
        try:
            with _suppress_h264_sei_truncated_stderr():
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
        finally:
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
