"""Settings editor port."""

from __future__ import annotations

from typing import Any, Protocol

from otomekairo.schema.runtime_types import SettingsChangeSetRecord


# Block: Settings editor contract
class SettingsEditorStore(Protocol):
    def read_settings_editor(self, default_settings: dict[str, Any]) -> dict[str, Any]:
        ...

    def save_settings_editor(
        self,
        *,
        default_settings: dict[str, Any],
        document: dict[str, object],
    ) -> dict[str, Any]:
        ...

    def read_enabled_camera_connections(self) -> list[dict[str, Any]]:
        ...

    def claim_next_settings_change_set(self) -> SettingsChangeSetRecord | None:
        ...

    def finalize_settings_change_set(
        self,
        *,
        change_set: SettingsChangeSetRecord,
        default_settings: dict[str, Any],
        final_status: str,
        camera_available: bool,
        reject_reason: str | None = None,
    ) -> None:
        ...

    def materialize_next_boot_settings(self) -> None:
        ...
