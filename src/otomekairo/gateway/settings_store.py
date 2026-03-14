"""Settings override port."""

from __future__ import annotations

from typing import Any, Protocol

from otomekairo.schema.runtime_types import SettingsOverrideRecord


# Block: Settings contract
class SettingsStore(Protocol):
    def read_settings(self, default_settings: dict[str, Any]) -> dict[str, Any]:
        ...

    def enqueue_settings_override(
        self,
        *,
        key: str,
        requested_value_json: dict[str, Any],
        apply_scope: str,
    ) -> dict[str, Any]:
        ...

    def claim_next_settings_override(self) -> SettingsOverrideRecord | None:
        ...

    def append_input_journal_for_settings_override(
        self,
        *,
        settings_override: SettingsOverrideRecord,
        cycle_id: str,
    ) -> None:
        ...

    def finalize_settings_override(
        self,
        *,
        override_id: str,
        key: str,
        requested_value_json: dict[str, Any],
        apply_scope: str,
        cycle_id: str,
        final_status: str,
        reject_reason: str | None,
        camera_available: bool,
    ) -> None:
        ...
