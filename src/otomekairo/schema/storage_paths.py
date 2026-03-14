"""Shared storage path helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any


# Block: Storage directories
DATA_DIRECTORY = Path(__file__).resolve().parents[3] / "data"
CAMERA_CAPTURE_DIRECTORY = DATA_DIRECTORY / "camera"
TTS_AUDIO_DIRECTORY = DATA_DIRECTORY / "audio"


# Block: Camera capture directory
def default_camera_capture_dir() -> Path:
    CAMERA_CAPTURE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    return CAMERA_CAPTURE_DIRECTORY


# Block: TTS audio directory
def default_tts_audio_dir() -> Path:
    TTS_AUDIO_DIRECTORY.mkdir(parents=True, exist_ok=True)
    return TTS_AUDIO_DIRECTORY


# Block: Camera capture validation
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


# Block: Camera capture paths
def camera_capture_relative_path(capture_id: str) -> Path:
    validated_capture_id = validate_camera_capture_id(capture_id)
    return Path("data") / "camera" / f"{validated_capture_id}.jpg"


def camera_capture_file_path(capture_id: str) -> Path:
    validated_capture_id = validate_camera_capture_id(capture_id)
    return default_camera_capture_dir() / f"{validated_capture_id}.jpg"


def camera_capture_public_url(capture_id: str) -> str:
    validated_capture_id = validate_camera_capture_id(capture_id)
    return f"/captures/{validated_capture_id}.jpg"


# Block: Text validation
def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise RuntimeError(f"{field_name} must be non-empty string")
    normalized_value = value.strip()
    if not normalized_value:
        raise RuntimeError(f"{field_name} must be non-empty string")
    return normalized_value
