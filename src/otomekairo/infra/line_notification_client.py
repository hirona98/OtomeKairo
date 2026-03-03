"""Minimal LINE notification adapter."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen

from otomekairo.gateway.notification_client import (
    LineNotificationRequest,
    LineNotificationResponse,
    NotificationClient,
)


# Block: Adapter constants
LINE_PUSH_ENDPOINT = "https://api.line.me/v2/bot/message/push"


# Block: LINE adapter
@dataclass(frozen=True, slots=True)
class LineNotificationClient(NotificationClient):
    timeout_ms: int = 5_000

    # Block: LINE push execution
    def send_line_text(self, request: LineNotificationRequest) -> LineNotificationResponse:
        if self.timeout_ms <= 0:
            raise RuntimeError("timeout_ms must be positive")
        text = request.text.strip()
        if not text:
            raise RuntimeError("line notification text must be non-empty")
        if len(text) > 5000:
            raise RuntimeError("line notification text is too long")
        if not request.channel_access_token.strip():
            raise RuntimeError("LINE channel access token is required")
        if not request.to_user_id.strip():
            raise RuntimeError("LINE target user id is required")
        started_at = _now_ms()
        body_bytes = json.dumps(
            {
                "to": request.to_user_id,
                "messages": [
                    {
                        "type": "text",
                        "text": text,
                    }
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        http_request = Request(
            url=LINE_PUSH_ENDPOINT,
            data=body_bytes,
            headers={
                "Authorization": f"Bearer {request.channel_access_token}",
                "Content-Type": "application/json",
                "User-Agent": "OtomeKairo/1.0",
            },
            method="POST",
        )
        with urlopen(http_request, timeout=self.timeout_ms / 1000.0) as response:
            response_body = response.read()
            status_code = int(response.status)
        finished_at = _now_ms()
        response_text = response_body.decode("utf-8").strip()
        return LineNotificationResponse(
            raw_result_ref={
                "provider": "line_messaging_api",
                "endpoint": LINE_PUSH_ENDPOINT,
                "status_code": status_code,
            },
            adapter_trace_ref={
                "provider": "line_messaging_api",
                "started_at": started_at,
                "finished_at": finished_at,
                "elapsed_ms": finished_at - started_at,
                "status_code": status_code,
                "response_text": response_text,
            },
        )


# Block: Time helper
def _now_ms() -> int:
    return int(time.time() * 1000)
