"""Speech synthesizer abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# Block: Synthesis request
@dataclass(frozen=True, slots=True)
class SpeechSynthesisRequest:
    cycle_id: str
    message_id: str
    text: str
    provider: str
    provider_settings: dict[str, Any]


# Block: Synthesis response
@dataclass(frozen=True, slots=True)
class SpeechSynthesisResponse:
    audio_url: str
    storage_path: str
    mime_type: str
    byte_length: int
    raw_result_ref: dict[str, Any] | None
    adapter_trace_ref: dict[str, Any] | None


# Block: Synthesizer protocol
class SpeechSynthesizer(Protocol):
    def synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResponse:
        ...
