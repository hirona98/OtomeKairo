"""SSE chat stream endpoint."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Header, Query
from fastapi.responses import StreamingResponse

from otomekairo.web.dependencies import ApiError, AppServices


# Block: Stream constants
HEARTBEAT_INTERVAL_SECONDS = 15.0
POLL_INTERVAL_SECONDS = 1.0


# Block: Router factory
def build_chat_stream_router(services: AppServices) -> APIRouter:
    router = APIRouter()

    # Block: Chat stream endpoint
    @router.get("/api/chat/stream")
    async def get_chat_stream(
        channel: str = Query(default="browser_chat"),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        if channel != "browser_chat":
            raise ApiError(status_code=400, error_code="invalid_request", message="channel must be browser_chat")
        parsed_last_event_id = _parse_last_event_id(last_event_id)
        stream_window = services.store.read_stream_window(channel=channel)
        event_stream = _stream_events(
            services=services,
            channel=channel,
            last_event_id=parsed_last_event_id,
            stream_window=stream_window,
        )
        return StreamingResponse(event_stream, media_type="text/event-stream")

    return router


# Block: Header parsing
def _parse_last_event_id(last_event_id: str | None) -> int | None:
    if last_event_id is None:
        return None
    try:
        parsed_value = int(last_event_id)
    except ValueError as error:
        raise ApiError(status_code=400, error_code="invalid_request", message="Last-Event-ID must be an integer") from error
    if parsed_value < 0:
        raise ApiError(status_code=400, error_code="invalid_request", message="Last-Event-ID must be non-negative")
    return parsed_value


# Block: SSE generator
async def _stream_events(
    *,
    services: AppServices,
    channel: str,
    last_event_id: int | None,
    stream_window: tuple[int | None, int | None],
):
    min_event_id, _ = stream_window
    current_event_id = last_event_id or 0
    if last_event_id is not None and min_event_id is not None and last_event_id < (min_event_id - 1):
        yield _format_synthetic_notice(
            event_type="notice",
            payload={
                "notice_code": "stream_reset",
                "text": "保持範囲外のため利用可能な最古のイベントから再開します",
            },
        )
        current_event_id = min_event_id - 1

    heartbeat_started_at = time.monotonic()
    while True:
        events = services.store.read_ui_events(
            channel=channel,
            after_event_id=current_event_id,
        )
        if events:
            for event in events:
                current_event_id = event["ui_event_id"]
                yield _format_sse_event(
                    ui_event_id=current_event_id,
                    event_type=event["event_type"],
                    payload=event["payload"],
                )
            heartbeat_started_at = time.monotonic()
            continue
        if time.monotonic() - heartbeat_started_at >= HEARTBEAT_INTERVAL_SECONDS:
            yield ": heartbeat\n\n"
            heartbeat_started_at = time.monotonic()
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


# Block: SSE formatting
def _format_sse_event(*, ui_event_id: int, event_type: str, payload: dict[str, object]) -> str:
    payload_text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return f"id: {ui_event_id}\nevent: {event_type}\ndata: {payload_text}\n\n"


def _format_synthetic_notice(*, event_type: str, payload: dict[str, object]) -> str:
    payload_text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return f"event: {event_type}\ndata: {payload_text}\n\n"
