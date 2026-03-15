"""SQLite の settings 実装集約。"""

from __future__ import annotations

from otomekairo.infra.sqlite.settings_change_set_impl import (
    claim_next_settings_change_set,
    finalize_settings_change_set,
    materialize_next_boot_settings,
)
from otomekairo.infra.sqlite.settings_editor_persistence_impl import save_settings_editor
from otomekairo.infra.sqlite.settings_override_impl import (
    append_input_journal_for_settings_override,
    claim_next_settings_override,
    enqueue_settings_override,
    finalize_settings_override,
    read_settings,
)

__all__ = [
    "append_input_journal_for_settings_override",
    "claim_next_settings_change_set",
    "claim_next_settings_override",
    "enqueue_settings_override",
    "finalize_settings_change_set",
    "finalize_settings_override",
    "materialize_next_boot_settings",
    "read_settings",
    "save_settings_editor",
]
