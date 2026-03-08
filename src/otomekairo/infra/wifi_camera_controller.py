"""ONVIF-backed Wi-Fi camera controller."""

from __future__ import annotations

import time
from typing import Any, Callable

from otomekairo.gateway.camera_controller import (
    CameraCandidate,
    CameraController,
    CameraLookRequest,
    CameraLookResponse,
    CameraPresetCandidate,
)
from otomekairo.infra.wifi_camera_common import (
    CameraConnectionSettings,
    create_onvif_camera,
    normalized_optional_text,
    read_camera_connection_settings,
)


# Block: PTZ constants
MOVE_DURATION_SECONDS = 0.35
MOVE_SPEED = 0.6


# Block: Direction vectors
DIRECTION_VECTORS = {
    "left": (-MOVE_SPEED, 0.0),
    "right": (MOVE_SPEED, 0.0),
    "up": (0.0, MOVE_SPEED),
    "down": (0.0, -MOVE_SPEED),
}


# Block: Wi-Fi camera controller
class WiFiCameraController(CameraController):
    def __init__(self, *, camera_connections_loader: Callable[[], list[dict[str, Any]]]) -> None:
        self._camera_connections_loader = camera_connections_loader
        self._cached_settings_key: tuple[str, str, str] | None = None
        self._ptz_service: Any | None = None
        self._profile_token: str | None = None
        self._preset_candidates_cache: dict[tuple[str, str, str], tuple[CameraPresetCandidate, ...]] = {}

    def is_available(self) -> bool:
        try:
            return bool(self._resolved_enabled_settings())
        except RuntimeError:
            return False

    # Block: Candidate listing
    def list_candidates(self) -> list[CameraCandidate]:
        return [
            CameraCandidate(
                camera_connection_id=settings.camera_connection_id,
                display_name=settings.display_name,
                can_look=True,
                can_capture=True,
                presets=self._preset_candidates_for(settings),
            )
            for settings in self._resolved_enabled_settings()
        ]

    def move_view(self, request: CameraLookRequest) -> CameraLookResponse:
        settings = self._require_settings(camera_connection_id=request.camera_connection_id)
        ptz_service = self._ptz_service_for(settings)
        profile_token = self._profile_token_for(settings)
        if request.preset_id is not None or request.preset_name is not None:
            return self._move_to_preset(
                settings=settings,
                ptz_service=ptz_service,
                profile_token=profile_token,
                request=request,
            )
        direction = _validated_direction(request.direction)
        raw_result = _move_in_direction(
            ptz_service=ptz_service,
            profile_token=profile_token,
            direction=direction,
        )
        return CameraLookResponse(
            camera_connection_id=settings.camera_connection_id,
            camera_display_name=settings.display_name,
            movement_label=_direction_label(direction),
            raw_result_ref=_raw_result_payload(raw_result),
            adapter_trace_ref={
                "camera_connection_id": settings.camera_connection_id,
                "camera_display_name": settings.display_name,
                "camera_host": settings.host,
                "movement_mode": "direction",
                "direction": direction,
            },
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

    def _resolve_settings(self, *, camera_connection_id: str) -> CameraConnectionSettings | None:
        enabled_settings = self._resolved_enabled_settings()
        for settings in enabled_settings:
            if settings.camera_connection_id == camera_connection_id:
                return settings
        return None

    def _require_settings(self, *, camera_connection_id: str) -> CameraConnectionSettings:
        settings = self._resolve_settings(camera_connection_id=camera_connection_id)
        if settings is None:
            raise RuntimeError("requested enabled camera connection is missing")
        return settings

    # Block: Client helpers
    def _ptz_service_for(self, settings: CameraConnectionSettings) -> Any:
        self._ensure_client(settings)
        if self._ptz_service is None:
            raise RuntimeError("camera ptz service is not initialized")
        return self._ptz_service

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
        ptz_service = camera.create_ptz_service()
        profile_token = _read_profile_token(media_service)
        self._cached_settings_key = settings_key
        self._ptz_service = ptz_service
        self._profile_token = profile_token

    # Block: Preset candidate cache
    def _preset_candidates_for(
        self,
        settings: CameraConnectionSettings,
    ) -> tuple[CameraPresetCandidate, ...]:
        settings_key = _settings_key(settings)
        cached_candidates = self._preset_candidates_cache.get(settings_key)
        if cached_candidates is not None:
            return cached_candidates
        ptz_service = self._ptz_service_for(settings)
        profile_token = self._profile_token_for(settings)
        available_presets = _read_available_presets(
            ptz_service=ptz_service,
            profile_token=profile_token,
        )
        preset_candidates = _build_preset_candidates(available_presets)
        self._preset_candidates_cache[settings_key] = preset_candidates
        return preset_candidates

    # Block: Preset movement
    def _move_to_preset(
        self,
        *,
        settings: CameraConnectionSettings,
        ptz_service: Any,
        profile_token: str,
        request: CameraLookRequest,
    ) -> CameraLookResponse:
        preset_name = normalized_optional_text(request.preset_name)
        preset_id = normalized_optional_text(request.preset_id)
        available_presets = _read_available_presets(
            ptz_service=ptz_service,
            profile_token=profile_token,
        )
        resolved_preset_token = preset_id
        resolved_preset_name = preset_name
        if resolved_preset_token is None:
            if resolved_preset_name is None:
                raise RuntimeError("preset_id または preset_name が必要です")
            resolved_preset_token = _preset_token_by_name(
                available_presets=available_presets,
                preset_name=resolved_preset_name,
            )
        if resolved_preset_name is None:
            resolved_preset_name = _preset_name_by_token(
                available_presets=available_presets,
                preset_token=resolved_preset_token,
            )
        raw_result = ptz_service.GotoPreset(
            {
                "ProfileToken": profile_token,
                "PresetToken": resolved_preset_token,
            }
        )
        return CameraLookResponse(
            camera_connection_id=settings.camera_connection_id,
            camera_display_name=settings.display_name,
            movement_label=f"プリセット {resolved_preset_name}",
            raw_result_ref=_raw_result_payload(raw_result),
            adapter_trace_ref={
                "camera_connection_id": settings.camera_connection_id,
                "camera_display_name": settings.display_name,
                "camera_host": settings.host,
                "movement_mode": "preset",
                "preset_id": resolved_preset_token,
                "preset_name": resolved_preset_name,
            },
        )


# Block: ONVIF helpers
def _read_profile_token(media_service: Any) -> str:
    profiles = media_service.GetProfiles()
    if not isinstance(profiles, (list, tuple)) or not profiles:
        raise RuntimeError("カメラの ONVIF profile を取得できませんでした")
    profile_token = _read_object_value(profiles[0], "token")
    if profile_token is None:
        raise RuntimeError("カメラの ONVIF profile token を取得できませんでした")
    return profile_token


# Block: Preset listing helpers
def _read_available_presets(*, ptz_service: Any, profile_token: str) -> list[Any]:
    available_presets = ptz_service.GetPresets({"ProfileToken": profile_token})
    if not isinstance(available_presets, (list, tuple)):
        raise RuntimeError("camera presets must be returned as a list")
    return list(available_presets)


def _build_preset_candidates(
    available_presets: list[Any],
) -> tuple[CameraPresetCandidate, ...]:
    preset_candidates: list[CameraPresetCandidate] = []
    for current_preset in available_presets:
        preset_id = _read_object_value(current_preset, "token")
        if preset_id is None:
            continue
        preset_name = _read_object_value(current_preset, "Name") or preset_id
        preset_candidates.append(
            CameraPresetCandidate(
                preset_id=preset_id,
                preset_name=preset_name,
            )
        )
    return tuple(preset_candidates)


# Block: Direction helpers
def _validated_direction(direction: str | None) -> str:
    normalized_direction = normalized_optional_text(direction)
    if normalized_direction not in DIRECTION_VECTORS:
        raise RuntimeError("direction は left / right / up / down のいずれかにしてください")
    return normalized_direction


def _move_in_direction(*, ptz_service: Any, profile_token: str, direction: str) -> Any:
    pan_speed, tilt_speed = DIRECTION_VECTORS[direction]
    move_request = {
        "ProfileToken": profile_token,
        "Velocity": {
            "PanTilt": {
                "x": pan_speed,
                "y": tilt_speed,
            }
        },
    }
    raw_result = ptz_service.ContinuousMove(move_request)
    time.sleep(MOVE_DURATION_SECONDS)
    ptz_service.Stop(
        {
            "ProfileToken": profile_token,
            "PanTilt": True,
            "Zoom": True,
        }
    )
    return raw_result


def _direction_label(direction: str) -> str:
    return {
        "left": "左",
        "right": "右",
        "up": "上",
        "down": "下",
    }[direction]


# Block: Preset helpers
def _preset_token_by_name(*, available_presets: list[Any], preset_name: str) -> str:
    expected_name = preset_name.casefold()
    for current_preset in available_presets:
        current_name = _read_object_value(current_preset, "Name")
        current_token = _read_object_value(current_preset, "token")
        if current_name is None or current_token is None:
            continue
        if current_name.casefold() == expected_name:
            return current_token
    raise RuntimeError(f"preset_name '{preset_name}' は見つかりません")


def _preset_name_by_token(*, available_presets: list[Any], preset_token: str) -> str:
    for current_preset in available_presets:
        current_token = _read_object_value(current_preset, "token")
        current_name = _read_object_value(current_preset, "Name")
        if current_token == preset_token:
            return current_name or preset_token
    return preset_token


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


# Block: Result payload helper
def _raw_result_payload(raw_result: Any) -> dict[str, Any] | None:
    if raw_result is None:
        return None
    if isinstance(raw_result, dict):
        return raw_result
    return {"value": str(raw_result)}
