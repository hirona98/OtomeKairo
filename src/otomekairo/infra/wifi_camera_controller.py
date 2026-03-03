"""PyTapo-backed Wi-Fi camera controller."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from otomekairo.gateway.camera_controller import (
    CameraController,
    CameraLookRequest,
    CameraLookResponse,
)


# Block: Environment settings
@dataclass(frozen=True, slots=True)
class CameraConnectionSettings:
    host: str
    username: str
    password: str
    control_port: int
    stream_port: int


# Block: Wi-Fi camera controller
class WiFiCameraController(CameraController):
    def __init__(self) -> None:
        self._settings = _read_connection_settings()
        self._client: Any | None = None

    def is_available(self) -> bool:
        return self._settings is not None

    def move_view(self, request: CameraLookRequest) -> CameraLookResponse:
        if self._settings is None:
            raise RuntimeError("OTOMEKAIRO_CAMERA_HOST / USERNAME / PASSWORD を設定してください")
        if request.preset_id is not None or request.preset_name is not None:
            return self._move_to_preset(request)
        direction = _validated_direction(request.direction)
        raw_result = _move_in_direction(
            camera=self._camera(),
            direction=direction,
        )
        return CameraLookResponse(
            movement_label=_direction_label(direction),
            raw_result_ref=_raw_result_payload(raw_result),
            adapter_trace_ref={
                "camera_host": self._settings.host,
                "movement_mode": "direction",
                "direction": direction,
            },
        )

    # Block: Camera client access
    def _camera(self) -> Any:
        if self._settings is None:
            raise RuntimeError("camera settings are not configured")
        if self._client is None:
            from pytapo import Tapo

            self._client = Tapo(
                self._settings.host,
                self._settings.username,
                self._settings.password,
                controlPort=self._settings.control_port,
                streamPort=self._settings.stream_port,
                printDebugInformation=False,
                printWarnInformation=False,
            )
        return self._client

    # Block: Preset movement
    def _move_to_preset(self, request: CameraLookRequest) -> CameraLookResponse:
        camera = self._camera()
        preset_name = _normalized_optional_text(request.preset_name)
        preset_id = _normalized_optional_text(request.preset_id)
        available_presets = camera.getPresets()
        if not isinstance(available_presets, dict):
            raise RuntimeError("camera presets must be returned as an object")
        resolved_preset_id = preset_id
        resolved_preset_name = preset_name
        if resolved_preset_id is None:
            if resolved_preset_name is None:
                raise RuntimeError("preset_id または preset_name が必要です")
            resolved_preset_id = _preset_id_by_name(
                available_presets=available_presets,
                preset_name=resolved_preset_name,
            )
        if resolved_preset_name is None:
            resolved_preset_name = str(available_presets.get(resolved_preset_id, resolved_preset_id))
        raw_result = camera.setPreset(resolved_preset_id)
        return CameraLookResponse(
            movement_label=f"プリセット {resolved_preset_name}",
            raw_result_ref=_raw_result_payload(raw_result),
            adapter_trace_ref={
                "camera_host": self._settings.host,
                "movement_mode": "preset",
                "preset_id": resolved_preset_id,
                "preset_name": resolved_preset_name,
            },
        )


# Block: Settings loader
def _read_connection_settings() -> CameraConnectionSettings | None:
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


# Block: Direction helpers
def _validated_direction(direction: str | None) -> str:
    normalized_direction = _normalized_optional_text(direction)
    if normalized_direction not in {"left", "right", "up", "down"}:
        raise RuntimeError("direction は left / right / up / down のいずれかにしてください")
    return normalized_direction


def _move_in_direction(*, camera: Any, direction: str) -> Any:
    if direction == "left":
        return camera.moveMotorCounterClockWise()
    if direction == "right":
        return camera.moveMotorClockWise()
    if direction == "up":
        return camera.moveMotorVertical()
    if direction == "down":
        return camera.moveMotorHorizontal()
    raise RuntimeError("unsupported direction")


def _direction_label(direction: str) -> str:
    return {
        "left": "左",
        "right": "右",
        "up": "上",
        "down": "下",
    }[direction]


# Block: Preset helpers
def _preset_id_by_name(*, available_presets: dict[Any, Any], preset_name: str) -> str:
    expected_name = preset_name.casefold()
    for current_preset_id, current_preset_name in available_presets.items():
        if str(current_preset_name).strip().casefold() == expected_name:
            return str(current_preset_id)
    raise RuntimeError(f"preset_name '{preset_name}' は見つかりません")


# Block: Environment helpers
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


# Block: Result payload helper
def _raw_result_payload(raw_result: Any) -> dict[str, Any] | None:
    if raw_result is None:
        return None
    if isinstance(raw_result, dict):
        return raw_result
    return {"value": str(raw_result)}
