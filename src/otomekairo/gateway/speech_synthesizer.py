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
    api_key: str
    endpoint_url: str
    model_uuid: str
    speaker_uuid: str
    style_id: int
    use_ssml: bool
    language: str
    speaking_rate: float
    emotional_intensity: float
    tempo_dynamics: float
    pitch: float
    volume: float
    output_format: str


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
