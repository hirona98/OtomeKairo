"""Shared ONVIF-backed Wi-Fi camera helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Block: Camera capture directory
CAMERA_CAPTURE_DIRECTORY = Path(__file__).resolve().parents[3] / "data" / "camera"


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


# Block: Capture directory helper
def default_camera_capture_dir() -> Path:
    CAMERA_CAPTURE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    return CAMERA_CAPTURE_DIRECTORY


# Block: Capture file helpers
def validate_camera_capture_id(capture_id: str) -> str:
    normalized_capture_id = _required_text(capture_id, "capture_id")
    if not normalized_capture_id.startswith("cap_"):
        raise RuntimeError("capture_id must start with cap_")
    suffix = normalized_capture_id[4:]
    if len(suffix) != 32:
        raise RuntimeError("capture_id suffix length is invalid")
    if any(character not in "0123456789abcdef" for character in suffix):
        raise RuntimeError("capture_id suffix must be lowercase hex")
    return normalized_capture_id


def camera_capture_relative_path(capture_id: str) -> Path:
    validated_capture_id = validate_camera_capture_id(capture_id)
    return Path("data") / "camera" / f"{validated_capture_id}.jpg"


def camera_capture_file_path(capture_id: str) -> Path:
    validated_capture_id = validate_camera_capture_id(capture_id)
    return default_camera_capture_dir() / f"{validated_capture_id}.jpg"


def camera_capture_public_url(capture_id: str) -> str:
    validated_capture_id = validate_camera_capture_id(capture_id)
    return f"/captures/{validated_capture_id}.jpg"


# Block: Text helpers
def normalized_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped_value = value.strip()
    if not stripped_value:
        return None
    return stripped_value


def _required_text(value: Any, field_name: str) -> str:
    normalized_value = normalized_optional_text(value)
    if normalized_value is None:
        raise RuntimeError(f"{field_name} must be non-empty string")
    return normalized_value
