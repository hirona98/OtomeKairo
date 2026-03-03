"""Camera capture endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Response, status

from otomekairo.web.dependencies import ApiError, AppServices


# Block: Router factory
def build_camera_router(services: AppServices) -> APIRouter:
    router = APIRouter()

    # Block: Camera capture endpoint
    @router.post("/api/camera/capture", status_code=status.HTTP_201_CREATED)
    async def post_camera_capture(response: Response) -> dict[str, object]:
        capture = await _capture_still_image(services)
        response.status_code = status.HTTP_201_CREATED
        return {
            "capture_id": capture.capture_id,
            "image_path": capture.image_path,
            "image_url": capture.image_url,
            "captured_at": capture.captured_at,
        }

    # Block: Camera observe endpoint
    @router.post("/api/camera/observe", status_code=status.HTTP_202_ACCEPTED)
    async def post_camera_observe(response: Response) -> dict[str, object]:
        capture = await _capture_still_image(services)
        response.status_code = status.HTTP_202_ACCEPTED
        return services.store.enqueue_camera_observation(
            capture_id=capture.capture_id,
            image_path=capture.image_path,
            image_url=capture.image_url,
            captured_at=capture.captured_at,
        )

    return router


# Block: Shared camera capture
async def _capture_still_image(services: AppServices):
    if not services.camera_sensor.is_available():
        raise ApiError(
            status_code=409,
            error_code="camera_unavailable",
            message="カメラの接続設定が不足しています",
        )
    try:
        return await asyncio.to_thread(services.camera_sensor.capture_still_image)
    except Exception as error:
        raise ApiError(
            status_code=500,
            error_code="camera_capture_failed",
            message=str(error),
        ) from error
