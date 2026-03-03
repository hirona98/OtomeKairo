"""Shared PyTapo-backed Wi-Fi camera helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Block: Camera capture directory
CAMERA_CAPTURE_DIRECTORY = Path(__file__).resolve().parents[3] / "data" / "camera"


# Block: Environment settings
@dataclass(frozen=True, slots=True)
class CameraConnectionSettings:
    host: str
    username: str
    password: str
    control_port: int
    stream_port: int


# Block: Settings loaders
def read_camera_connection_settings() -> CameraConnectionSettings | None:
    host = _normalized_optional_text(os.environ.get("OTOMEKAIRO_CAMERA_HOST"))
    username = _normalized_optional_text(os.environ.get("OTOMEKAIRO_CAMERA_USERNAME"))
    password = _normalized_optional_text(os.environ.get("OTOMEKAIRO_CAMERA_PASSWORD"))
    if host is None or username is None or password is None:
        return None
    return CameraConnectionSettings(
        host=host,
        username=username,
        password=password,
        control_port=_read_port("OTOMEKAIRO_CAMERA_CONTROL_PORT", default_value=443),
        stream_port=_read_port("OTOMEKAIRO_CAMERA_STREAM_PORT", default_value=8800),
    )


def read_camera_stream_password() -> str | None:
    return _normalized_optional_text(os.environ.get("OTOMEKAIRO_CAMERA_CLOUD_PASSWORD"))


# Block: Camera client factories
def create_tapo_control_client(settings: CameraConnectionSettings) -> Any:
    from pytapo import Tapo

    return Tapo(
        settings.host,
        settings.username,
        settings.password,
        controlPort=settings.control_port,
        streamPort=settings.stream_port,
        printDebugInformation=False,
        printWarnInformation=False,
    )


def create_tapo_stream_client(
    settings: CameraConnectionSettings,
    *,
    stream_password: str,
) -> Any:
    from pytapo import Tapo

    return Tapo(
        settings.host,
        settings.username,
        settings.password,
        cloudPassword=stream_password,
        controlPort=settings.control_port,
        streamPort=settings.stream_port,
        printDebugInformation=False,
        printWarnInformation=False,
    )


# Block: Capture directory helper
def default_camera_capture_dir() -> Path:
    CAMERA_CAPTURE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    return CAMERA_CAPTURE_DIRECTORY


# Block: Environment helper
def normalized_optional_text(value: Any) -> str | None:
    return _normalized_optional_text(value)


def _normalized_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped_value = value.strip()
    if not stripped_value:
        return None
    return stripped_value


def _read_port(name: str, *, default_value: int) -> int:
    raw_value = _normalized_optional_text(os.environ.get(name))
    if raw_value is None:
        return default_value
    try:
        port = int(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{name} は整数で指定してください") from error
    if port <= 0 or port > 65535:
        raise RuntimeError(f"{name} は 1 から 65535 の範囲で指定してください")
    return port
