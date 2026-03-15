"""SQLite-backed UI event adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite.ui_event_impl import (
    append_ui_outbound_event,
    prune_ui_outbound_events,
    read_chat_history,
    read_stream_window,
    read_ui_events,
)


# Block: UI event adapter
@dataclass(frozen=True, slots=True)
class SqliteUiEventStore:
    backend: SqliteBackend

    def read_stream_window(self, *, channel: str) -> tuple[int | None, int | None]:
        return read_stream_window(self.backend, channel=channel)

    def read_chat_history(
        self,
        *,
        channel: str,
        limit: int = 200,
    ) -> dict[str, Any]:
        return read_chat_history(self.backend, channel=channel, limit=limit)

    def prune_ui_outbound_events(
        self,
        *,
        channel: str,
        retention_window_ms: int,
        retain_minimum_count: int,
    ) -> None:
        prune_ui_outbound_events(
            self.backend,
            channel=channel,
            retention_window_ms=retention_window_ms,
            retain_minimum_count=retain_minimum_count,
        )

    def read_ui_events(
        self,
        *,
        channel: str,
        after_event_id: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return read_ui_events(
            self.backend,
            channel=channel,
            after_event_id=after_event_id,
            limit=limit,
        )

    def append_ui_outbound_event(
        self,
        *,
        channel: str,
        event_type: str,
        payload: dict[str, Any],
        source_cycle_id: str,
    ) -> int:
        return append_ui_outbound_event(
            self.backend,
            channel=channel,
            event_type=event_type,
            payload=payload,
            source_cycle_id=source_cycle_id,
        )
