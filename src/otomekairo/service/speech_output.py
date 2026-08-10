from __future__ import annotations

from typing import Any
import uuid

from otomekairo.tts import TtsDeliveryReservation


class ServiceSpeechOutputMixin:
    def _reserve_speech_audio(
        self,
        *,
        cycle_id: str,
        source_kind: str,
        interaction_ref: str,
        recipient_person_refs: list[str],
        speech_text: str,
    ) -> TtsDeliveryReservation:
        return self._tts_runtime.reserve(
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
        self._broadcast_assistant_message(
            event_data={
                "cycle_id": response.get("cycle_id"),
                "source_kind": source_kind,
                "interaction_ref": interaction_ref,
                "recipient_person_refs": recipient_person_refs,
            },
            speech_text=speech["text"],
            reservation=reservation,
        )
        return reservation

    def _emit_assistant_message_with_audio(
        self,
        *,
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
        sent = self._broadcast_assistant_message(
            event_data=event_data,
            speech_text=speech_text,
            reservation=reservation,
        )
        return sent, dict(reservation.summary)

    def _broadcast_assistant_message(
        self,
        *,
        event_data: dict[str, Any],
        speech_text: str,
        reservation: TtsDeliveryReservation,
    ) -> bool:
        event = {
            "event_id": self._next_stream_event_id(),
            "type": "assistant_message",
            "data": {
                "message_id": f"chat_message:{uuid.uuid4().hex}",
                "created_at": self._now_iso(),
                **event_data,
                "message": speech_text,
                "audio_delivery": dict(reservation.summary),
            },
        }
        sent_count = self._event_stream_registry.send_to_subscribers(
            "assistant_message",
            event,
        )
        # 表示購読者の有無と音声出力先を分離する。
        self._tts_runtime.activate(reservation)
        return sent_count > 0

    def close_tts_runtime(self) -> None:
        self._tts_runtime.close()
