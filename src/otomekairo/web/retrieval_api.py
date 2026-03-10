"""Read-only retrieval inspection endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from otomekairo.web.dependencies import ApiError, AppServices


# Block: Router factory
def build_retrieval_router(services: AppServices) -> APIRouter:
    router = APIRouter()

    # Block: Latest retrieval run endpoint
    @router.get("/api/retrieval-runs/latest")
    async def get_latest_retrieval_run() -> dict[str, object]:
        payload = services.store.read_latest_retrieval_run()
        if payload is None:
            raise ApiError(
                status_code=404,
                error_code="not_found",
                message="latest retrieval run is unavailable",
            )
        return payload

    return router
