from __future__ import annotations

import threading
import time
from typing import Any

from .config import CameraConfig


class PtzError(RuntimeError):
    pass


class OnvifPtzController:
    def __init__(self, config: CameraConfig) -> None:
        self.config = config
        self._camera: Any | None = None
        self._media: Any | None = None
        self._ptz: Any | None = None
        self._profile_token: str | None = None
        self._lock = threading.Lock()

    def move(self, *, operation: str, amount: str) -> None:
        with self._lock:
            vector = self.config.operation_vectors.get(operation)
            if vector is None:
                raise PtzError("unsupported_operation")
            duration_seconds = self._duration_for_amount(amount)
            x = vector[0] * self.config.ptz_velocity
            y = vector[1] * self.config.ptz_velocity
            self._continuous_move(x=x, y=y, duration_seconds=duration_seconds)

    def service_capability(self) -> dict[str, Any]:
        with self._lock:
            profiles = self._media_service().GetProfiles()
            token = self._select_profile_token(profiles)
            self._ptz_service().GetServiceCapabilities()
            return {
                "profile_count": len(profiles),
                "profile_token_present": bool(token),
            }

    def _duration_for_amount(self, amount: str) -> float:
        if amount == "small":
            return self.config.small_move_seconds
        if amount == "medium":
            return self.config.medium_move_seconds
        raise PtzError("unsupported_amount")

    def _continuous_move(self, *, x: float, y: float, duration_seconds: float) -> None:
        ptz = self._ptz_service()
        profile_token = self._profile_token_value()
        request = ptz.create_type("ContinuousMove")
        request.ProfileToken = profile_token
        request.Velocity = {"PanTilt": {"x": x, "y": y}}
        try:
            ptz.ContinuousMove(request)
        except Exception as exc:  # noqa: BLE001
            raise PtzError("ptz_move_failed") from exc
        try:
            time.sleep(duration_seconds)
        finally:
            self._stop(ptz=ptz, profile_token=profile_token)

    def _stop(self, *, ptz: Any, profile_token: str) -> None:
        request = ptz.create_type("Stop")
        request.ProfileToken = profile_token
        request.PanTilt = True
        request.Zoom = False
        try:
            ptz.Stop(request)
        except Exception as exc:  # noqa: BLE001
            raise PtzError("ptz_stop_failed") from exc

    def _profile_token_value(self) -> str:
        if self._profile_token is not None:
            return self._profile_token
        profiles = self._media_service().GetProfiles()
        self._profile_token = self._select_profile_token(profiles)
        return self._profile_token

    def _select_profile_token(self, profiles: Any) -> str:
        if not isinstance(profiles, list) or not profiles:
            raise PtzError("onvif_profile_not_found")
        for profile in profiles:
            token = getattr(profile, "token", None)
            if token and getattr(profile, "PTZConfiguration", None) is not None:
                return str(token)
        for profile in profiles:
            token = getattr(profile, "token", None)
            if token:
                return str(token)
        raise PtzError("onvif_profile_token_not_found")

    def _media_service(self) -> Any:
        if self._media is None:
            self._media = self._onvif_camera().create_media_service()
        return self._media

    def _ptz_service(self) -> Any:
        if self._ptz is None:
            self._ptz = self._onvif_camera().create_ptz_service()
        return self._ptz

    def _onvif_camera(self) -> Any:
        if self._camera is not None:
            return self._camera
        try:
            from onvif import ONVIFCamera  # type: ignore[import-not-found]
        except ImportError as exc:
            raise PtzError("onvif-zeep is not installed.") from exc
        self._camera = ONVIFCamera(
            self.config.host,
            self.config.onvif_port,
            self.config.onvif_username,
            self.config.onvif_password,
            no_cache=True,
        )
        return self._camera
