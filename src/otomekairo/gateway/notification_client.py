"""Notification client abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# Block: Notification request
@dataclass(frozen=True, slots=True)
class LineNotificationRequest:
    cycle_id: str
    text: str
    channel_access_token: str
    to_user_id: str


# Block: Notification response
@dataclass(frozen=True, slots=True)
class LineNotificationResponse:
    raw_result_ref: dict[str, Any] | None
    adapter_trace_ref: dict[str, Any] | None


# Block: Notification client protocol
class NotificationClient(Protocol):
    def send_line_text(self, request: LineNotificationRequest) -> LineNotificationResponse:
        ...
