from __future__ import annotations

import unittest

from otomekairo.interaction import InteractionContext, ParticipantContext
from otomekairo.service.speech_output import ServiceSpeechOutputMixin
from otomekairo.service.spontaneous.wake import ServiceSpontaneousWakeMixin
from otomekairo.tts import TtsDeliveryReservation


class _Registry:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def send_to_subscribers(self, event_type: str, event: dict) -> int:
        self.events.append(event)
        return 1


class _TtsRuntime:
    def __init__(self) -> None:
        self.reservations: list[dict] = []
        self.activated: list[TtsDeliveryReservation] = []

    def reserve(self, **kwargs) -> TtsDeliveryReservation:
        self.reservations.append(kwargs)
        return TtsDeliveryReservation(
            summary={
                "delivery_id": None,
                "status": "disabled",
                "error_code": None,
            },
            delivery=None,
        )

    def activate(self, reservation: TtsDeliveryReservation) -> None:
        self.activated.append(reservation)


class _Service(ServiceSpontaneousWakeMixin, ServiceSpeechOutputMixin):
    def __init__(self) -> None:
        self._event_stream_registry = _Registry()
        self._tts_runtime = _TtsRuntime()
        self._event_id = 0

    def _next_stream_event_id(self) -> int:
        self._event_id += 1
        return self._event_id

    def _now_iso(self) -> str:
        return "2026-08-12T21:17:31+09:00"

    def _short_cycle_id(self, cycle_id: str) -> str:
        return cycle_id.removeprefix("cycle:")[:12]


class SpontaneousSpeechDeliveryTests(unittest.TestCase):
    def _pipeline(self) -> dict:
        return {
            "persona_id": "persona:test",
            "persona_display_name": "テスト人格",
            "speech_payload": {"speech_text": "周囲への独り言。"},
        }

    def test_background_thinking_speech_is_delivered_without_interaction(self) -> None:
        service = _Service()

        audio_delivery = service._emit_wake_assistant_message_event(
            cycle_id="cycle:test",
            trigger_kind="background_thinking",
            client_context={},
            interaction_context=None,
            pipeline=self._pipeline(),
        )

        self.assertEqual(audio_delivery["status"], "disabled")
        self.assertEqual(len(service._event_stream_registry.events), 1)
        event = service._event_stream_registry.events[0]
        self.assertEqual(event["type"], "assistant_message")
        self.assertIsNone(event["data"]["interaction_ref"])
        self.assertEqual(event["data"]["recipient_person_refs"], [])
        self.assertIsNone(service._tts_runtime.reservations[0]["interaction_ref"])
        self.assertEqual(service._tts_runtime.reservations[0]["recipient_person_refs"], [])
        self.assertEqual(len(service._tts_runtime.activated), 1)

    def test_wake_speech_without_interaction_uses_ambient_delivery(self) -> None:
        service = _Service()

        service._emit_wake_assistant_message_event(
            cycle_id="cycle:test",
            trigger_kind="wake",
            client_context={},
            interaction_context=None,
            pipeline=self._pipeline(),
        )

        data = service._event_stream_registry.events[0]["data"]
        self.assertIsNone(data["interaction_ref"])
        self.assertEqual(data["recipient_person_refs"], [])

    def test_directed_wake_speech_preserves_interaction(self) -> None:
        service = _Service()
        interaction = InteractionContext(
            interaction_ref="interaction:test",
            speaker_ref=None,
            participants=(
                ParticipantContext(person_ref="person:test", display_name="テストさん"),
            ),
        )

        service._emit_wake_assistant_message_event(
            cycle_id="cycle:test",
            trigger_kind="wake",
            client_context={},
            interaction_context=interaction,
            pipeline=self._pipeline(),
        )

        data = service._event_stream_registry.events[0]["data"]
        self.assertEqual(data["interaction_ref"], "interaction:test")
        self.assertEqual(data["recipient_person_refs"], ["person:test"])

    def test_wake_without_speech_does_not_emit_or_reserve_audio(self) -> None:
        service = _Service()

        result = service._emit_wake_assistant_message_event(
            cycle_id="cycle:test",
            trigger_kind="background_thinking",
            client_context={},
            interaction_context=None,
            pipeline={"speech_payload": None},
        )

        self.assertIsNone(result)
        self.assertEqual(service._event_stream_registry.events, [])
        self.assertEqual(service._tts_runtime.reservations, [])

    def test_ambient_delivery_is_rejected_for_directed_source_kinds(self) -> None:
        service = _Service()

        for source_kind in ("conversation", "capability_result", "autonomous_run"):
            with self.subTest(source_kind=source_kind):
                with self.assertRaises(ValueError):
                    service._speech_delivery_context(
                        source_kind=source_kind,
                        interaction_ref=None,
                        recipient_person_refs=[],
                    )

    def test_partial_delivery_context_is_rejected(self) -> None:
        service = _Service()

        invalid_contexts = (
            ("wake", None, ["person:test"]),
            ("wake", "interaction:test", []),
            ("conversation", "interaction:test", []),
            ("conversation", "interaction:test", [""]),
        )
        for source_kind, interaction_ref, recipient_person_refs in invalid_contexts:
            with self.subTest(
                source_kind=source_kind,
                interaction_ref=interaction_ref,
                recipient_person_refs=recipient_person_refs,
            ):
                with self.assertRaises(ValueError):
                    service._speech_delivery_context(
                        source_kind=source_kind,
                        interaction_ref=interaction_ref,
                        recipient_person_refs=recipient_person_refs,
                    )


if __name__ == "__main__":
    unittest.main()
