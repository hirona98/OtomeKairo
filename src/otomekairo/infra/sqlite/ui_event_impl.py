"""SQLite の UI event 実装集約。"""

from __future__ import annotations

from otomekairo.infra.sqlite.ui_chat_history_impl import read_chat_history
from otomekairo.infra.sqlite.ui_event_write_impl import (
    append_ui_outbound_event,
    insert_ui_outbound_event_in_transaction,
)
from otomekairo.infra.sqlite.ui_stream_read_impl import (
    prune_ui_outbound_events,
    read_stream_window,
    read_ui_events,
)

__all__ = [
    "append_ui_outbound_event",
    "insert_ui_outbound_event_in_transaction",
    "prune_ui_outbound_events",
    "read_chat_history",
    "read_stream_window",
    "read_ui_events",
]
