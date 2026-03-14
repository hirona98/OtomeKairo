"""Camera capture endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict

from otomekairo.gateway.camera_sensor import CameraCaptureRequest
from otomekairo.web.dependencies import ApiError, AppServices


# Block: Request models
class CameraSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    camera_connection_id: str


# Block: Router factory
def build_camera_router(services: AppServices) -> APIRouter:
    router = APIRouter()

    # Block: Camera capture endpoint
    @router.post("/api/camera/capture", status_code=status.HTTP_201_CREATED)
    async def post_camera_capture(payload: CameraSelectionRequest, response: Response) -> dict[str, object]:
        capture = await _capture_still_image(
            services,
            camera_connection_id=payload.camera_connection_id,
        )
        response.status_code = status.HTTP_201_CREATED
        return {
            "camera_connection_id": capture.camera_connection_id,
            "camera_display_name": capture.camera_display_name,
            "capture_id": capture.capture_id,
            "image_path": capture.image_path,
            "image_url": capture.image_url,
            "captured_at": capture.captured_at,
        }

    # Block: Camera observe endpoint
    @router.post("/api/camera/observe", status_code=status.HTTP_202_ACCEPTED)
    async def post_camera_observe(payload: CameraSelectionRequest, response: Response) -> dict[str, object]:
        capture = await _capture_still_image(
            services,
            camera_connection_id=payload.camera_connection_id,
        )
        response.status_code = status.HTTP_202_ACCEPTED
        return services.cycle_commit_store.enqueue_camera_observation(
            camera_connection_id=capture.camera_connection_id,
            camera_display_name=capture.camera_display_name,
            capture_id=capture.capture_id,
            image_path=capture.image_path,
            image_url=capture.image_url,
            captured_at=capture.captured_at,
        )

    return router


# Block: Shared camera capture
async def _capture_still_image(
    services: AppServices,
    *,
    camera_connection_id: str,
):
    selected_camera_connection_id = _require_enabled_camera_connection(
        services,
        camera_connection_id=camera_connection_id,
    )
    try:
        return await asyncio.to_thread(
            services.camera_sensor.capture_still_image,
            CameraCaptureRequest(camera_connection_id=selected_camera_connection_id),
        )
    except Exception as error:
        raise ApiError(
            status_code=500,
            error_code="camera_capture_failed",
            message=str(error),
        ) from error


# Block: Camera validation helpers
def _require_enabled_camera_connection(
    services: AppServices,
    *,
    camera_connection_id: str,
) -> str:
    normalized_camera_connection_id = camera_connection_id.strip()
    if not normalized_camera_connection_id:
        raise ApiError(
            status_code=400,
            error_code="invalid_request",
            message="camera_connection_id は必須です",
        )
    enabled_camera_connections = services.settings_editor_store.read_enabled_camera_connections()
    if not enabled_camera_connections:
        raise ApiError(
            status_code=409,
            error_code="camera_unavailable",
            message="有効なカメラ接続がありません",
        )
    for camera_connection in enabled_camera_connections:
        if camera_connection.get("camera_connection_id") == normalized_camera_connection_id:
            return normalized_camera_connection_id
    raise ApiError(
        status_code=400,
        error_code="invalid_request",
        message="enabled な camera_connection_id を指定してください",
    )
