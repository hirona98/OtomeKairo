from __future__ import annotations

from typing import Any

from otomekairo.tts import TtsDeliveryReservation


class ServiceSpeechOutputMixin:
    def _reserve_speech_audio(
        self,
        *,
        target_client_id: str | None,
        cycle_id: str,
        source_kind: str,
        interaction_ref: str,
        recipient_person_refs: list[str],
        speech_text: str,
    ) -> TtsDeliveryReservation:
        return self._tts_runtime.reserve(
            target_client_id=target_client_id,
            cycle_id=cycle_id,
            source_kind=source_kind,
            interaction_ref=interaction_ref,
            recipient_person_refs=recipient_person_refs,
            speech_text=speech_text,
        )

    def _attach_response_audio_delivery(
        self,
        response: dict[str, Any],
        *,
        target_client_id: str | None,
        source_kind: str,
    ) -> TtsDeliveryReservation | None:
        speech = response.get("speech")
        if not isinstance(speech, dict) or not isinstance(speech.get("text"), str):
            return None
        interaction_ref = response.get("interaction_ref")
        recipient_person_refs = response.get("recipient_person_refs")
        if not isinstance(interaction_ref, str):
            interaction_ref = ""
        if not isinstance(recipient_person_refs, list):
            recipient_person_refs = []
        reservation = self._reserve_speech_audio(
            target_client_id=target_client_id,
            cycle_id=str(response.get("cycle_id") or ""),
            source_kind=source_kind,
            interaction_ref=interaction_ref,
            recipient_person_refs=[
                item
                for item in recipient_person_refs
                if isinstance(item, str) and item
            ],
            speech_text=speech["text"],
        )
        speech["audio_delivery"] = dict(reservation.summary)
        return reservation

    def _emit_assistant_message_with_audio(
        self,
        *,
        target_client_id: str,
        event_data: dict[str, Any],
        speech_text: str,
    ) -> tuple[bool, dict[str, Any]]:
        cycle_id = str(event_data.get("cycle_id") or "")
        interaction_ref = str(event_data.get("interaction_ref") or "")
        recipient_person_refs = event_data.get("recipient_person_refs")
        if not isinstance(recipient_person_refs, list):
            recipient_person_refs = []
        source_kind = str(event_data.get("source_kind") or "")
        reservation = self._reserve_speech_audio(
            target_client_id=target_client_id,
            cycle_id=cycle_id,
            source_kind=source_kind,
            interaction_ref=interaction_ref,
            recipient_person_refs=[
                item
                for item in recipient_person_refs
                if isinstance(item, str) and item
            ],
            speech_text=speech_text,
        )
        event = {
            "event_id": self._next_stream_event_id(),
            "type": "assistant_message",
            "data": {
                **event_data,
                "message": speech_text,
                "audio_delivery": dict(reservation.summary),
            },
        }
        sent = self._event_stream_registry.send_to_client(
            target_client_id,
            event,
        )
        if sent:
            self._tts_runtime.activate(reservation)
        else:
            self._tts_runtime.cancel(reservation)
        return sent, dict(reservation.summary)

    def close_tts_runtime(self) -> None:
        self._tts_runtime.close()
