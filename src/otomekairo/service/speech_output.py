from __future__ import annotations

from typing import Any
import uuid

from otomekairo.tts import TtsDeliveryReservation


class ServiceSpeechOutputMixin:
    _AMBIENT_SPEECH_SOURCE_KINDS = {"wake", "background_thinking"}

    def _broadcast_system_notice(self, notice: dict[str, Any] | None) -> bool:
        if not isinstance(notice, dict):
            return False
        for field_name in ("source_kind", "code", "message"):
            value = notice.get(field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"system_notice.{field_name} is required.")
        event = {
            "event_id": self._next_stream_event_id(),
            "type": "system_notice",
            "data": {
                "notice_id": f"system_notice:{uuid.uuid4().hex}",
                "created_at": self._now_iso(),
                **notice,
            },
        }
        return self._event_stream_registry.send_to_subscribers("system_notice", event) > 0

    def _reserve_speech_audio(
        self,
        *,
        cycle_id: str,
        source_kind: str,
        interaction_ref: str | None,
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
        persona_id = speech.get("persona_id")
        persona_display_name = speech.get("persona_display_name")
        interaction_ref, recipient_person_refs = self._speech_delivery_context(
            source_kind=source_kind,
            interaction_ref=response.get("interaction_ref"),
            recipient_person_refs=response.get("recipient_person_refs"),
        )
        reservation = self._reserve_speech_audio(
            cycle_id=str(response.get("cycle_id") or ""),
            source_kind=source_kind,
            interaction_ref=interaction_ref,
            recipient_person_refs=recipient_person_refs,
            speech_text=speech["text"],
        )
        speech["audio_delivery"] = dict(reservation.summary)
        self._broadcast_assistant_message(
            event_data={
                "cycle_id": response.get("cycle_id"),
                "source_kind": source_kind,
                "persona_id": persona_id,
                "persona_display_name": persona_display_name,
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
        source_kind = str(event_data.get("source_kind") or "")
        interaction_ref, recipient_person_refs = self._speech_delivery_context(
            source_kind=source_kind,
            interaction_ref=event_data.get("interaction_ref"),
            recipient_person_refs=event_data.get("recipient_person_refs"),
        )
        event_data = {
            **event_data,
            "interaction_ref": interaction_ref,
            "recipient_person_refs": recipient_person_refs,
        }
        reservation = self._reserve_speech_audio(
            cycle_id=cycle_id,
            source_kind=source_kind,
            interaction_ref=interaction_ref,
            recipient_person_refs=recipient_person_refs,
            speech_text=speech_text,
        )
        sent = self._broadcast_assistant_message(
            event_data=event_data,
            speech_text=speech_text,
            reservation=reservation,
        )
        return sent, dict(reservation.summary)

    def _speech_delivery_context(
        self,
        *,
        source_kind: str,
        interaction_ref: Any,
        recipient_person_refs: Any,
    ) -> tuple[str | None, list[str]]:
        if interaction_ref is None:
            if source_kind not in self._AMBIENT_SPEECH_SOURCE_KINDS:
                raise ValueError("assistant_message.interaction_ref is required for directed speech.")
            if recipient_person_refs != []:
                raise ValueError("Ambient assistant_message must use an empty recipient_person_refs array.")
            return None, []

        if not isinstance(interaction_ref, str) or not interaction_ref.strip():
            raise ValueError("assistant_message.interaction_ref must be a non-empty string or null.")
        if not isinstance(recipient_person_refs, list) or not recipient_person_refs:
            raise ValueError("Directed assistant_message.recipient_person_refs must be a non-empty array.")
        if any(not isinstance(item, str) or not item.strip() for item in recipient_person_refs):
            raise ValueError("assistant_message.recipient_person_refs must contain non-empty strings.")
        return interaction_ref, list(recipient_person_refs)

    def _broadcast_assistant_message(
        self,
        *,
        event_data: dict[str, Any],
        speech_text: str,
        reservation: TtsDeliveryReservation,
    ) -> bool:
        # UI は現在設定ではなく、この発話で使った人格を表示する。
        for field_name in ("persona_id", "persona_display_name"):
            field_value = event_data.get(field_name)
            if not isinstance(field_value, str) or not field_value.strip():
                raise ValueError(f"assistant_message.{field_name} is required.")
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
