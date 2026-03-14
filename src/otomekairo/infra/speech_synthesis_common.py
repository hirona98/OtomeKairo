"""Common helpers for remote speech synthesis adapters."""

from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pathlib import Path

from otomekairo.schema.storage_paths import default_tts_audio_dir

# Block: Shared adapter constants
DEFAULT_TIMEOUT_MS = 20_000
DEFAULT_USER_AGENT = "OtomeKairo/1.0"


# Block: HTTP request execution
def execute_http_request(
    *,
    provider_label: str,
    url: str,
    method: str,
    headers: dict[str, str],
    request_body: bytes | None,
    timeout_ms: int,
) -> tuple[str, bytes]:
    normalized_headers = dict(headers)
    normalized_headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
    http_request = Request(
        url=url,
        data=request_body,
        headers=normalized_headers,
        method=method,
    )
    try:
        with urlopen(http_request, timeout=timeout_ms / 1000.0) as response:
            response_body = response.read()
            response_mime_type = normalized_content_type(response.headers.get("Content-Type"))
    except HTTPError as error:
        error_body = error.read()
        error_message = http_error_message(error_body)
        raise RuntimeError(f"{provider_label} request failed: {error.code} {error_message}") from error
    except URLError as error:
        raise RuntimeError(f"{provider_label} request failed: {error.reason}") from error
    return response_mime_type, response_body


# Block: JSON response decode
def decode_json_response(*, provider_label: str, response_body: bytes) -> dict[str, object]:
    if not response_body:
        raise RuntimeError(f"{provider_label} response body is empty")
    try:
        parsed = json.loads(response_body.decode("utf-8"))
    except Exception as error:
        raise RuntimeError(f"{provider_label} response must be valid JSON") from error
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{provider_label} response must be JSON object")
    return parsed


# Block: Audio response validation
def require_audio_response(
    *,
    provider_label: str,
    response_mime_type: str,
    response_body: bytes,
) -> None:
    if not response_mime_type.startswith("audio/"):
        raise RuntimeError(f"{provider_label} response must be audio")
    if not response_body:
        raise RuntimeError(f"{provider_label} response body is empty")


# Block: Audio file persistence
def persist_audio_file(
    *,
    audio_output_dir: Path,
    message_id: str,
    preferred_output_format: str | None,
    response_mime_type: str,
    response_body: bytes,
) -> Path:
    audio_output_dir.mkdir(parents=True, exist_ok=True)
    file_extension = audio_file_extension(
        preferred_output_format=preferred_output_format,
        response_mime_type=response_mime_type,
    )
    safe_message_id = safe_file_token(message_id)
    file_name = f"tts_{safe_message_id}_{now_ms()}.{file_extension}"
    output_path = audio_output_dir / file_name
    output_path.write_bytes(response_body)
    return output_path


# Block: File extension resolution
def audio_file_extension(*, preferred_output_format: str | None, response_mime_type: str) -> str:
    mime_extension = audio_extension_from_content_type(response_mime_type)
    if mime_extension is not None:
        return mime_extension
    if preferred_output_format is None:
        raise RuntimeError("audio output format must be known")
    normalized_format = preferred_output_format.strip().lower()
    if not normalized_format:
        raise RuntimeError("preferred_output_format must be non-empty")
    return normalized_audio_extension(normalized_format)


def audio_extension_from_content_type(content_type: str) -> str | None:
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


def normalized_audio_extension(value: str) -> str:
    if value in {"wav", "x-wav", "wave"}:
        return "wav"
    if value in {"mpeg", "mp3"}:
        return "mp3"
    if value in {"ogg", "oga"}:
        return "ogg"
    if value == "aac":
        return "aac"
    if value == "flac":
        return "flac"
    raise RuntimeError("audio output format is unsupported")


# Block: Content type normalization
def normalized_content_type(content_type: str | None) -> str:
    if not isinstance(content_type, str):
        return ""
    return content_type.split(";", 1)[0].strip().lower()


# Block: Error message extraction
def http_error_message(error_body: bytes) -> str:
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


# Block: URL helpers
def join_base_url(base_url: str, path: str) -> str:
    normalized_base_url = base_url.strip()
    normalized_path = path.strip()
    if not normalized_base_url:
        raise RuntimeError("base_url must be non-empty")
    if not normalized_path:
        raise RuntimeError("path must be non-empty")
    return f"{normalized_base_url.rstrip('/')}/{normalized_path.lstrip('/')}"


# Block: File token helper
def safe_file_token(value: str) -> str:
    safe_chars = [
        character
        for character in value
        if character.isascii() and (character.isalnum() or character in {"_", "-"})
    ]
    if not safe_chars:
        return "message"
    return "".join(safe_chars)


# Block: Time helper
def now_ms() -> int:
    return int(time.time() * 1000)
