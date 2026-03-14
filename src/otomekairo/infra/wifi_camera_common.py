"""Shared ONVIF-backed Wi-Fi camera helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Block: Camera connection constants
CAMERA_ONVIF_PORT = 2020


# Block: Camera connection settings
@dataclass(frozen=True, slots=True)
class CameraConnectionSettings:
    camera_connection_id: str
    display_name: str
    host: str
    username: str
    password: str


# Block: Settings decode
def read_camera_connection_settings(camera_connection: dict[str, Any] | None) -> CameraConnectionSettings | None:
    if camera_connection is None:
        return None
    camera_connection_id = normalized_optional_text(camera_connection.get("camera_connection_id"))
    display_name = normalized_optional_text(camera_connection.get("display_name"))
    host = normalized_optional_text(camera_connection.get("host"))
    username = normalized_optional_text(camera_connection.get("username"))
    password = normalized_optional_text(camera_connection.get("password"))
    if camera_connection_id is None or display_name is None or host is None or username is None or password is None:
        return None
    return CameraConnectionSettings(
        camera_connection_id=camera_connection_id,
        display_name=display_name,
        host=host,
        username=username,
        password=password,
    )


# Block: ONVIF client factory
def create_onvif_camera(settings: CameraConnectionSettings) -> Any:
    from onvif import ONVIFCamera

    return ONVIFCamera(
        settings.host,
        CAMERA_ONVIF_PORT,
        settings.username,
        settings.password,
    )


# Block: Text helpers
def normalized_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped_value = value.strip()
    if not stripped_value:
        return None
    return stripped_value
