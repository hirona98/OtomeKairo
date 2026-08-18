from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import CurrentInput, PersonaContext, SpeechContext
from otomekairo.llm.contracts import LLMError
from otomekairo.llm.transport import CompletionResult, complete_text


def _completion_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _persona_context() -> PersonaContext:
    return PersonaContext(
        display_name="Test",
        initiative_baseline={"level": "medium", "summary_text": "test"},
        persona_prompt_text="テスト人格。",
        expression_addon=None,
        use_policy="テスト判断に使う。",
    )


def _current_input() -> CurrentInput:
    return CurrentInput(
        sender_kind="person",
        sender_ref="person:test",
        source_kind="user_message",
        response_target_refs=("person:test",),
        interaction_context=None,
        text="こんにちは",
    )


class LLMTransportTests(unittest.TestCase):
    def test_openrouter_structured_call_requires_parameters_and_keeps_reasoning(self) -> None:
        captured: dict = {}

        def fake_completion(**kwargs):
            captured.update(kwargs)
            return _completion_response("{}")

        with patch("otomekairo.llm.transport._load_litellm_completion", return_value=fake_completion):
            complete_text(
                model_config={
                    "model": "openrouter/google/gemini-3.5-flash-lite",
                    "reasoning_effort": "low",
                },
                messages=[{"role": "user", "content": "x"}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "t", "strict": True, "schema": {"type": "object"}},
                },
            )

        self.assertEqual(captured["response_format"]["type"], "json_schema")
        self.assertEqual(captured["extra_body"]["reasoning"], {"effort": "low"})
        self.assertEqual(captured["extra_body"]["provider"], {"require_parameters": True})

    def test_non_openrouter_structured_call_does_not_set_provider(self) -> None:
        captured: dict = {}

        def fake_completion(**kwargs):
            captured.update(kwargs)
            return _completion_response("{}")

        with patch("otomekairo.llm.transport._load_litellm_completion", return_value=fake_completion):
            complete_text(
                model_config={
                    "model": "openai/gpt-4o",
                    "reasoning_effort": "low",
                },
                messages=[{"role": "user", "content": "x"}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "t", "strict": True, "schema": {"type": "object"}},
                },
            )

        self.assertEqual(captured["response_format"]["type"], "json_schema")
        self.assertEqual(captured["reasoning_effort"], "low")
        self.assertNotIn("extra_body", captured)

    def test_openrouter_speech_does_not_require_parameters(self) -> None:
        captured: dict = {}

        def fake_completion(**kwargs):
            captured.update(kwargs)
            return _completion_response("こんにちは")

        with patch("otomekairo.llm.transport._load_litellm_completion", return_value=fake_completion):
            complete_text(
                model_config={
                    "model": "openrouter/google/gemini-3.5-flash-lite",
                    "reasoning_effort": "low",
                },
                messages=[{"role": "user", "content": "x"}],
            )

        self.assertNotIn("response_format", captured)
        self.assertEqual(captured["extra_body"]["reasoning"], {"effort": "low"})
        self.assertNotIn("provider", captured["extra_body"])

    def test_litellm_error_is_not_retried_without_schema(self) -> None:
        calls: list[dict] = []

        def fake_complete(**kwargs):
            calls.append(kwargs)
            raise LLMError("structured outputs not supported")

        client = LLMClient()
        client.push_usage_scope("test")
        with patch("otomekairo.llm.client.complete_text", side_effect=fake_complete):
            with self.assertRaisesRegex(LLMError, "structured outputs not supported"):
                client.generate_pre_send_check(
                    model_config={"model": "openrouter/google/gemini-3.5-flash-lite"},
                    review_context={"tool_name": "get_information", "arguments": {}},
                )

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["response_format"]["type"], "json_schema")
        self.assertEqual(calls[0]["response_format"]["json_schema"]["name"], "pre_send_check")

    def test_structured_client_passes_json_schema(self) -> None:
        client = LLMClient()
        client.push_usage_scope("test")
        with patch(
            "otomekairo.llm.client.complete_text",
            return_value=CompletionResult(
                text=json.dumps({"outcome": "allow", "reason_summary": "公開情報のみ。"}),
                usage={},
            ),
        ) as complete:
            client.generate_pre_send_check(
                model_config={"model": "openrouter/google/gemini-3.5-flash-lite"},
                review_context={"tool_name": "get_information", "arguments": {}},
            )

        kwargs = complete.call_args.kwargs
        self.assertEqual(kwargs["response_format"]["type"], "json_schema")
        self.assertEqual(kwargs["response_format"]["json_schema"]["name"], "pre_send_check")
        self.assertTrue(kwargs["response_format"]["json_schema"]["strict"])

    def test_speech_does_not_pass_response_format(self) -> None:
        context = SpeechContext(
            input_text="こんにちは",
            current_input=_current_input(),
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
            decision={"kind": "speech"},
        )
        client = LLMClient()
        client.push_usage_scope("test")
        with patch(
            "otomekairo.llm.client.complete_text",
            return_value=CompletionResult(text="こんにちは", usage={}),
        ) as complete:
            client.generate_speech(
                model_config={"model": "openrouter/google/gemini-3.5-flash-lite"},
                persona_context=_persona_context(),
                context=context,
            )

        kwargs = complete.call_args.kwargs
        self.assertIsNone(kwargs.get("response_format"))
        self.assertEqual(kwargs.get("operation"), "speech")
