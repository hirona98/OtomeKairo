"""Switching speech synthesizer router."""

from __future__ import annotations

from dataclasses import dataclass

from otomekairo.gateway.speech_synthesizer import (
    SpeechSynthesisRequest,
    SpeechSynthesisResponse,
    SpeechSynthesizer,
)


# Block: Provider router
@dataclass(frozen=True, slots=True)
class SwitchingSpeechSynthesizer(SpeechSynthesizer):
    provider_synthesizers: dict[str, SpeechSynthesizer]

    # Block: Dispatch
    def synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResponse:
        provider = request.provider.strip()
        if not provider:
            raise RuntimeError("speech synthesis provider must be non-empty")
        provider_synthesizer = self.provider_synthesizers.get(provider)
        if provider_synthesizer is None:
            raise RuntimeError(f"unsupported speech synthesis provider: {provider}")
        return provider_synthesizer.synthesize(request)
