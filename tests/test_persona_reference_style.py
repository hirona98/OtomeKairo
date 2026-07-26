import unittest
from copy import deepcopy

from otomekairo.defaults import DEFAULT_PERSONA_ID, build_default_state
from otomekairo.interaction import InteractionContext, ParticipantContext
from otomekairo.llm.contexts import CurrentInput, SpeechContext, build_persona_context
from otomekairo.llm.mock import MockLLMClient
from otomekairo.llm.prompts import build_activity_state_messages, build_speech_messages
from otomekairo.service.common import ServiceError
from otomekairo.service.config.mixin import ServiceConfigMixin


class DummyStore:
    def __init__(self) -> None:
        self.state = build_default_state()
        self.state["console_access_token"] = "token"

    def read_state(self) -> dict:
        return deepcopy(self.state)

    def write_state(self, state: dict) -> None:
        self.state = deepcopy(state)


class DummyService(ServiceConfigMixin):
    def __init__(self) -> None:
        self.store = DummyStore()

    def _clear_pending_intent_candidates(self) -> None:
        return None


class PersonaReferenceStyleTests(unittest.TestCase):
    def test_default_persona_has_no_interlocutor_address_term(self) -> None:
        state = build_default_state()
        persona = state["personas"][DEFAULT_PERSONA_ID]

        self.assertIsNone(persona["reference_style"]["interlocutor_address_term"])

    def test_internal_persona_context_excludes_interlocutor_address_term(self) -> None:
        persona = deepcopy(build_default_state()["personas"][DEFAULT_PERSONA_ID])
        persona["reference_style"]["interlocutor_address_term"] = "マスター"

        context = build_persona_context(persona, role="decision_generation")
        payload = context.to_prompt_payload()
        summary = context.to_summary_payload()

        self.assertNotIn("reference_style", payload)
        self.assertNotIn("reference_style", summary)

    def test_activity_state_messages_use_structured_person_boundary(self) -> None:
        persona = deepcopy(build_default_state()["personas"][DEFAULT_PERSONA_ID])
        persona["reference_style"]["interlocutor_address_term"] = "マスター"
        context = build_persona_context(persona, role="activity_state")

        messages = build_activity_state_messages(
            persona_context=context,
            source_pack={"current_input": {"sender_kind": "system", "text": "background thinking"}},
        )

        self.assertIn("people_context", messages[0]["content"])
        self.assertIn("schema key、enum", messages[0]["content"])
        self.assertNotIn("マスター", messages[0]["content"])
        self.assertNotIn("マスター", messages[1]["content"])

    def test_expression_context_alone_receives_interlocutor_address_term(self) -> None:
        persona = deepcopy(build_default_state()["personas"][DEFAULT_PERSONA_ID])
        persona["reference_style"]["interlocutor_address_term"] = "マスター"
        persona_context = build_persona_context(persona, role="expression_generation", include_expression=True)
        current_input = CurrentInput(
            sender_kind="person",
            sender_ref="person:test",
            source_kind="user_message",
            response_target_refs=("person:test",),
            interaction_context=InteractionContext(
                interaction_ref="interaction:test",
                speaker_ref="person:test",
                participants=(ParticipantContext(person_ref="person:test", display_name="田中"),),
            ),
            text="おはよう",
        )
        context = SpeechContext(
            input_text="おはよう",
            current_input=current_input,
            recent_turns=[],
            time_context={},
            affect_context={},
            drive_state_summary=None,
            foreground_world_state=None,
            activity_context=None,
            ongoing_action_summary=None,
            initiative_context=None,
            visual_observation_context=None,
            self_state_context=None,
            relationship_context=None,
            prediction_error_context=None,
            workspace_context=None,
            recall_hint={},
            recall_pack={},
            decision={"kind": "speech", "reason_summary": "応答する。"},
            people_context=[{"person_ref": "person:test", "display_name": "田中"}],
        )

        messages = build_speech_messages(persona_context=persona_context, context=context)

        self.assertIn('"interlocutor_address_term":"マスター"', messages[1]["content"])
        self.assertIn('"target_person_ref":"person:test"', messages[1]["content"])
        self.assertIn('"reason_summary":"人物発話への直接応答。"', messages[1]["content"])
        self.assertNotIn("マスターの発話", messages[1]["content"])

    def test_mock_memory_uses_api_display_name_for_internal_person_text(self) -> None:
        persona = deepcopy(build_default_state()["personas"][DEFAULT_PERSONA_ID])
        persona_context = build_persona_context(persona, role="memory_interpretation")
        current_input = CurrentInput(
            sender_kind="person",
            sender_ref="person:test",
            source_kind="user_message",
            response_target_refs=("person:test",),
            interaction_context=InteractionContext(
                interaction_ref="interaction:test",
                speaker_ref="person:test",
                participants=(ParticipantContext(person_ref="person:test", display_name="田中"),),
            ),
            text="辛い食べ物が好き",
        )

        payload = MockLLMClient().generate_memory_interpretation(
            {"model": "mock"},
            "辛い食べ物が好き",
            {"primary_recall_focus": "preference"},
            {"reason_summary": "好みを受け止める。"},
            None,
            {
                "current_input": current_input.to_prompt_payload(),
                "people_context": [{"person_ref": "person:test", "display_name": "田中"}],
            },
            persona_context=persona_context,
        )

        summary_text = payload["candidate_memory_units"][0]["summary_text"]
        self.assertIn("田中", summary_text)
        self.assertNotIn("あなた", summary_text)

    def test_replace_persona_normalizes_interlocutor_address_term(self) -> None:
        service = DummyService()
        definition = deepcopy(service.store.state["personas"][DEFAULT_PERSONA_ID])
        definition["reference_style"]["interlocutor_address_term"] = " マスター "

        response = service.replace_persona("token", DEFAULT_PERSONA_ID, definition)

        self.assertEqual(response["persona"]["reference_style"]["interlocutor_address_term"], "マスター")

    def test_replace_persona_accepts_null_interlocutor_address_term(self) -> None:
        service = DummyService()
        definition = deepcopy(service.store.state["personas"][DEFAULT_PERSONA_ID])

        response = service.replace_persona("token", DEFAULT_PERSONA_ID, definition)

        self.assertIsNone(response["persona"]["reference_style"]["interlocutor_address_term"])

    def test_replace_persona_requires_reference_style(self) -> None:
        service = DummyService()
        definition = deepcopy(service.store.state["personas"][DEFAULT_PERSONA_ID])
        del definition["reference_style"]

        with self.assertRaises(ServiceError) as raised:
            service.replace_persona("token", DEFAULT_PERSONA_ID, definition)

        self.assertEqual(raised.exception.error_code, "invalid_persona_reference_style")


if __name__ == "__main__":
    unittest.main()
