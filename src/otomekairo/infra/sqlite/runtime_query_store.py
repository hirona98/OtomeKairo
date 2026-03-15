"""SQLite-backed runtime query adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite.runtime_query_impl import (
    read_cognition_state,
    read_effective_settings,
    read_health,
    read_runtime_work_state,
    read_status,
)


# Block: Runtime query adapter
@dataclass(frozen=True, slots=True)
class SqliteRuntimeQueryStore:
    backend: SqliteBackend

    def read_health(self) -> dict[str, Any]:
        return read_health()

    def read_status(self) -> dict[str, Any]:
        return read_status(self.backend)

    def read_effective_settings(
        self,
        default_settings: dict[str, Any],
    ) -> dict[str, Any]:
        return read_effective_settings(self.backend, default_settings)

    def read_cognition_state(
        self,
        default_settings: dict[str, Any],
        observation_hint_text: str | None = None,
    ):
        return read_cognition_state(
            self.backend,
            default_settings,
            observation_hint_text=observation_hint_text,
        )

    def read_runtime_work_state(self) -> dict[str, bool]:
        return read_runtime_work_state(self.backend)
