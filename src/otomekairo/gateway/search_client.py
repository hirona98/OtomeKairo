"""Search client abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# Block: Search request
@dataclass(frozen=True, slots=True)
class SearchRequest:
    cycle_id: str
    task_id: str
    query: str


# Block: Search response
@dataclass(frozen=True, slots=True)
class SearchResponse:
    summary_text: str
    raw_result_ref: dict[str, Any] | None
    adapter_trace_ref: dict[str, Any] | None


# Block: Search client protocol
class SearchClient(Protocol):
    def search(self, request: SearchRequest) -> SearchResponse:
        ...
