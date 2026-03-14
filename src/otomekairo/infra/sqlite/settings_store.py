"""SQLite-backed settings override adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from otomekairo.infra.sqlite_state_store import SqliteStateStore
from otomekairo.schema.runtime_types import SettingsOverrideRecord


# Block: Settings adapter
@dataclass(frozen=True, slots=True)
class SqliteSettingsStore:
    backend: SqliteStateStore

    def read_settings(self, default_settings: dict[str, Any]) -> dict[str, Any]:
        return self.backend.read_settings(default_settings)

    def enqueue_settings_override(
        self,
        *,
        key: str,
        requested_value_json: dict[str, Any],
        apply_scope: str,
    ) -> dict[str, Any]:
        return self.backend.enqueue_settings_override(
            key=key,
            requested_value_json=requested_value_json,
            apply_scope=apply_scope,
        )

    def claim_next_settings_override(self) -> SettingsOverrideRecord | None:
        return self.backend.claim_next_settings_override()

    def append_input_journal_for_settings_override(
        self,
        *,
        settings_override: SettingsOverrideRecord,
        cycle_id: str,
    ) -> None:
        self.backend.append_input_journal_for_settings_override(
            settings_override=settings_override,
            cycle_id=cycle_id,
        )

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
        self.backend.finalize_settings_override(
            override_id=override_id,
            key=key,
            requested_value_json=requested_value_json,
            apply_scope=apply_scope,
            cycle_id=cycle_id,
            final_status=final_status,
            reject_reason=reject_reason,
            camera_available=camera_available,
        )
