"""Style-Bert-VITS2 backed speech synthesizer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from otomekairo.gateway.speech_synthesizer import (
    SpeechSynthesisRequest,
    SpeechSynthesisResponse,
    SpeechSynthesizer,
)
from otomekairo.infra.speech_synthesis_common import (
    DEFAULT_TIMEOUT_MS,
    execute_http_request,
    join_base_url,
    now_ms,
    persist_audio_file,
    require_audio_response,
)


# Block: Style-Bert-VITS2 adapter
@dataclass(frozen=True, slots=True)
class StyleBertVits2SpeechSynthesizer(SpeechSynthesizer):
    audio_output_dir: Path
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    # Block: Speech synthesis
    def synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResponse:
        if request.provider != "style-bert-vits2":
            raise RuntimeError("Style-Bert-VITS2 synthesizer requires provider style-bert-vits2")
        if self.timeout_ms <= 0:
            raise RuntimeError("timeout_ms must be positive")
        normalized_text = request.text.strip()
        if not normalized_text:
            raise RuntimeError("speech text must be non-empty")
        provider_settings = _required_provider_settings(request.provider_settings)
        endpoint_url = _required_non_empty_string(provider_settings, "endpoint_url")
        request_query = urlencode(
            _request_query_parameters(
                text=normalized_text,
                provider_settings=provider_settings,
            ),
            doseq=False,
        )
        started_at = now_ms()
        response_mime_type, response_body = execute_http_request(
            provider_label="Style-Bert-VITS2",
            url=f"{join_base_url(endpoint_url, 'voice')}?{request_query}",
            method="POST",
            headers={
                "Accept": "audio/wav",
            },
            request_body=None,
            timeout_ms=self.timeout_ms,
        )
        require_audio_response(
            provider_label="Style-Bert-VITS2",
            response_mime_type=response_mime_type,
            response_body=response_body,
        )
        output_file = persist_audio_file(
            audio_output_dir=self.audio_output_dir,
            message_id=request.message_id,
            preferred_output_format="wav",
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
                "provider": "style-bert-vits2",
                "endpoint_url": endpoint_url,
                "audio_url": audio_url,
                "storage_path": str(output_file),
                "mime_type": response_mime_type,
                "byte_length": len(response_body),
            },
            adapter_trace_ref={
                "provider": "style-bert-vits2",
                "cycle_id": request.cycle_id,
                "message_id": request.message_id,
                "started_at": started_at,
                "finished_at": finished_at,
                "elapsed_ms": finished_at - started_at,
                "text_length": len(normalized_text),
                "model_name": _optional_string(provider_settings, "model_name"),
                "model_id": _required_integer(provider_settings, "model_id"),
                "speaker_name": _optional_string(provider_settings, "speaker_name"),
                "speaker_id": _required_integer(provider_settings, "speaker_id"),
                "style": _required_non_empty_string(provider_settings, "style"),
            },
        )


# Block: Request query build
def _request_query_parameters(
    *,
    text: str,
    provider_settings: dict[str, object],
) -> dict[str, str]:
    query_parameters = {
        "text": text,
        "model_id": str(_required_integer(provider_settings, "model_id")),
        "speaker_id": str(_required_integer(provider_settings, "speaker_id")),
        "sdp_ratio": str(_required_number(provider_settings, "sdp_ratio")),
        "noise": str(_required_number(provider_settings, "noise")),
        "noisew": str(_required_number(provider_settings, "noise_w")),
        "length": str(_required_number(provider_settings, "length")),
        "language": _required_non_empty_string(provider_settings, "language"),
        "auto_split": "true" if _required_boolean(provider_settings, "auto_split") else "false",
        "split_interval": str(_required_number(provider_settings, "split_interval")),
        "assist_text": _required_string(provider_settings, "assist_text"),
        "assist_text_weight": str(_required_number(provider_settings, "assist_text_weight")),
        "style": _required_non_empty_string(provider_settings, "style"),
        "style_weight": str(_required_number(provider_settings, "style_weight")),
    }
    model_name = _optional_string(provider_settings, "model_name")
    if model_name is not None:
        query_parameters["model_name"] = model_name
    speaker_name = _optional_string(provider_settings, "speaker_name")
    if speaker_name is not None:
        query_parameters["speaker_name"] = speaker_name
    return query_parameters


# Block: Provider settings validation
def _required_provider_settings(provider_settings: object) -> dict[str, object]:
    if not isinstance(provider_settings, dict):
        raise RuntimeError("Style-Bert-VITS2 provider_settings must be object")
    return provider_settings


def _required_non_empty_string(provider_settings: dict[str, object], key: str) -> str:
    value = provider_settings.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Style-Bert-VITS2 {key} must be non-empty string")
    return value.strip()


def _required_string(provider_settings: dict[str, object], key: str) -> str:
    value = provider_settings.get(key)
    if not isinstance(value, str):
        raise RuntimeError(f"Style-Bert-VITS2 {key} must be string")
    return value


def _optional_string(provider_settings: dict[str, object], key: str) -> str | None:
    value = provider_settings.get(key)
    if not isinstance(value, str):
        return None
    stripped_value = value.strip()
    if not stripped_value:
        return None
    return stripped_value


def _required_integer(provider_settings: dict[str, object], key: str) -> int:
    value = provider_settings.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"Style-Bert-VITS2 {key} must be integer")
    return value


def _required_number(provider_settings: dict[str, object], key: str) -> float:
    value = provider_settings.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"Style-Bert-VITS2 {key} must be number")
    return float(value)


def _required_boolean(provider_settings: dict[str, object], key: str) -> bool:
    value = provider_settings.get(key)
    if not isinstance(value, bool):
        raise RuntimeError(f"Style-Bert-VITS2 {key} must be boolean")
    return value
