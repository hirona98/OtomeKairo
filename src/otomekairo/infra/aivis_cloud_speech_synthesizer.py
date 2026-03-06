"""Aivis Cloud backed speech synthesizer."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from otomekairo.gateway.speech_synthesizer import (
    SpeechSynthesisRequest,
    SpeechSynthesisResponse,
    SpeechSynthesizer,
)


# Block: Adapter constants
DEFAULT_TIMEOUT_MS = 20_000
DEFAULT_USER_AGENT = "OtomeKairo/1.0"


# Block: Aivis cloud adapter
@dataclass(frozen=True, slots=True)
class AivisCloudSpeechSynthesizer(SpeechSynthesizer):
    audio_output_dir: Path
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    # Block: Speech synthesis
    def synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResponse:
        if self.timeout_ms <= 0:
            raise RuntimeError("timeout_ms must be positive")
        normalized_text = request.text.strip()
        if not normalized_text:
            raise RuntimeError("speech text must be non-empty")
        started_at = _now_ms()
        payload = _build_request_payload(request=request, normalized_text=normalized_text)
        response_mime_type, response_body = _post_synthesis_request(
            endpoint_url=request.endpoint_url,
            api_key=request.api_key,
            payload=payload,
            timeout_ms=self.timeout_ms,
        )
        output_file = _persist_audio_file(
            audio_output_dir=self.audio_output_dir,
            message_id=request.message_id,
            output_format=request.output_format,
            response_mime_type=response_mime_type,
            response_body=response_body,
        )
        finished_at = _now_ms()
        audio_url = f"/audio/{output_file.name}"
        return SpeechSynthesisResponse(
            audio_url=audio_url,
            storage_path=str(output_file),
            mime_type=response_mime_type,
            byte_length=len(response_body),
            raw_result_ref={
                "provider": "aivis_cloud",
                "endpoint_url": request.endpoint_url,
                "audio_url": audio_url,
                "storage_path": str(output_file),
                "mime_type": response_mime_type,
                "byte_length": len(response_body),
            },
            adapter_trace_ref={
                "provider": "aivis_cloud",
                "cycle_id": request.cycle_id,
                "message_id": request.message_id,
                "started_at": started_at,
                "finished_at": finished_at,
                "elapsed_ms": finished_at - started_at,
                "model_uuid": request.model_uuid,
                "speaker_uuid": request.speaker_uuid,
                "style_id": request.style_id,
                "text_length": len(normalized_text),
                "output_format": request.output_format,
            },
        )


# Block: Request payload build
def _build_request_payload(
    *,
    request: SpeechSynthesisRequest,
    normalized_text: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model_uuid": request.model_uuid,
        "speaker_uuid": request.speaker_uuid,
        "style_id": request.style_id,
        "text": normalized_text,
        "use_ssml": request.use_ssml,
        "language": request.language,
        "speaking_rate": request.speaking_rate,
        "emotional_intensity": request.emotional_intensity,
        "tempo_dynamics": request.tempo_dynamics,
        "pitch": request.pitch,
        "volume": request.volume,
        "output_format": request.output_format,
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
    http_request = Request(
        url=endpoint_url,
        data=request_body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "audio/*",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urlopen(http_request, timeout=timeout_ms / 1000.0) as response:
            response_body = response.read()
            response_mime_type = _normalized_content_type(response.headers.get("Content-Type"))
    except HTTPError as error:
        error_body = error.read()
        error_message = _http_error_message(error_body)
        raise RuntimeError(f"Aivis Cloud request failed: {error.code} {error_message}") from error
    except URLError as error:
        raise RuntimeError(f"Aivis Cloud request failed: {error.reason}") from error
    if not response_mime_type.startswith("audio/"):
        raise RuntimeError("Aivis Cloud response must be audio")
    if not response_body:
        raise RuntimeError("Aivis Cloud response body is empty")
    return response_mime_type, response_body


# Block: Audio file persistence
def _persist_audio_file(
    *,
    audio_output_dir: Path,
    message_id: str,
    output_format: str,
    response_mime_type: str,
    response_body: bytes,
) -> Path:
    audio_output_dir.mkdir(parents=True, exist_ok=True)
    file_extension = _audio_file_extension(
        output_format=output_format,
        response_mime_type=response_mime_type,
    )
    safe_message_id = _safe_file_token(message_id)
    file_name = f"tts_{safe_message_id}_{_now_ms()}.{file_extension}"
    output_path = audio_output_dir / file_name
    output_path.write_bytes(response_body)
    return output_path


# Block: Content type normalization
def _normalized_content_type(content_type: str | None) -> str:
    if not isinstance(content_type, str):
        return ""
    return content_type.split(";", 1)[0].strip().lower()


# Block: Error message extraction
def _http_error_message(error_body: bytes) -> str:
    if not error_body:
        return "no response body"
    try:
        parsed = json.loads(error_body.decode("utf-8"))
    except Exception:
        return error_body.decode("utf-8", errors="replace").strip()[:240]
    if not isinstance(parsed, dict):
        return str(parsed)[:240]
    for key in ("message", "error", "detail"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:240]
    return json.dumps(parsed, ensure_ascii=False)[:240]


# Block: File extension resolution
def _audio_file_extension(*, output_format: str, response_mime_type: str) -> str:
    mime_extension = _audio_extension_from_content_type(response_mime_type)
    if mime_extension is not None:
        return mime_extension
    normalized_format = output_format.strip().lower()
    if not normalized_format:
        raise RuntimeError("output_format must be non-empty")
    return _normalized_audio_extension(normalized_format)


# Block: Mime type extension resolution
def _audio_extension_from_content_type(content_type: str) -> str | None:
    normalized_content_type = content_type.strip().lower()
    if normalized_content_type in {"audio/wav", "audio/x-wav", "audio/wave"}:
        return "wav"
    if normalized_content_type in {"audio/mpeg", "audio/mp3"}:
        return "mp3"
    if normalized_content_type in {"audio/ogg"}:
        return "ogg"
    if normalized_content_type in {"audio/aac"}:
        return "aac"
    if normalized_content_type in {"audio/flac"}:
        return "flac"
    return None


def _normalized_audio_extension(value: str) -> str:
    if value in {"wav", "x-wav", "wave"}:
        return "wav"
    if value in {"mpeg", "mp3"}:
        return "mp3"
    if value in {"ogg", "oga"}:
        return "ogg"
    if value in {"aac"}:
        return "aac"
    if value in {"flac"}:
        return "flac"
    raise RuntimeError("output_format is unsupported")


# Block: File token helper
def _safe_file_token(value: str) -> str:
    safe_chars = [
        character
        for character in value
        if character.isascii() and (character.isalnum() or character in {"_", "-"})
    ]
    if not safe_chars:
        return "message"
    return "".join(safe_chars)


# Block: Default audio path
def default_tts_audio_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "audio"


# Block: Time helper
def _now_ms() -> int:
    return int(time.time() * 1000)
