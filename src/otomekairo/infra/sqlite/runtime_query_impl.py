"""SQLite の runtime query 集約。"""

from __future__ import annotations

from otomekairo.infra.sqlite.runtime_cognition_query_impl import read_cognition_state
from otomekairo.infra.sqlite.runtime_settings_editor_query_impl import (
    read_enabled_camera_connections,
    read_settings_editor,
)
from otomekairo.infra.sqlite.runtime_status_query_impl import (
    read_effective_settings,
    read_health,
    read_runtime_work_state,
    read_status,
)

__all__ = [
    "read_cognition_state",
    "read_effective_settings",
    "read_enabled_camera_connections",
    "read_health",
    "read_runtime_work_state",
    "read_settings_editor",
    "read_status",
]
