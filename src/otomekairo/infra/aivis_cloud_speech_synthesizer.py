"""Aivis Cloud backed speech synthesizer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from otomekairo.gateway.speech_synthesizer import (
    SpeechSynthesisRequest,
    SpeechSynthesisResponse,
    SpeechSynthesizer,
)
from otomekairo.infra.speech_synthesis_common import (
    DEFAULT_TIMEOUT_MS,
    execute_http_request,
    now_ms,
    persist_audio_file,
    require_audio_response,
)


# Block: Aivis cloud adapter
@dataclass(frozen=True, slots=True)
class AivisCloudSpeechSynthesizer(SpeechSynthesizer):
    audio_output_dir: Path
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    # Block: Speech synthesis
    def synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResponse:
        if request.provider != "aivis-cloud":
            raise RuntimeError("Aivis Cloud synthesizer requires provider aivis-cloud")
        if self.timeout_ms <= 0:
            raise RuntimeError("timeout_ms must be positive")
        normalized_text = request.text.strip()
        if not normalized_text:
            raise RuntimeError("speech text must be non-empty")
        provider_settings = _required_provider_settings(request.provider_settings)
        started_at = now_ms()
        payload = _build_request_payload(
            provider_settings=provider_settings,
            normalized_text=normalized_text,
        )
        response_mime_type, response_body = _post_synthesis_request(
            endpoint_url=_required_non_empty_string(provider_settings, "endpoint_url"),
            api_key=_required_non_empty_string(provider_settings, "api_key"),
            payload=payload,
            timeout_ms=self.timeout_ms,
        )
        require_audio_response(
            provider_label="Aivis Cloud",
            response_mime_type=response_mime_type,
            response_body=response_body,
        )
        output_file = persist_audio_file(
            audio_output_dir=self.audio_output_dir,
            message_id=request.message_id,
            preferred_output_format=_required_non_empty_string(provider_settings, "output_format"),
            response_mime_type=response_mime_type,
            response_body=response_body,
        )
        finished_at = now_ms()
        audio_url = f"/audio/{output_file.name}"
        return SpeechSynthesisResponse(
            audio_url=audio_url,
            storage_path=str(output_file),
            mime_type=response_mime_type,
            byte_length=len(response_body),
            raw_result_ref={
                "provider": "aivis-cloud",
                "endpoint_url": provider_settings["endpoint_url"],
                "audio_url": audio_url,
                "storage_path": str(output_file),
                "mime_type": response_mime_type,
                "byte_length": len(response_body),
            },
            adapter_trace_ref={
                "provider": "aivis-cloud",
                "cycle_id": request.cycle_id,
                "message_id": request.message_id,
                "started_at": started_at,
                "finished_at": finished_at,
                "elapsed_ms": finished_at - started_at,
                "model_uuid": provider_settings["model_uuid"],
                "speaker_uuid": provider_settings["speaker_uuid"],
                "style_id": provider_settings["style_id"],
                "text_length": len(normalized_text),
                "output_format": provider_settings["output_format"],
            },
        )


# Block: Request payload build
def _build_request_payload(
    *,
    provider_settings: dict[str, object],
    normalized_text: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model_uuid": _required_non_empty_string(provider_settings, "model_uuid"),
        "speaker_uuid": _required_non_empty_string(provider_settings, "speaker_uuid"),
        "style_id": _required_integer(provider_settings, "style_id"),
        "text": normalized_text,
        "use_ssml": _required_boolean(provider_settings, "use_ssml"),
        "language": _required_non_empty_string(provider_settings, "language"),
        "speaking_rate": _required_number(provider_settings, "speaking_rate"),
        "emotional_intensity": _required_number(provider_settings, "emotional_intensity"),
        "tempo_dynamics": _required_number(provider_settings, "tempo_dynamics"),
        "pitch": _required_number(provider_settings, "pitch"),
        "volume": _required_number(provider_settings, "volume"),
        "output_format": _required_non_empty_string(provider_settings, "output_format"),
    }
    return payload


# Block: Remote request
def _post_synthesis_request(
    *,
    endpoint_url: str,
    api_key: str,
    payload: dict[str, object],
    timeout_ms: int,
) -> tuple[str, bytes]:
    request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response_mime_type, response_body = execute_http_request(
        provider_label="Aivis Cloud",
        url=endpoint_url,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "audio/*",
        },
        request_body=request_body,
        timeout_ms=timeout_ms,
    )
    return response_mime_type, response_body


# Block: Provider settings validation
def _required_provider_settings(provider_settings: object) -> dict[str, object]:
    if not isinstance(provider_settings, dict):
        raise RuntimeError("Aivis Cloud provider_settings must be object")
    return provider_settings


def _required_non_empty_string(provider_settings: dict[str, object], key: str) -> str:
    value = provider_settings.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Aivis Cloud {key} must be non-empty string")
    return value.strip()


def _required_integer(provider_settings: dict[str, object], key: str) -> int:
    value = provider_settings.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"Aivis Cloud {key} must be integer")
    return value


def _required_number(provider_settings: dict[str, object], key: str) -> float:
    value = provider_settings.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"Aivis Cloud {key} must be number")
    return float(value)


def _required_boolean(provider_settings: dict[str, object], key: str) -> bool:
    value = provider_settings.get(key)
    if not isinstance(value, bool):
        raise RuntimeError(f"Aivis Cloud {key} must be boolean")
    return value
