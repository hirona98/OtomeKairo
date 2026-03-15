"""SQLite の write_memory 状態更新集約。"""

from __future__ import annotations

from otomekairo.infra.sqlite.write_memory_self_state_sync_impl import (
    sync_current_emotion_from_long_mood_state,
)
from otomekairo.infra.sqlite.write_memory_state_update_impl import apply_state_updates

__all__ = [
    "apply_state_updates",
    "sync_current_emotion_from_long_mood_state",
]
