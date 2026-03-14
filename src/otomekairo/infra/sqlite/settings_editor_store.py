"""SQLite-backed settings editor adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from otomekairo.infra.sqlite_state_store import SqliteStateStore
from otomekairo.schema.runtime_types import SettingsChangeSetRecord


# Block: Settings editor adapter
@dataclass(frozen=True, slots=True)
class SqliteSettingsEditorStore:
    backend: SqliteStateStore

    def read_settings_editor(self, default_settings: dict[str, Any]) -> dict[str, Any]:
        return self.backend.read_settings_editor(default_settings)

    def save_settings_editor(
        self,
        *,
        default_settings: dict[str, Any],
        document: dict[str, object],
    ) -> dict[str, Any]:
        return self.backend.save_settings_editor(
            default_settings=default_settings,
            document=document,
        )

    def read_enabled_camera_connections(self) -> list[dict[str, Any]]:
        return self.backend.read_enabled_camera_connections()

    def claim_next_settings_change_set(self) -> SettingsChangeSetRecord | None:
        return self.backend.claim_next_settings_change_set()

    def finalize_settings_change_set(
        self,
        *,
        change_set: SettingsChangeSetRecord,
        default_settings: dict[str, Any],
        final_status: str,
        camera_available: bool,
        reject_reason: str | None = None,
    ) -> None:
        self.backend.finalize_settings_change_set(
            change_set=change_set,
            default_settings=default_settings,
            final_status=final_status,
            camera_available=camera_available,
            reject_reason=reject_reason,
        )

    def materialize_next_boot_settings(self) -> None:
        self.backend.materialize_next_boot_settings()
