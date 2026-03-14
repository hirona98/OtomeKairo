"""Read-only status endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from otomekairo.web.dependencies import AppServices


# Block: Router factory
def build_status_router(services: AppServices) -> APIRouter:
    router = APIRouter()

    # Block: Health endpoint
    @router.get("/api/health")
    async def get_health() -> dict[str, object]:
        return services.runtime_query_store.read_health()

    # Block: Status endpoint
    @router.get("/api/status")
    async def get_status() -> dict[str, object]:
        return services.runtime_query_store.read_status()

    return router
