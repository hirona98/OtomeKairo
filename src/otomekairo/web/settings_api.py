"""Settings endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, StrictBool, StrictFloat, StrictInt, StrictStr
from fastapi import APIRouter, Response, status

from otomekairo.schema.settings import normalize_requested_value
from otomekairo.web.dependencies import AppServices


# Block: Request models
class SettingsOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    requested_value: StrictStr | StrictInt | StrictFloat | StrictBool
    apply_scope: str


# Block: Router factory
def build_settings_router(services: AppServices) -> APIRouter:
    router = APIRouter()

    # Block: Settings read endpoint
    @router.get("/api/settings")
    async def get_settings() -> dict[str, object]:
        return services.settings_store.read_settings(services.default_settings)

    # Block: Settings editor read endpoint
    @router.get("/api/settings/editor")
    async def get_settings_editor() -> dict[str, object]:
        return services.settings_editor_store.read_settings_editor(services.default_settings)

    # Block: Settings write endpoint
    @router.post("/api/settings/overrides", status_code=status.HTTP_202_ACCEPTED)
    async def post_settings_override(payload: SettingsOverrideRequest, response: Response) -> dict[str, object]:
        normalized_value = normalize_requested_value(
            payload.key,
            payload.requested_value,
            payload.apply_scope,
        )
        response.status_code = status.HTTP_202_ACCEPTED
        return services.settings_store.enqueue_settings_override(
            key=payload.key,
            requested_value_json=normalized_value,
            apply_scope=payload.apply_scope,
        )

    # Block: Settings editor write endpoint
    @router.put("/api/settings/editor")
    async def put_settings_editor(payload: dict[str, object]) -> dict[str, object]:
        return services.settings_editor_store.save_settings_editor(
            default_settings=services.default_settings,
            document=payload,
        )

    return router
