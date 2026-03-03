"""Cognition client abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# Block: Cognition request
@dataclass(frozen=True, slots=True)
class CognitionRequest:
    cycle_id: str
    input_kind: str
    cognition_input: dict[str, Any]


# Block: Cognition response
@dataclass(frozen=True, slots=True)
class CognitionResponse:
    cognition_result: dict[str, Any]


# Block: Cognition client protocol
class CognitionClient(Protocol):
    def generate_result(self, request: CognitionRequest) -> CognitionResponse:
        ...
