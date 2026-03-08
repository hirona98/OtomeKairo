"""AmiVoice backed speech recognizer."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from otomekairo.gateway.speech_recognizer import (
    SpeechRecognitionRequest,
    SpeechRecognitionResponse,
    SpeechRecognizer,
)
from otomekairo.infra.speech_synthesis_common import DEFAULT_TIMEOUT_MS, decode_json_response, execute_http_request, now_ms


# Block: AmiVoice endpoint
AMIVOICE_NOLOG_ENDPOINT_URL = "https://acp-api.amivoice.com/v1/nolog/recognize"


# Block: AmiVoice adapter
@dataclass(frozen=True, slots=True)
class AmivoiceSpeechRecognizer(SpeechRecognizer):
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    # Block: Speech recognition
    def recognize(self, request: SpeechRecognitionRequest) -> SpeechRecognitionResponse:
        if request.provider != "amivoice":
            raise RuntimeError("AmiVoice recognizer requires provider amivoice")
        if self.timeout_ms <= 0:
            raise RuntimeError("timeout_ms must be positive")
        if not request.audio_bytes:
            raise RuntimeError("audio_bytes must be non-empty")
        provider_settings = _required_provider_settings(request.provider_settings)
        started_at = now_ms()
        boundary = f"otomekairo_{uuid.uuid4().hex}"
        request_body = _build_multipart_body(
            boundary=boundary,
            api_key=_required_non_empty_string(provider_settings, "api_key"),
            recognition_profile=_recognition_profile(provider_settings),
            audio_mime_type=_required_audio_mime_type(request.audio_mime_type),
            file_name=_required_file_name(request.file_name),
            audio_bytes=request.audio_bytes,
        )
        response_mime_type, response_body = execute_http_request(
            provider_label="AmiVoice",
            url=AMIVOICE_NOLOG_ENDPOINT_URL,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
            },
            request_body=request_body,
            timeout_ms=self.timeout_ms,
        )
        if response_mime_type != "application/json":
            raise RuntimeError("AmiVoice response must be application/json")
        parsed_response = decode_json_response(
            provider_label="AmiVoice",
            response_body=response_body,
        )
        transcript_text = _recognized_text(parsed_response)
        finished_at = now_ms()
        return SpeechRecognitionResponse(
            transcript_text=transcript_text,
            provider="amivoice",
            language=_required_non_empty_string(provider_settings, "language"),
            raw_result_ref=parsed_response,
            adapter_trace_ref={
                "provider": "amivoice",
                "started_at": started_at,
                "finished_at": finished_at,
                "elapsed_ms": finished_at - started_at,
                "audio_mime_type": request.audio_mime_type,
                "file_name": request.file_name,
                "profile_id": provider_settings["profile_id"],
            },
        )


# Block: Multipart request build
def _build_multipart_body(
    *,
    boundary: str,
    api_key: str,
    recognition_profile: str,
    audio_mime_type: str,
    file_name: str,
    audio_bytes: bytes,
) -> bytes:
    boundary_line = f"--{boundary}\r\n".encode("utf-8")
    parts: list[bytes] = []
    parts.append(boundary_line)
    parts.append(_text_part(name="u", value=api_key))
    parts.append(boundary_line)
    parts.append(_text_part(name="d", value=recognition_profile))
    parts.append(boundary_line)
    parts.append(
        _file_part(
            name="a",
            file_name=file_name,
            content_type=audio_mime_type,
            payload=audio_bytes,
        )
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts)


def _text_part(*, name: str, value: str) -> bytes:
    return (
        f'Content-Disposition: form-data; name="{name}"\r\n'
        "\r\n"
        f"{value}\r\n"
    ).encode("utf-8")


def _file_part(
    *,
    name: str,
    file_name: str,
    content_type: str,
    payload: bytes,
) -> bytes:
    return (
        f'Content-Disposition: form-data; name="{name}"; filename="{file_name}"\r\n'
        f"Content-Type: {content_type}\r\n"
        "\r\n"
    ).encode("utf-8") + payload + b"\r\n"


# Block: Provider settings validation
def _required_provider_settings(provider_settings: object) -> dict[str, object]:
    if not isinstance(provider_settings, dict):
        raise RuntimeError("AmiVoice provider_settings must be object")
    return provider_settings


def _required_non_empty_string(provider_settings: dict[str, object], key: str) -> str:
    value = provider_settings.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"AmiVoice {key} must be non-empty string")
    return value.strip()


def _recognition_profile(provider_settings: dict[str, object]) -> str:
    language = _required_non_empty_string(provider_settings, "language")
    if language not in {"ja", "ja-JP"}:
        raise RuntimeError("AmiVoice language must be ja or ja-JP")
    profile_parts = ["grammarFileNames=-a-general"]
    profile_id = provider_settings.get("profile_id")
    if isinstance(profile_id, str) and profile_id.strip():
        profile_parts.append(f"profileId=:{profile_id.strip()}")
    return " ".join(profile_parts)


def _required_audio_mime_type(audio_mime_type: str) -> str:
    normalized_audio_mime_type = audio_mime_type.strip().lower()
    if not normalized_audio_mime_type:
        raise RuntimeError("audio_mime_type must be non-empty string")
    return normalized_audio_mime_type


def _required_file_name(file_name: str) -> str:
    normalized_file_name = file_name.strip()
    if not normalized_file_name:
        raise RuntimeError("file_name must be non-empty string")
    return normalized_file_name


# Block: Response validation
def _recognized_text(parsed_response: dict[str, object]) -> str:
    response_code = parsed_response.get("code")
    if isinstance(response_code, str) and response_code:
        response_message = parsed_response.get("message")
        if isinstance(response_message, str) and response_message.strip():
            raise RuntimeError(f"AmiVoice recognition failed: {response_code} {response_message.strip()}")
        raise RuntimeError(f"AmiVoice recognition failed: {response_code}")
    transcript_text = parsed_response.get("text")
    if not isinstance(transcript_text, str) or not transcript_text.strip():
        raise RuntimeError("AmiVoice recognition returned empty text")
    return transcript_text.strip()
