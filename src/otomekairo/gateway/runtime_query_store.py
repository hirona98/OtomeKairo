"""Read-only runtime query port."""

from __future__ import annotations

from typing import Any, Protocol

from otomekairo.schema.runtime_types import CognitionStateSnapshot


# Block: Runtime query contract
class RuntimeQueryStore(Protocol):
    def read_health(self) -> dict[str, Any]:
        ...

    def read_status(self) -> dict[str, Any]:
        ...

    def read_effective_settings(
        self,
        default_settings: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def read_cognition_state(
        self,
        default_settings: dict[str, Any],
        observation_hint_text: str | None = None,
    ) -> CognitionStateSnapshot:
        ...

    def read_runtime_work_state(self) -> dict[str, bool]:
        ...
