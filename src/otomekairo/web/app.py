"""FastAPI application assembly."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from otomekairo import __version__
from otomekairo.infra.sqlite_state_store import StoreConflictError, StoreValidationError, SqliteStateStore
from otomekairo.schema.settings import SettingsValidationError, build_effective_settings
from otomekairo.web.dependencies import ApiError, AppServices
from otomekairo.web.chat_input_api import build_chat_input_router
from otomekairo.web.chat_stream_api import build_chat_stream_router
from otomekairo.web.settings_api import build_settings_router
from otomekairo.web.status_api import build_status_router


# Block: App factory
def create_app() -> FastAPI:
    store = SqliteStateStore(_default_db_path(), __version__)
    store.initialize()
    services = AppServices(store=store, effective_settings=build_effective_settings())

    app = FastAPI(title="OtomeKairo Settings Server", version=__version__)
    app.state.services = services

    # Block: Request id middleware
    @app.middleware("http")
    async def assign_request_id(request: Request, call_next):
        request.state.request_id = f"req_{uuid.uuid4().hex}"
        return await call_next(request)

    # Block: API error handler
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error_code": error.error_code,
                "message": error.message,
                "request_id": request.state.request_id,
            },
        )

    # Block: Store validation handler
    @app.exception_handler(StoreValidationError)
    async def handle_store_validation_error(request: Request, error: StoreValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error_code": "invalid_request",
                "message": str(error),
                "request_id": request.state.request_id,
            },
        )

    # Block: Store conflict handler
    @app.exception_handler(StoreConflictError)
    async def handle_store_conflict_error(request: Request, error: StoreConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error_code": "conflict",
                "message": str(error),
                "request_id": request.state.request_id,
            },
        )

    # Block: Settings validation handler
    @app.exception_handler(SettingsValidationError)
    async def handle_settings_validation_error(request: Request, error: SettingsValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error_code": error.error_code,
                "message": error.message,
                "request_id": request.state.request_id,
            },
        )

    # Block: Generic error handler
    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "error_code": "internal_server_error",
                "message": str(error),
                "request_id": request.state.request_id,
            },
        )

    # Block: Router registration
    app.include_router(build_status_router(services))
    app.include_router(build_settings_router(services))
    app.include_router(build_chat_input_router(services))
    app.include_router(build_chat_stream_router(services))

    return app


# Block: Default database path
def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "core.sqlite3"
