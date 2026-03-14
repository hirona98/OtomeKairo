"""Microphone input endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from otomekairo.gateway.speech_recognizer import SpeechRecognitionRequest
from otomekairo.web.dependencies import ApiError, AppServices


# Block: Router factory
def build_microphone_router(services: AppServices) -> APIRouter:
    router = APIRouter()

    # Block: Microphone transcription endpoint
    @router.post("/api/microphone/input", status_code=status.HTTP_202_ACCEPTED)
    async def post_microphone_input(request: Request) -> dict[str, object]:
        effective_settings = services.settings_store.read_settings(services.default_settings)["effective_settings"]
        recognition_request = await _build_recognition_request(
            request=request,
            effective_settings=effective_settings,
        )
        recognition_response = services.speech_recognizer.recognize(recognition_request)
        enqueue_result = services.cycle_commit_store.enqueue_microphone_message(
            transcript_text=recognition_response.transcript_text,
            stt_provider=recognition_response.provider,
            stt_language=recognition_response.language,
        )
        return {
            **enqueue_result,
            "transcript_text": recognition_response.transcript_text,
            "provider": recognition_response.provider,
            "language": recognition_response.language,
        }

    return router


# Block: Recognition request build
async def _build_recognition_request(
    *,
    request: Request,
    effective_settings: dict[str, object],
) -> SpeechRecognitionRequest:
    if not bool(effective_settings.get("sensors.microphone.enabled")):
        raise ApiError(
            status_code=409,
            error_code="microphone_unavailable",
            message="マイク入力が無効です",
        )
    if not bool(effective_settings.get("speech.stt.enabled")):
        raise ApiError(
            status_code=409,
            error_code="stt_unavailable",
            message="STT が無効です",
        )
    provider = _required_non_empty_setting(effective_settings, "speech.stt.provider")
    if provider != "amivoice":
        raise ApiError(
            status_code=400,
            error_code="invalid_request",
            message="speech.stt.provider が未対応です",
        )
    audio_bytes = await request.body()
    if not audio_bytes:
        raise ApiError(
            status_code=400,
            error_code="invalid_request",
            message="request body が空です",
        )
    audio_mime_type = _required_content_type(request=request)
    return SpeechRecognitionRequest(
        provider=provider,
        audio_bytes=audio_bytes,
        audio_mime_type=audio_mime_type,
        file_name=_microphone_file_name(audio_mime_type),
        provider_settings={
            "api_key": _required_non_empty_setting(effective_settings, "speech.stt.amivoice.api_key"),
            "profile_id": _required_setting_string(effective_settings, "speech.stt.amivoice.profile_id"),
            "language": _required_non_empty_setting(effective_settings, "speech.stt.language"),
        },
    )


# Block: Settings read helpers
def _required_setting_string(effective_settings: dict[str, object], key: str) -> str:
    value = effective_settings.get(key)
    if not isinstance(value, str):
        raise ApiError(
            status_code=500,
            error_code="internal_server_error",
            message=f"{key} must be string",
        )
    return value


def _required_non_empty_setting(effective_settings: dict[str, object], key: str) -> str:
    value = effective_settings.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ApiError(
            status_code=409,
            error_code="stt_unavailable",
            message=f"{key} が未設定です",
        )
    return value.strip()


# Block: Request metadata helpers
def _required_content_type(*, request: Request) -> str:
    content_type = request.headers.get("content-type", "").strip()
    if not content_type:
        raise ApiError(
            status_code=400,
            error_code="invalid_request",
            message="Content-Type が必要です",
        )
    return content_type


def _microphone_file_name(audio_mime_type: str) -> str:
    normalized_audio_mime_type = audio_mime_type.lower()
    if "ogg" in normalized_audio_mime_type:
        return "microphone_input.ogg"
    return "microphone_input.webm"
