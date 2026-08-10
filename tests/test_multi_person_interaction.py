from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from otomekairo.interaction import normalize_interaction_context
from otomekairo.llm.contexts import CurrentInput
from otomekairo.llm.client import LLMError
from otomekairo.service.common import ServiceError
from otomekairo.service.app import OtomeKairoService
from otomekairo.service.input.cycle import ServiceInputCycleMixin
from otomekairo.store.file_store import FileStore


class MultiPersonInteractionTests(unittest.TestCase):
    def test_external_person_reference_is_accepted_as_authoritative(self) -> None:
        context = normalize_interaction_context(
            {
                "interaction_ref": "interaction:discord:dm-123",
                "speaker_ref": "person:External-123",
                "participants": [
                    {
                        "person_ref": "person:External-123",
                        "display_name": " 田中 ",
                    }
                ],
            },
            required=True,
            require_speaker=True,
        )

        assert context is not None
        self.assertEqual(context.interaction_ref, "interaction:discord:dm-123")
        self.assertEqual(context.speaker_ref, "person:External-123")
        self.assertEqual(context.participant_refs, ("person:External-123",))
        self.assertEqual(context.participants[0].display_name, "田中")

    def test_group_shape_is_reserved_but_rejected_in_v1(self) -> None:
        with self.assertRaises(ServiceError) as raised:
            normalize_interaction_context(
                {
                    "interaction_ref": "interaction:group:test",
                    "speaker_ref": "person:a",
                    "participants": [
                        {"person_ref": "person:a", "display_name": "A"},
                        {"person_ref": "person:b", "display_name": "B"},
                    ],
                },
                required=True,
                require_speaker=True,
            )

        self.assertEqual(raised.exception.error_code, "unsupported_group_interaction")

    def test_display_name_is_required_for_every_interaction_participant(self) -> None:
        with self.assertRaises(ServiceError) as raised:
            normalize_interaction_context(
                {
                    "interaction_ref": "interaction:missing-name",
                    "speaker_ref": "person:missing-name",
                    "participants": [{"person_ref": "person:missing-name"}],
                },
                required=True,
                require_speaker=True,
            )

        self.assertEqual(raised.exception.error_code, "invalid_person_display_name")

    def test_recent_turns_are_isolated_by_interaction_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileStore(Path(temp_dir))
            memory_set_id = store.read_state()["selected_memory_set_id"]
            store.append_events(
                events=[
                    self._event(
                        event_id="event:a1",
                        memory_set_id=memory_set_id,
                        interaction_ref="interaction:a",
                        speaker_ref="person:a",
                        text="Aの発話",
                        created_at="2026-07-26T10:00:00+09:00",
                    ),
                    self._event(
                        event_id="event:b1",
                        memory_set_id=memory_set_id,
                        interaction_ref="interaction:b",
                        speaker_ref="person:b",
                        text="Bの発話",
                        created_at="2026-07-26T10:00:01+09:00",
                    ),
                    self._event(
                        event_id="event:a2",
                        memory_set_id=memory_set_id,
                        interaction_ref="interaction:a",
                        speaker_ref=None,
                        text="Aへの応答",
                        created_at="2026-07-26T10:00:02+09:00",
                        kind="speech",
                        role="assistant",
                    ),
                ]
            )

            turns = store.load_recent_turns(
                memory_set_id=memory_set_id,
                interaction_ref="interaction:a",
                since_iso="2026-07-26T09:00:00+09:00",
                limit=10,
            )

        self.assertEqual([turn["text"] for turn in turns], ["Aの発話", "Aへの応答"])
        self.assertEqual({turn["interaction_ref"] for turn in turns}, {"interaction:a"})

    def test_activity_and_registry_are_kept_per_external_person_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileStore(Path(temp_dir))
            memory_set_id = store.read_state()["selected_memory_set_id"]
            store.register_interaction_participants(
                memory_set_id=memory_set_id,
                participants=[
                    {"person_ref": "person:Alice", "display_name": "Alice"},
                    {"person_ref": "person:alice", "display_name": "alice"},
                ],
                observed_at="2026-07-26T10:00:00+09:00",
                evidence_event_ids=[],
            )
            for person_ref, suffix in (("person:Alice", "a"), ("person:alice", "b")):
                store.refresh_activity_state(
                    memory_set_id=memory_set_id,
                    actor_ref=person_ref,
                    current_time="2026-07-26T10:00:00+09:00",
                    activity_state={
                        "activity_id": f"activity:{suffix}",
                        "memory_set_id": memory_set_id,
                        "actor_ref": person_ref,
                        "label": f"活動{suffix}",
                        "status": "active",
                        "confidence": 0.8,
                        "salience": 0.7,
                        "started_at": "2026-07-26T10:00:00+09:00",
                        "updated_at": "2026-07-26T10:00:00+09:00",
                        "expires_at": "2026-07-26T11:00:00+09:00",
                    },
                )

            registry_refs = {
                record["entity_ref"]
                for record in store.list_entity_registry_records(memory_set_id=memory_set_id)
            }
            first = store.get_current_activity_state(
                memory_set_id=memory_set_id,
                actor_ref="person:Alice",
                current_time="2026-07-26T10:30:00+09:00",
            )
            second = store.get_current_activity_state(
                memory_set_id=memory_set_id,
                actor_ref="person:alice",
                current_time="2026-07-26T10:30:00+09:00",
            )

        self.assertEqual(registry_refs, {"person:Alice", "person:alice"})
        assert first is not None and second is not None
        self.assertEqual(first["activity_id"], "activity:a")
        self.assertEqual(second["activity_id"], "activity:b")

    def test_people_context_contains_only_structurally_relevant_people(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            memory_set_id = state["selected_memory_set_id"]
            service.store.register_interaction_participants(
                memory_set_id=memory_set_id,
                participants=[
                    {"person_ref": "person:a", "display_name": "同名"},
                    {"person_ref": "person:b", "display_name": "同名"},
                    {"person_ref": "person:unrelated", "display_name": "無関係"},
                ],
                observed_at="2026-07-26T10:00:00+09:00",
                evidence_event_ids=[],
            )
            current_input = CurrentInput(
                sender_kind="system",
                sender_ref=None,
                source_kind="background_thinking",
                response_target_refs=(),
                interaction_context=None,
                text="background thinking",
            )

            people_context = service._build_people_context(
                state=state,
                current_input=current_input,
                structured_sources=[
                    {
                        "person_model": [
                            {"scope_type": "entity", "scope_key": "person:a"},
                        ],
                        "relationship_model": [
                            {"scope_type": "relationship", "scope_key": "self|person:b"},
                        ],
                    }
                ],
            )

        self.assertEqual(
            people_context,
            [
                {"person_ref": "person:a", "display_name": "同名"},
                {"person_ref": "person:b", "display_name": "同名"},
            ],
        )

    def test_autonomous_run_cancel_is_an_explicit_protocol_action(self) -> None:
        service = ServiceInputCycleMixin()

        self.assertIsNone(service._normalize_conversation_autonomous_run_action(None))
        self.assertEqual(
            service._normalize_conversation_autonomous_run_action({"kind": "cancel_all"}),
            "cancel_all",
        )
        with self.assertRaises(ServiceError) as raised:
            service._normalize_conversation_autonomous_run_action({"kind": "stop"})
        self.assertEqual(raised.exception.error_code, "invalid_autonomous_run_action")

    def test_conversation_compact_trace_keeps_interaction_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            context = normalize_interaction_context(
                {
                    "interaction_ref": "interaction:trace",
                    "speaker_ref": "person:trace",
                    "participants": [{"person_ref": "person:trace", "display_name": "Trace"}],
                },
                required=True,
                require_speaker=True,
            )
            summary = service._build_trigger_compact_summary(
                trigger_kind="user_message",
                input_text="こんにちは",
                interaction_context=context,
                observation_summary=None,
                capability_request_summary=None,
                followup_capability_request_summary=None,
                decision=None,
                result_kind="internal_failure",
                speech_payload=None,
                pending_intent_summary=None,
                pending_intent_selection=None,
                initiative_context=None,
                ongoing_action_transition_summary=None,
                failure_reason="test",
            )

        current_input = summary["current_input_summary"]
        self.assertEqual(current_input["sender_ref"], "person:trace")
        self.assertEqual(
            current_input["interaction_context"]["interaction_ref"],
            "interaction:trace",
        )

    def test_failed_cycle_keeps_logical_context_and_registers_person(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            state["console_access_token"] = "test-token"
            service.store.write_state(state)

            with patch.object(
                type(service.llm),
                "generate_input_interpretation",
                side_effect=LLMError("test failure"),
            ):
                result = service.handle_conversation(
                    "test-token",
                    {
                        "message_id": "chat_message:test-failure",
                        "text": "失敗する入力",
                        "interaction_context": {
                            "interaction_ref": "interaction:failure",
                            "speaker_ref": "person:failure",
                            "participants": [
                                {
                                    "person_ref": "person:failure",
                                    "display_name": "失敗時の人物",
                                }
                            ],
                        },
                    },
                )
            registry = service.store.list_entity_registry_records(
                memory_set_id=state["selected_memory_set_id"],
            )

        self.assertEqual(result["result_kind"], "internal_failure")
        self.assertEqual(result["interaction_ref"], "interaction:failure")
        self.assertEqual(result["recipient_person_refs"], ["person:failure"])
        self.assertEqual(registry[0]["entity_ref"], "person:failure")

    def test_conversation_cycle_persists_logical_person_and_interaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            state["console_access_token"] = "test-token"
            state["model_presets"][state["selected_model_preset_id"]]["model"] = "mock"
            state["memory_sets"][state["selected_memory_set_id"]]["embedding"]["model"] = "mock"
            service.store.write_state(state)

            result = service.handle_conversation(
                "test-token",
                {
                    "message_id": "chat_message:test-e2e",
                    "text": "こんにちは",
                    "interaction_context": {
                        "interaction_ref": "interaction:e2e",
                        "speaker_ref": "person:e2e",
                        "participants": [
                            {
                                "person_ref": "person:e2e",
                                "display_name": "E2E",
                            }
                        ],
                    },
                },
            )
            turns = service.store.load_recent_turns(
                memory_set_id=state["selected_memory_set_id"],
                interaction_ref="interaction:e2e",
                since_iso="2000-01-01T00:00:00+09:00",
                limit=10,
            )
            episodes = service.store.list_episodes_for_recall(
                memory_set_id=state["selected_memory_set_id"],
                limit=10,
            )

        self.assertEqual(result["result_kind"], "speech")
        self.assertEqual(result["interaction_ref"], "interaction:e2e")
        self.assertEqual(result["recipient_person_refs"], ["person:e2e"])
        self.assertEqual([turn["role"] for turn in turns], ["person", "assistant"])
        self.assertEqual([turn["speaker_ref"] for turn in turns], ["person:e2e", "self"])
        self.assertEqual(episodes[0]["source_interaction_refs"], ["interaction:e2e"])
        self.assertEqual(episodes[0]["source_participant_refs"], ["person:e2e"])

    def _event(
        self,
        *,
        event_id: str,
        memory_set_id: str,
        interaction_ref: str,
        speaker_ref: str | None,
        text: str,
        created_at: str,
        kind: str = "conversation_input",
        role: str = "person",
    ) -> dict:
        return {
            "event_id": event_id,
            "cycle_id": f"cycle:{event_id}",
            "memory_set_id": memory_set_id,
            "kind": kind,
            "role": role,
            "text": text,
            "interaction_ref": interaction_ref,
            "speaker_ref": speaker_ref,
            "participant_refs": [speaker_ref] if speaker_ref is not None else ["person:a"],
            "created_at": created_at,
        }


if __name__ == "__main__":
    unittest.main()
