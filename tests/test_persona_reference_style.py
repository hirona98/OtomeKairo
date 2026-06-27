import unittest
from copy import deepcopy

from otomekairo.defaults import DEFAULT_PERSONA_ID, build_default_state
from otomekairo.llm.contexts import CurrentInput, SpeechContext, build_persona_context
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
    def test_default_persona_has_user_natural_reference(self) -> None:
        state = build_default_state()
        persona = state["personas"][DEFAULT_PERSONA_ID]

        self.assertEqual(persona["reference_style"]["user_natural_reference"], "マスター")

    def test_persona_context_prompt_payload_separates_schema_user_and_natural_reference(self) -> None:
        persona = deepcopy(build_default_state()["personas"][DEFAULT_PERSONA_ID])
        persona["reference_style"]["user_natural_reference"] = "マスター"

        context = build_persona_context(persona, role="decision_generation")
        payload = context.to_prompt_payload()
        summary = context.to_summary_payload()

        self.assertEqual(payload["reference_style"]["schema_user_reference"], "user")
        self.assertEqual(payload["reference_style"]["user_natural_reference"], "マスター")
        self.assertEqual(summary["reference_style"]["user_natural_reference"], "マスター")

    def test_activity_state_messages_include_reference_style_boundary(self) -> None:
        persona = deepcopy(build_default_state()["personas"][DEFAULT_PERSONA_ID])
        persona["reference_style"]["user_natural_reference"] = "マスター"
        context = build_persona_context(persona, role="activity_state")

        messages = build_activity_state_messages(
            persona_context=context,
            source_pack={"current_input": {"sender": "system", "text": "background thinking"}},
        )

        self.assertIn("persona_context.reference_style", messages[0]["content"])
        self.assertIn("schema key、enum", messages[0]["content"])
        self.assertIn('"schema_user_reference":"user"', messages[1]["content"])
        self.assertIn('"user_natural_reference":"マスター"', messages[1]["content"])

    def test_speech_stance_reason_uses_user_natural_reference(self) -> None:
        persona = deepcopy(build_default_state()["personas"][DEFAULT_PERSONA_ID])
        persona["reference_style"]["user_natural_reference"] = "マスター"
        persona_context = build_persona_context(persona, role="expression_generation", include_expression=True)
        current_input = CurrentInput(
            sender="user",
            source_kind="user_message",
            response_target="user",
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
        )

        messages = build_speech_messages(persona_context=persona_context, context=context)

        self.assertIn('"reason_summary":"マスター発話への直接応答。"', messages[1]["content"])

    def test_replace_persona_normalizes_user_natural_reference(self) -> None:
        service = DummyService()
        definition = deepcopy(service.store.state["personas"][DEFAULT_PERSONA_ID])
        definition["reference_style"]["user_natural_reference"] = " マスター "

        response = service.replace_persona("token", DEFAULT_PERSONA_ID, definition)

        self.assertEqual(response["persona"]["reference_style"]["user_natural_reference"], "マスター")

    def test_replace_persona_requires_reference_style(self) -> None:
        service = DummyService()
        definition = deepcopy(service.store.state["personas"][DEFAULT_PERSONA_ID])
        del definition["reference_style"]

        with self.assertRaises(ServiceError) as raised:
            service.replace_persona("token", DEFAULT_PERSONA_ID, definition)

        self.assertEqual(raised.exception.error_code, "invalid_persona_reference_style")


if __name__ == "__main__":
    unittest.main()
