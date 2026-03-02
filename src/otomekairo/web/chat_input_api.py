"""Chat input endpoints."""

from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Response, status

from otomekairo.web.dependencies import AppServices


# Block: Request models
class ChatInputRequest(BaseModel):
    text: str
    client_message_id: str | None = None


class ChatCancelRequest(BaseModel):
    target_message_id: str | None = None


# Block: Router factory
def build_chat_input_router(services: AppServices) -> APIRouter:
    router = APIRouter()

    # Block: Chat input endpoint
    @router.post("/api/chat/input", status_code=status.HTTP_202_ACCEPTED)
    async def post_chat_input(payload: ChatInputRequest, response: Response) -> dict[str, object]:
        response.status_code = status.HTTP_202_ACCEPTED
        return services.store.enqueue_chat_message(
            text=payload.text,
            client_message_id=payload.client_message_id,
        )

    # Block: Chat cancel endpoint
    @router.post("/api/chat/cancel", status_code=status.HTTP_202_ACCEPTED)
    async def post_chat_cancel(payload: ChatCancelRequest, response: Response) -> dict[str, object]:
        response.status_code = status.HTTP_202_ACCEPTED
        return services.store.enqueue_cancel(target_message_id=payload.target_message_id)

    return router
