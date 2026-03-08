"""ONVIF-backed Wi-Fi camera still-image sensor."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
import urllib.parse
import uuid
from typing import Any, Callable

from otomekairo.gateway.camera_sensor import (
    CameraCaptureRequest,
    CameraCaptureResponse,
    CameraSensor,
)
from otomekairo.infra.wifi_camera_common import (
    CameraConnectionSettings,
    camera_capture_file_path,
    camera_capture_public_url,
    camera_capture_relative_path,
    create_onvif_camera,
    default_camera_capture_dir,
    read_camera_connection_settings,
)


# Block: Capture constants
CAPTURE_TIMEOUT_SECONDS = 10.0
FFMPEG_IMAGE_QUALITY = 2


# Block: Wi-Fi camera sensor
class WiFiCameraSensor(CameraSensor):
    def __init__(self, *, camera_connections_loader: Callable[[], list[dict[str, Any]]]) -> None:
        self._camera_connections_loader = camera_connections_loader
        self._capture_dir = default_camera_capture_dir()
        self._cached_settings_key: tuple[str, str, str] | None = None
        self._media_service: Any | None = None
        self._capture_profile_token: str | None = None

    def is_available(self) -> bool:
        try:
            return bool(self._resolved_enabled_settings())
        except RuntimeError:
            return False

    def capture_still_image(self, request: CameraCaptureRequest) -> CameraCaptureResponse:
        settings = self._require_settings(camera_connection_id=request.camera_connection_id)
        media_service = self._media_service_for(settings)
        profile_token = self._capture_profile_token_for(settings)
        stream_uri = _read_stream_uri(
            media_service=media_service,
            profile_token=profile_token,
        )
        capture_id = f"cap_{uuid.uuid4().hex}"
        file_path = camera_capture_file_path(capture_id)
        if file_path.parent != self._capture_dir:
            raise RuntimeError("camera capture directory is inconsistent")
        _capture_stream_frame(
            settings=settings,
            stream_uri=stream_uri,
            output_path=file_path,
        )
        return CameraCaptureResponse(
            camera_connection_id=settings.camera_connection_id,
            camera_display_name=settings.display_name,
            capture_id=capture_id,
            image_path=str(camera_capture_relative_path(capture_id)),
            image_url=camera_capture_public_url(capture_id),
            captured_at=_now_ms(),
        )

    # Block: Settings helpers
    def _resolved_enabled_settings(self) -> list[CameraConnectionSettings]:
        raw_camera_connections = self._camera_connections_loader()
        return [
            settings
            for settings in (
                read_camera_connection_settings(camera_connection)
                for camera_connection in raw_camera_connections
            )
            if settings is not None
        ]

    def _resolve_settings(self, *, camera_connection_id: str | None) -> CameraConnectionSettings | None:
        enabled_settings = self._resolved_enabled_settings()
        if camera_connection_id is None:
            if not enabled_settings:
                return None
            if len(enabled_settings) != 1:
                raise RuntimeError("camera_connection_id is required when multiple enabled cameras exist")
            return enabled_settings[0]
        for settings in enabled_settings:
            if settings.camera_connection_id == camera_connection_id:
                return settings
        raise RuntimeError("requested enabled camera connection is missing")

    def _require_settings(self, *, camera_connection_id: str | None) -> CameraConnectionSettings:
        settings = self._resolve_settings(camera_connection_id=camera_connection_id)
        if settings is None:
            raise RuntimeError("カメラ接続が未設定です")
        return settings

    # Block: Client helpers
    def _media_service_for(self, settings: CameraConnectionSettings) -> Any:
        self._ensure_client(settings)
        if self._media_service is None:
            raise RuntimeError("camera media service is not initialized")
        return self._media_service

    def _capture_profile_token_for(self, settings: CameraConnectionSettings) -> str:
        self._ensure_client(settings)
        if self._capture_profile_token is None:
            raise RuntimeError("camera profile token is not initialized")
        return self._capture_profile_token

    def _ensure_client(self, settings: CameraConnectionSettings) -> None:
        settings_key = _settings_key(settings)
        if self._cached_settings_key == settings_key:
            return
        camera = create_onvif_camera(settings)
        media_service = camera.create_media_service()
        capture_profile_token = _read_capture_profile_token(media_service)
        self._cached_settings_key = settings_key
        self._media_service = media_service
        self._capture_profile_token = capture_profile_token


# Block: ONVIF helpers
def _read_capture_profile_token(media_service: Any) -> str:
    profiles = media_service.GetProfiles()
    if not isinstance(profiles, (list, tuple)) or not profiles:
        raise RuntimeError("カメラの ONVIF profile を取得できませんでした")
    selected_profile_token: str | None = None
    selected_profile_score: tuple[int, int, int] | None = None
    for profile in profiles:
        profile_token = _read_object_value(profile, "token")
        if profile_token is None:
            continue
        profile_score = _profile_capture_score(profile)
        if selected_profile_score is None or profile_score > selected_profile_score:
            selected_profile_token = profile_token
            selected_profile_score = profile_score
    if selected_profile_token is None:
        raise RuntimeError("カメラの ONVIF profile token を取得できませんでした")
    return selected_profile_token


def _read_stream_uri(*, media_service: Any, profile_token: str) -> str:
    response = media_service.GetStreamUri(
        {
            "StreamSetup": {
                "Stream": "RTP-Unicast",
                "Transport": {
                    "Protocol": "RTSP",
                },
            },
            "ProfileToken": profile_token,
        }
    )
    stream_uri = _read_object_value(response, "Uri")
    if stream_uri is None:
        raise RuntimeError("カメラの RTSP stream URL を取得できませんでした")
    return stream_uri


# Block: Profile helpers
def _profile_capture_score(profile: Any) -> tuple[int, int, int]:
    width = _read_profile_dimension(
        profile=profile,
        config_name="VideoEncoderConfiguration",
        field_path=("Resolution", "Width"),
    )
    height = _read_profile_dimension(
        profile=profile,
        config_name="VideoEncoderConfiguration",
        field_path=("Resolution", "Height"),
    )
    if width == 0 or height == 0:
        width = _read_profile_dimension(
            profile=profile,
            config_name="VideoSourceConfiguration",
            field_path=("Bounds", "width"),
        )
        height = _read_profile_dimension(
            profile=profile,
            config_name="VideoSourceConfiguration",
            field_path=("Bounds", "height"),
        )
    return (width * height, width, height)


def _read_profile_dimension(
    *,
    profile: Any,
    config_name: str,
    field_path: tuple[str, ...],
) -> int:
    current_value: Any = getattr(profile, config_name, None)
    for field_name in field_path:
        if current_value is None:
            return 0
        if isinstance(current_value, dict):
            current_value = current_value.get(field_name)
            continue
        current_value = getattr(current_value, field_name, None)
    if isinstance(current_value, int) and current_value > 0:
        return current_value
    return 0


# Block: Stream capture
def _capture_stream_frame(
    *,
    settings: CameraConnectionSettings,
    stream_uri: str,
    output_path: Path,
) -> None:
    command = _ffmpeg_command(
        settings=settings,
        stream_uri=stream_uri,
        output_path=output_path,
    )
    try:
        completed_process = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=CAPTURE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        _unlink_capture_file(output_path)
        raise RuntimeError("カメラ映像のフレーム取得がタイムアウトしました") from error
    if completed_process.returncode != 0:
        _unlink_capture_file(output_path)
        raise RuntimeError("カメラ映像のフレーム取得に失敗しました")
    if not output_path.exists():
        raise RuntimeError("カメラ静止画ファイルを生成できませんでした")
    if output_path.stat().st_size <= 0:
        _unlink_capture_file(output_path)
        raise RuntimeError("カメラ静止画ファイルが空です")


def _ffmpeg_command(
    *,
    settings: CameraConnectionSettings,
    stream_uri: str,
    output_path: Path,
) -> list[str]:
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        raise RuntimeError("ffmpeg が見つかりません")
    authorized_uri = _inject_basic_auth(
        uri=stream_uri,
        settings=settings,
    )
    return [
        ffmpeg_path,
        "-nostdin",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        authorized_uri,
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-q:v",
        str(FFMPEG_IMAGE_QUALITY),
        "-f",
        "image2",
        "-y",
        str(output_path),
    ]


def _inject_basic_auth(*, uri: str, settings: CameraConnectionSettings) -> str:
    parsed = urllib.parse.urlsplit(uri)
    if parsed.scheme not in {"rtsp", "rtsps"}:
        raise RuntimeError("カメラの RTSP stream URL が不正です")
    netloc = parsed.netloc
    if not netloc:
        raise RuntimeError("カメラの RTSP stream URL にホストがありません")
    host_netloc = netloc.rsplit("@", 1)[-1]
    encoded_username = urllib.parse.quote(settings.username, safe="")
    encoded_password = urllib.parse.quote(settings.password, safe="")
    authorized_netloc = f"{encoded_username}:{encoded_password}@{host_netloc}"
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            authorized_netloc,
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


# Block: File helpers
def _unlink_capture_file(output_path: Path) -> None:
    if output_path.exists():
        output_path.unlink()


# Block: Generic object helpers
def _read_object_value(source: Any, field_name: str) -> str | None:
    if isinstance(source, dict):
        raw_value = source.get(field_name)
        if isinstance(raw_value, str) and raw_value:
            return raw_value
    raw_value = getattr(source, field_name, None)
    if isinstance(raw_value, str) and raw_value:
        return raw_value
    return None


def _settings_key(settings: CameraConnectionSettings) -> tuple[str, str, str]:
    return (
        settings.host,
        settings.username,
        settings.password,
    )


# Block: Time helper
def _now_ms() -> int:
    return int(time.time() * 1000)
