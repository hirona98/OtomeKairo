"""VOICEVOX backed speech synthesizer."""

from __future__ import annotations

import json
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
    decode_json_response,
    execute_http_request,
    join_base_url,
    now_ms,
    persist_audio_file,
    require_audio_response,
)


# Block: VOICEVOX adapter
@dataclass(frozen=True, slots=True)
class VoicevoxSpeechSynthesizer(SpeechSynthesizer):
    audio_output_dir: Path
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    # Block: Speech synthesis
    def synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResponse:
        if request.provider != "voicevox":
            raise RuntimeError("VOICEVOX synthesizer requires provider voicevox")
        if self.timeout_ms <= 0:
            raise RuntimeError("timeout_ms must be positive")
        normalized_text = request.text.strip()
        if not normalized_text:
            raise RuntimeError("speech text must be non-empty")
        provider_settings = _required_provider_settings(request.provider_settings)
        speaker_id = _required_integer(provider_settings, "speaker_id")
        endpoint_url = _required_non_empty_string(provider_settings, "endpoint_url")
        started_at = now_ms()
        audio_query = _create_audio_query(
            endpoint_url=endpoint_url,
            text=normalized_text,
            speaker_id=speaker_id,
            timeout_ms=self.timeout_ms,
        )
        _apply_audio_query_settings(
            audio_query=audio_query,
            provider_settings=provider_settings,
        )
        response_mime_type, response_body = _synthesize_audio(
            endpoint_url=endpoint_url,
            speaker_id=speaker_id,
            audio_query=audio_query,
            timeout_ms=self.timeout_ms,
        )
        require_audio_response(
            provider_label="VOICEVOX",
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
                "provider": "voicevox",
                "endpoint_url": endpoint_url,
                "audio_url": audio_url,
                "storage_path": str(output_file),
                "mime_type": response_mime_type,
                "byte_length": len(response_body),
            },
            adapter_trace_ref={
                "provider": "voicevox",
                "cycle_id": request.cycle_id,
                "message_id": request.message_id,
                "started_at": started_at,
                "finished_at": finished_at,
                "elapsed_ms": finished_at - started_at,
                "speaker_id": speaker_id,
                "text_length": len(normalized_text),
            },
        )


# Block: Audio query creation
def _create_audio_query(
    *,
    endpoint_url: str,
    text: str,
    speaker_id: int,
    timeout_ms: int,
) -> dict[str, object]:
    request_query = urlencode(
        {
            "text": text,
            "speaker": str(speaker_id),
        }
    )
    response_mime_type, response_body = execute_http_request(
        provider_label="VOICEVOX",
        url=f"{join_base_url(endpoint_url, 'audio_query')}?{request_query}",
        method="POST",
        headers={
            "Accept": "application/json",
        },
        request_body=b"",
        timeout_ms=timeout_ms,
    )
    if response_mime_type != "application/json":
        raise RuntimeError("VOICEVOX audio_query response must be JSON")
    return decode_json_response(
        provider_label="VOICEVOX audio_query",
        response_body=response_body,
    )


# Block: Audio query settings
def _apply_audio_query_settings(
    *,
    audio_query: dict[str, object],
    provider_settings: dict[str, object],
) -> None:
    audio_query["speedScale"] = _required_number(provider_settings, "speed_scale")
    audio_query["pitchScale"] = _required_number(provider_settings, "pitch_scale")
    audio_query["intonationScale"] = _required_number(provider_settings, "intonation_scale")
    audio_query["volumeScale"] = _required_number(provider_settings, "volume_scale")
    audio_query["prePhonemeLength"] = _required_number(provider_settings, "pre_phoneme_length")
    audio_query["postPhonemeLength"] = _required_number(provider_settings, "post_phoneme_length")
    audio_query["outputSamplingRate"] = _required_integer(provider_settings, "output_sampling_rate")
    audio_query["outputStereo"] = _required_boolean(provider_settings, "output_stereo")


# Block: Audio synthesis
def _synthesize_audio(
    *,
    endpoint_url: str,
    speaker_id: int,
    audio_query: dict[str, object],
    timeout_ms: int,
) -> tuple[str, bytes]:
    request_query = urlencode(
        {
            "speaker": str(speaker_id),
        }
    )
    return execute_http_request(
        provider_label="VOICEVOX",
        url=f"{join_base_url(endpoint_url, 'synthesis')}?{request_query}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "audio/wav",
        },
        request_body=json.dumps(audio_query, ensure_ascii=False).encode("utf-8"),
        timeout_ms=timeout_ms,
    )


# Block: Provider settings validation
def _required_provider_settings(provider_settings: object) -> dict[str, object]:
    if not isinstance(provider_settings, dict):
        raise RuntimeError("VOICEVOX provider_settings must be object")
    return provider_settings


def _required_non_empty_string(provider_settings: dict[str, object], key: str) -> str:
    value = provider_settings.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"VOICEVOX {key} must be non-empty string")
    return value.strip()


def _required_integer(provider_settings: dict[str, object], key: str) -> int:
    value = provider_settings.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"VOICEVOX {key} must be integer")
    return value


def _required_number(provider_settings: dict[str, object], key: str) -> float:
    value = provider_settings.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"VOICEVOX {key} must be number")
    return float(value)


def _required_boolean(provider_settings: dict[str, object], key: str) -> bool:
    value = provider_settings.get(key)
    if not isinstance(value, bool):
        raise RuntimeError(f"VOICEVOX {key} must be boolean")
    return value
