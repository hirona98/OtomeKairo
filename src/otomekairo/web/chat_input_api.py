"""Chat input endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Query, Response, status

from otomekairo.schema.storage_paths import (
    camera_capture_file_path,
    camera_capture_public_url,
    camera_capture_relative_path,
    validate_camera_capture_id,
)
from otomekairo.web.dependencies import ApiError, AppServices


# Block: Attachment constants
SUPPORTED_CHAT_ATTACHMENT_KIND = "camera_still_image"


# Block: Request models
class ChatAttachmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attachment_kind: str
    camera_connection_id: str
    camera_display_name: str
    capture_id: str


class ChatInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    client_message_id: str | None = None
    attachments: list[ChatAttachmentRequest] = Field(default_factory=list)


class ChatCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_message_id: str | None = None


# Block: Router factory
def build_chat_input_router(services: AppServices) -> APIRouter:
    router = APIRouter()

    # Block: Chat input endpoint
    @router.post("/api/chat/input", status_code=status.HTTP_202_ACCEPTED)
    async def post_chat_input(payload: ChatInputRequest, response: Response) -> dict[str, object]:
        response.status_code = status.HTTP_202_ACCEPTED
        normalized_attachments = _normalize_chat_attachments(payload.attachments)
        return services.cycle_commit_store.enqueue_chat_message(
            text=payload.text,
            client_message_id=payload.client_message_id,
            attachments=normalized_attachments,
        )

    # Block: Chat history endpoint
    @router.get("/api/chat/history")
    async def get_chat_history(
        channel: str = Query(default="browser_chat"),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> dict[str, object]:
        if channel != "browser_chat":
            raise ApiError(status_code=400, error_code="invalid_request", message="channel must be browser_chat")
        return services.ui_event_store.read_chat_history(channel=channel, limit=limit)

    # Block: Chat cancel endpoint
    @router.post("/api/chat/cancel", status_code=status.HTTP_202_ACCEPTED)
    async def post_chat_cancel(payload: ChatCancelRequest, response: Response) -> dict[str, object]:
        response.status_code = status.HTTP_202_ACCEPTED
        return services.cycle_commit_store.enqueue_cancel(target_message_id=payload.target_message_id)

    return router


# Block: Attachment normalization
def _normalize_chat_attachments(
    attachments: list[ChatAttachmentRequest],
) -> list[dict[str, object]]:
    normalized_attachments: list[dict[str, object]] = []
    for attachment in attachments:
        if attachment.attachment_kind != SUPPORTED_CHAT_ATTACHMENT_KIND:
            raise ApiError(
                status_code=400,
                error_code="invalid_request",
                message="attachment_kind が不正です",
            )
        try:
            capture_id = validate_camera_capture_id(attachment.capture_id)
        except RuntimeError as error:
            raise ApiError(
                status_code=400,
                error_code="invalid_request",
                message=str(error),
            ) from error
        camera_connection_id = attachment.camera_connection_id.strip()
        camera_display_name = attachment.camera_display_name.strip()
        if not camera_connection_id or not camera_display_name:
            raise ApiError(
                status_code=400,
                error_code="invalid_request",
                message="camera attachment metadata が不正です",
            )
        file_path = camera_capture_file_path(capture_id)
        if not file_path.is_file():
            raise ApiError(
                status_code=400,
                error_code="invalid_request",
                message="capture_id に対応する画像がありません",
            )
        normalized_attachments.append(
            {
                "attachment_kind": SUPPORTED_CHAT_ATTACHMENT_KIND,
                "media_kind": "image",
                "camera_connection_id": camera_connection_id,
                "camera_display_name": camera_display_name,
                "capture_id": capture_id,
                "mime_type": "image/jpeg",
                "storage_path": str(camera_capture_relative_path(capture_id)),
                "content_url": camera_capture_public_url(capture_id),
                "captured_at": int(file_path.stat().st_mtime * 1000),
            }
        )
    return normalized_attachments
