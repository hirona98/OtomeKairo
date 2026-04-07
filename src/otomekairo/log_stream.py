from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from otomekairo.event_stream import ServerWebSocket


# Block: Constants
MAX_BUFFERED_LOG_MESSAGES = 200


# Block: Registry
class LogStreamRegistry:
    def __init__(self) -> None:
        # Block: Fields
        self._lock = threading.RLock()
        self._sessions: dict[str, ServerWebSocket] = {}
        self._recent_logs: list[dict[str, Any]] = []

    def add_connection(self, websocket: ServerWebSocket) -> str:
        # Block: Session
        session_id = f"log_stream_session:{uuid.uuid4().hex}"
        with self._lock:
            self._sessions[session_id] = websocket
            snapshot = list(self._recent_logs)

        # Block: Replay
        if snapshot:
            try:
                websocket.send_text(json.dumps(snapshot, ensure_ascii=False))
            except OSError:
                self.remove_connection(session_id)

        # Block: Result
        return session_id

    def remove_connection(self, session_id: str) -> None:
        # Block: Remove
        with self._lock:
            self._sessions.pop(session_id, None)

    def append_logs(self, logs: list[dict[str, Any]]) -> None:
        # Block: Empty
        if not logs:
            return

        # Block: Snapshot
        with self._lock:
            self._recent_logs.extend(logs)
            if len(self._recent_logs) > MAX_BUFFERED_LOG_MESSAGES:
                self._recent_logs = self._recent_logs[-MAX_BUFFERED_LOG_MESSAGES:]
            sessions = list(self._sessions.items())

        # Block: Broadcast
        failed_session_ids: list[str] = []
        for session_id, websocket in sessions:
            try:
                websocket.send_text(json.dumps(logs, ensure_ascii=False))
            except OSError:
                failed_session_ids.append(session_id)

        # Block: Cleanup
        if not failed_session_ids:
            return
        with self._lock:
            for session_id in failed_session_ids:
                self._sessions.pop(session_id, None)
