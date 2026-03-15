"""SQLite-backed settings editor adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite.runtime_query_impl import (
    read_enabled_camera_connections,
    read_settings_editor,
)
from otomekairo.infra.sqlite.settings_impl import (
    claim_next_settings_change_set,
    finalize_settings_change_set,
    materialize_next_boot_settings,
    save_settings_editor,
)
from otomekairo.schema.runtime_types import SettingsChangeSetRecord


# Block: Settings editor adapter
@dataclass(frozen=True, slots=True)
class SqliteSettingsEditorStore:
    backend: SqliteBackend

    def read_settings_editor(self, default_settings: dict[str, Any]) -> dict[str, Any]:
        return read_settings_editor(self.backend)

    def save_settings_editor(
        self,
        *,
        default_settings: dict[str, Any],
        document: dict[str, object],
    ) -> dict[str, Any]:
        return save_settings_editor(
            self.backend,
            document=document,
        )

    def read_enabled_camera_connections(self) -> list[dict[str, Any]]:
        return read_enabled_camera_connections(self.backend)

    def claim_next_settings_change_set(self) -> SettingsChangeSetRecord | None:
        return claim_next_settings_change_set(self.backend)

    def finalize_settings_change_set(
        self,
        *,
        change_set: SettingsChangeSetRecord,
        default_settings: dict[str, Any],
        final_status: str,
        camera_available: bool,
        reject_reason: str | None = None,
    ) -> None:
        finalize_settings_change_set(
            self.backend,
            change_set=change_set,
            default_settings=default_settings,
            final_status=final_status,
            camera_available=camera_available,
            reject_reason=reject_reason,
        )

    def materialize_next_boot_settings(self) -> None:
        materialize_next_boot_settings(self.backend)
