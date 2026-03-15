"""SQLite の event writer 集約。"""

from __future__ import annotations

from otomekairo.infra.sqlite.cycle_event_insert_impl import (
    insert_pending_input_events,
    insert_settings_override_events,
    insert_task_cycle_events,
)
from otomekairo.infra.sqlite.event_record_insert_impl import (
    insert_action_history,
    insert_event,
)
from otomekairo.infra.sqlite.input_journal_impl import append_input_journal

__all__ = [
    "append_input_journal",
    "insert_action_history",
    "insert_event",
    "insert_pending_input_events",
    "insert_settings_override_events",
    "insert_task_cycle_events",
]
