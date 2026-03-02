"""Cognition client abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol


# Block: Cognition request
@dataclass(frozen=True, slots=True)
class CognitionRequest:
    cycle_id: str
    input_kind: str
    cognition_input: dict[str, Any]


# Block: Cognition client protocol
class CognitionClient(Protocol):
    def stream_text(self, request: CognitionRequest) -> Iterable[str]:
        ...
