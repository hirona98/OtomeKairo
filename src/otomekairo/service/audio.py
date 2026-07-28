from __future__ import annotations

from typing import Any

from otomekairo.event_stream import ServerWebSocket


class ServiceAudioMixin:
    def register_audio_stream_connection(
        self,
        websocket: ServerWebSocket,
        *,
        endpoint_source: str,
    ) -> str:
        return self._audio_runtime.register_connection(
            websocket,
            endpoint_source=endpoint_source,
        )

    def handle_audio_stream_message(
        self,
        session_id: str,
        message_kind: str,
        message_payload: bytes,
    ) -> None:
        self._audio_runtime.handle_message(
            session_id,
            message_kind,
            message_payload,
        )

    def send_audio_stream_error(
        self,
        session_id: str,
        *,
        code: str,
        message: str,
    ) -> None:
        self._audio_runtime.send_error(
            session_id,
            code=code,
            message=message,
        )

    def unregister_audio_stream_connection(self, session_id: str) -> None:
        self._audio_runtime.unregister_connection(session_id)

    def close_audio_runtime(self) -> None:
        self._audio_runtime.close()

    def list_audio_input_devices(self, token: str | None) -> dict[str, Any]:
        self._require_token(token)
        return self._audio_runtime.list_input_devices()

    def list_audio_speakers(self, token: str | None) -> dict[str, Any]:
        self._require_token(token)
        return self._audio_runtime.list_speakers()

    def start_audio_speaker_enrollment(
        self,
        token: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_token(token)
        return self._audio_runtime.start_enrollment(payload)

    def cancel_audio_speaker_enrollment(
        self,
        token: str | None,
        enrollment_id: str,
    ) -> dict[str, Any]:
        self._require_token(token)
        return self._audio_runtime.cancel_enrollment(
            enrollment_id,
            owner_client_id=None,
        )

    def rename_audio_speaker(
        self,
        token: str | None,
        person_ref: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_token(token)
        return self._audio_runtime.rename_speaker(person_ref, payload)

    def unregister_audio_speaker(
        self,
        token: str | None,
        person_ref: str,
    ) -> dict[str, Any]:
        self._require_token(token)
        return self._audio_runtime.unregister_speaker(person_ref)
