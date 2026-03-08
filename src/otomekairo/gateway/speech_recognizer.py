"""Speech recognizer abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# Block: Recognition request
@dataclass(frozen=True, slots=True)
class SpeechRecognitionRequest:
    provider: str
    audio_bytes: bytes
    audio_mime_type: str
    file_name: str
    provider_settings: dict[str, Any]


# Block: Recognition response
@dataclass(frozen=True, slots=True)
class SpeechRecognitionResponse:
    transcript_text: str
    provider: str
    language: str
    raw_result_ref: dict[str, Any] | None
    adapter_trace_ref: dict[str, Any] | None


# Block: Recognizer protocol
class SpeechRecognizer(Protocol):
    def recognize(self, request: SpeechRecognitionRequest) -> SpeechRecognitionResponse:
        ...
