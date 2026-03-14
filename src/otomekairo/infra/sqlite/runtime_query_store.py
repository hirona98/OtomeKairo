"""SQLite-backed runtime query adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend


# Block: Runtime query adapter
@dataclass(frozen=True, slots=True)
class SqliteRuntimeQueryStore:
    backend: SqliteBackend

    def read_health(self) -> dict[str, Any]:
        return self.backend.read_health()

    def read_status(self) -> dict[str, Any]:
        return self.backend.read_status()

    def read_effective_settings(
        self,
        default_settings: dict[str, Any],
    ) -> dict[str, Any]:
        return self.backend.read_effective_settings(default_settings)

    def read_cognition_state(
        self,
        default_settings: dict[str, Any],
        observation_hint_text: str | None = None,
    ):
        return self.backend.read_cognition_state(
            default_settings,
            observation_hint_text=observation_hint_text,
        )

    def read_runtime_work_state(self) -> dict[str, bool]:
        return self.backend.read_runtime_work_state()
