"""ONVIF-backed Wi-Fi camera still-image sensor."""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable

from otomekairo.gateway.camera_sensor import CameraCaptureResponse, CameraSensor
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


# Block: Wi-Fi camera sensor
class WiFiCameraSensor(CameraSensor):
    def __init__(self, *, camera_connection_loader: Callable[[], dict[str, Any] | None]) -> None:
        self._camera_connection_loader = camera_connection_loader
        self._capture_dir = default_camera_capture_dir()
        self._cached_settings_key: tuple[str, str, str] | None = None
        self._media_service: Any | None = None
        self._profile_token: str | None = None

    def is_available(self) -> bool:
        return self._resolve_settings() is not None

    def capture_still_image(self) -> CameraCaptureResponse:
        settings = self._require_settings()
        media_service = self._media_service_for(settings)
        profile_token = self._profile_token_for(settings)
        snapshot_uri = _read_snapshot_uri(
            media_service=media_service,
            profile_token=profile_token,
        )
        image_bytes = _download_snapshot_bytes(
            settings=settings,
            snapshot_uri=snapshot_uri,
        )
        capture_id = f"cap_{uuid.uuid4().hex}"
        file_path = camera_capture_file_path(capture_id)
        if file_path.parent != self._capture_dir:
            raise RuntimeError("camera capture directory is inconsistent")
        file_path.write_bytes(image_bytes)
        return CameraCaptureResponse(
            capture_id=capture_id,
            image_path=str(camera_capture_relative_path(capture_id)),
            image_url=camera_capture_public_url(capture_id),
            captured_at=_now_ms(),
        )

    # Block: Settings helpers
    def _resolve_settings(self) -> CameraConnectionSettings | None:
        return read_camera_connection_settings(self._camera_connection_loader())

    def _require_settings(self) -> CameraConnectionSettings:
        settings = self._resolve_settings()
        if settings is None:
            raise RuntimeError("カメラ接続が未設定です")
        return settings

    # Block: Client helpers
    def _media_service_for(self, settings: CameraConnectionSettings) -> Any:
        self._ensure_client(settings)
        if self._media_service is None:
            raise RuntimeError("camera media service is not initialized")
        return self._media_service

    def _profile_token_for(self, settings: CameraConnectionSettings) -> str:
        self._ensure_client(settings)
        if self._profile_token is None:
            raise RuntimeError("camera profile token is not initialized")
        return self._profile_token

    def _ensure_client(self, settings: CameraConnectionSettings) -> None:
        settings_key = _settings_key(settings)
        if self._cached_settings_key == settings_key:
            return
        camera = create_onvif_camera(settings)
        media_service = camera.create_media_service()
        profile_token = _read_profile_token(media_service)
        self._cached_settings_key = settings_key
        self._media_service = media_service
        self._profile_token = profile_token


# Block: ONVIF helpers
def _read_profile_token(media_service: Any) -> str:
    profiles = media_service.GetProfiles()
    if not isinstance(profiles, (list, tuple)) or not profiles:
        raise RuntimeError("カメラの ONVIF profile を取得できませんでした")
    profile = profiles[0]
    profile_token = _read_object_value(profile, "token")
    if profile_token is None:
        raise RuntimeError("カメラの ONVIF profile token を取得できませんでした")
    return profile_token


def _read_snapshot_uri(*, media_service: Any, profile_token: str) -> str:
    response = media_service.GetSnapshotUri({"ProfileToken": profile_token})
    snapshot_uri = _read_object_value(response, "Uri")
    if snapshot_uri is None:
        raise RuntimeError("カメラのスナップショット URL を取得できませんでした")
    return snapshot_uri


# Block: Snapshot download
def _download_snapshot_bytes(*, settings: CameraConnectionSettings, snapshot_uri: str) -> bytes:
    authorized_uri = _inject_basic_auth(
        uri=snapshot_uri,
        settings=settings,
    )
    try:
        with urllib.request.urlopen(authorized_uri, timeout=CAPTURE_TIMEOUT_SECONDS) as response:
            image_bytes = response.read()
    except urllib.error.URLError as error:
        raise RuntimeError("カメラのスナップショット取得に失敗しました") from error
    if not image_bytes:
        raise RuntimeError("カメラのスナップショットが空です")
    return image_bytes


def _inject_basic_auth(*, uri: str, settings: CameraConnectionSettings) -> str:
    parsed = urllib.parse.urlsplit(uri)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError("カメラのスナップショット URL が不正です")
    netloc = parsed.netloc
    if not netloc:
        raise RuntimeError("カメラのスナップショット URL にホストがありません")
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
