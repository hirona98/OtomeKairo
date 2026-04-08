from __future__ import annotations


# 定数
REQUIRED_ROLE_NAMES = {
    "reply_generation": "generation",
    "decision_generation": "generation",
    "recall_hint_generation": "generation",
    "memory_interpretation": "generation",
    "embedding": "embedding",
}
FUTURE_ACT_NOT_BEFORE_MINUTES = 30
FUTURE_ACT_EXPIRES_HOURS = 24
WAKE_REPLY_COOLDOWN_MINUTES = 30
BACKGROUND_WAKE_POLL_SECONDS = 5.0
BACKGROUND_DESKTOP_WATCH_POLL_SECONDS = 5.0
DESKTOP_WATCH_CAPTURE_TIMEOUT_MS = 5000


# エラー
class ServiceError(Exception):
    def __init__(self, status_code: int, error_code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
