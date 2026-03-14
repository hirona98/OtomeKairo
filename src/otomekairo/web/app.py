"""FastAPI application assembly."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from otomekairo import __version__
from otomekairo.infra.speech_synthesis_common import default_tts_audio_dir
from otomekairo.infra.amivoice_speech_recognizer import AmivoiceSpeechRecognizer
from otomekairo.infra.wifi_camera_common import default_camera_capture_dir
from otomekairo.infra.wifi_camera_sensor import WiFiCameraSensor
from otomekairo.infra.sqlite_state_store import SqliteStateStore
from otomekairo.infra.sqlite_store_errors import StoreConflictError, StoreValidationError
from otomekairo.schema.settings import SettingsValidationError, build_default_settings
from otomekairo.web.camera_api import build_camera_router
from otomekairo.web.dependencies import ApiError, AppServices
from otomekairo.web.chat_input_api import build_chat_input_router
from otomekairo.web.chat_stream_api import build_chat_stream_router
from otomekairo.web.microphone_api import build_microphone_router
from otomekairo.web.settings_api import build_settings_router
from otomekairo.web.status_api import build_status_router


# Block: Stream janitor constants
STREAM_JANITOR_INTERVAL_MS = 60_000
STREAM_RETENTION_WINDOW_MS = 86_400_000
STREAM_RETAIN_MINIMUM_COUNT = 20_000


# Block: Request context middleware
class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, services: AppServices) -> None:
        self._app = app
        self._services = services
        self._last_stream_janitor_at = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        state["request_id"] = f"req_{uuid.uuid4().hex}"
        self._last_stream_janitor_at = _run_stream_janitor_if_due(
            services=self._services,
            last_stream_janitor_at=self._last_stream_janitor_at,
        )
        await self._app(scope, receive, send)


# Block: App factory
def create_app() -> FastAPI:
    store = SqliteStateStore(_default_db_path(), __version__)
    store.initialize()
    default_settings = build_default_settings()
    camera_sensor = WiFiCameraSensor(
        camera_connections_loader=store.read_enabled_camera_connections,
    )
    services = AppServices(
        store=store,
        default_settings=default_settings,
        camera_sensor=camera_sensor,
        speech_recognizer=AmivoiceSpeechRecognizer(),
    )
    static_dir = _static_dir()
    capture_dir = _camera_capture_dir()
    tts_audio_dir = _tts_audio_dir()

    app = FastAPI(title="OtomeKairo Settings Server", version=__version__)
    app.state.services = services
    app.add_middleware(RequestContextMiddleware, services=services)

    # Block: Browser UI static files
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.mount("/captures", StaticFiles(directory=capture_dir), name="captures")
    app.mount("/audio", StaticFiles(directory=tts_audio_dir), name="audio")

    # Block: API error handler
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, error: ApiError) -> JSONResponse:
        return _error_response(
            request=request,
            status_code=error.status_code,
            error_code=error.error_code,
            message=error.message,
        )

    # Block: Store validation handler
    @app.exception_handler(StoreValidationError)
    async def handle_store_validation_error(request: Request, error: StoreValidationError) -> JSONResponse:
        return _error_response(
            request=request,
            status_code=400,
            error_code="invalid_request",
            message=str(error),
        )

    # Block: Store conflict handler
    @app.exception_handler(StoreConflictError)
    async def handle_store_conflict_error(request: Request, error: StoreConflictError) -> JSONResponse:
        return _error_response(
            request=request,
            status_code=409,
            error_code=error.error_code,
            message=error.message,
        )

    # Block: Settings validation handler
    @app.exception_handler(SettingsValidationError)
    async def handle_settings_validation_error(request: Request, error: SettingsValidationError) -> JSONResponse:
        return _error_response(
            request=request,
            status_code=400,
            error_code=error.error_code,
            message=error.message,
        )

    # Block: Request validation handler
    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(request: Request, _: RequestValidationError) -> JSONResponse:
        return _error_response(
            request=request,
            status_code=400,
            error_code="invalid_request",
            message="request validation failed",
        )

    # Block: HTTP exception handler
    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, error: StarletteHTTPException) -> JSONResponse:
        if error.status_code == 404:
            return _error_response(
                request=request,
                status_code=404,
                error_code="not_found",
                message="resource not found",
            )
        if error.status_code == 405:
            return _error_response(
                request=request,
                status_code=405,
                error_code="method_not_allowed",
                message="method not allowed",
            )
        return _error_response(
            request=request,
            status_code=error.status_code,
            error_code="http_error",
            message="request failed",
        )

    # Block: Generic error handler
    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
        return _error_response(
            request=request,
            status_code=500,
            error_code="internal_server_error",
            message=str(error),
        )

    # Block: Router registration
    app.include_router(build_status_router(services))
    app.include_router(build_settings_router(services))
    app.include_router(build_chat_input_router(services))
    app.include_router(build_chat_stream_router(services))
    app.include_router(build_camera_router(services))
    app.include_router(build_microphone_router(services))

    # Block: Browser UI entrypoint
    @app.get("/", include_in_schema=False)
    async def get_browser_ui() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    # Block: Browser favicon
    @app.get("/favicon.ico", include_in_schema=False)
    async def get_browser_favicon() -> FileResponse:
        return FileResponse(static_dir / "favicon.ico")

    return app


# Block: Default database path
def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "core.sqlite3"


# Block: Static asset path
def _static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


# Block: Camera capture path
def _camera_capture_dir() -> Path:
    return default_camera_capture_dir()


# Block: TTS audio path
def _tts_audio_dir() -> Path:
    audio_dir = default_tts_audio_dir()
    audio_dir.mkdir(parents=True, exist_ok=True)
    return audio_dir


# Block: Stream janitor
def _run_stream_janitor_if_due(
    *,
    services: AppServices,
    last_stream_janitor_at: int,
) -> int:
    now_ms = _now_ms()
    if now_ms - last_stream_janitor_at < STREAM_JANITOR_INTERVAL_MS:
        return last_stream_janitor_at
    services.store.prune_ui_outbound_events(
        channel="browser_chat",
        retention_window_ms=STREAM_RETENTION_WINDOW_MS,
        retain_minimum_count=STREAM_RETAIN_MINIMUM_COUNT,
    )
    return now_ms


# Block: Error response helper
def _error_response(
    *,
    request: Request,
    status_code: int,
    error_code: str,
    message: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error_code": error_code,
            "message": message,
            "request_id": request.state.request_id,
        },
    )


# Block: Time helper
def _now_ms() -> int:
    return int(time.time() * 1000)
