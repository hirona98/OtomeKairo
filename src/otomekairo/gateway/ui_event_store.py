"""Browser UI event port."""

from __future__ import annotations

from typing import Any, Protocol


# Block: UI event contract
class UiEventStore(Protocol):
    def read_stream_window(self, *, channel: str) -> tuple[int | None, int | None]:
        ...

    def read_chat_history(
        self,
        *,
        channel: str,
        limit: int = 200,
    ) -> dict[str, Any]:
        ...

    def prune_ui_outbound_events(
        self,
        *,
        channel: str,
        retention_window_ms: int,
        retain_minimum_count: int,
    ) -> None:
        ...

    def read_ui_events(
        self,
        *,
        channel: str,
        after_event_id: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        ...

    def append_ui_outbound_event(
        self,
        *,
        channel: str,
        event_type: str,
        payload: dict[str, Any],
        source_cycle_id: str,
    ) -> int:
        ...
