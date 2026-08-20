from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contracts import LLMError
from otomekairo.llm.transport import ROLE_MAX_OUTPUT_TOKENS, CompletionResult, complete_text
from otomekairo.llm.usage import consume_client_usage, extract_usage, merge_usage_summaries, summarize_usage_events


def _completion_response(content: str, usage: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=None if usage is None else SimpleNamespace(**usage),
    )


class LlmUsageTests(unittest.TestCase):
    def test_extract_usage_reads_cached_and_reasoning(self) -> None:
        response = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                prompt_tokens_details=SimpleNamespace(cached_tokens=40),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=8),
            )
        )
        self.assertEqual(
            extract_usage(response),
            {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "cached_tokens": 40,
                "reasoning_tokens": 8,
                "total_tokens": 120,
            },
        )

    def test_summarize_usage_omits_missing_totals(self) -> None:
        summary = summarize_usage_events(
            [
                {"operation": "decision"},
                {"operation": "speech", "prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            ]
        )
        self.assertEqual(summary["call_count"], 2)
        self.assertEqual(summary["prompt_tokens"], 10)
        self.assertEqual(summary["completion_tokens"], 4)
        self.assertEqual(summary["total_tokens"], 14)
        self.assertNotIn("cached_tokens", summary)

    def test_merge_usage_summaries_appends_calls(self) -> None:
        merged = merge_usage_summaries(
            {"call_count": 1, "calls": [{"operation": "decision", "prompt_tokens": 5, "total_tokens": 5}]},
            {"call_count": 1, "calls": [{"operation": "speech", "prompt_tokens": 3, "total_tokens": 3}]},
        )
        self.assertEqual(merged["call_count"], 2)
        self.assertEqual(merged["prompt_tokens"], 8)
        self.assertEqual([call["operation"] for call in merged["calls"]], ["decision", "speech"])

    def test_complete_text_records_usage_and_role_cap(self) -> None:
        captured: dict = {}

        def fake_completion(**kwargs):
            captured.update(kwargs)
            return _completion_response(
                "{}",
                {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13},
            )

        with patch("otomekairo.llm.transport._load_litellm_completion", return_value=fake_completion):
            result = complete_text(
                model_config={
                    "model": "openrouter/google/gemini-3.5-flash-lite",
                    "max_output_tokens": 40000,
                },
                messages=[{"role": "user", "content": "x"}],
                operation="visual_observation",
            )

        self.assertEqual(result.text, "{}")
        self.assertEqual(result.usage["prompt_tokens"], 11)
        self.assertEqual(captured["max_tokens"], ROLE_MAX_OUTPUT_TOKENS["visual_observation"])
        self.assertGreaterEqual(ROLE_MAX_OUTPUT_TOKENS["event_evidence"], 8000)
        self.assertGreaterEqual(ROLE_MAX_OUTPUT_TOKENS["decision"], 8000)

    def test_nested_scope_keeps_parent_events(self) -> None:
        client = LLMClient()
        client.push_usage_scope("parent")
        with patch(
            "otomekairo.llm.client.complete_text",
            return_value=CompletionResult(
                text=json.dumps({"outcome": "allow", "reason_summary": "公開情報のみ。"}),
                usage={"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
            ),
        ):
            client.generate_pre_send_check(
                model_config={"model": "openrouter/google/gemini-3.5-flash-lite"},
                review_context={"tool_name": "get_information", "arguments": {}},
            )
            client.push_usage_scope("child")
            client.generate_pre_send_check(
                model_config={"model": "openrouter/google/gemini-3.5-flash-lite"},
                review_context={"tool_name": "get_information", "arguments": {}},
            )
            child = consume_client_usage(client, scope_id="child")
        parent = consume_client_usage(client, scope_id="parent")
        self.assertEqual(child["call_count"], 1)
        self.assertEqual(parent["call_count"], 1)
        self.assertEqual(parent["calls"][0]["operation"], "pre_send_check")

    def test_record_without_scope_and_string_completion_fail(self) -> None:
        client = LLMClient()
        with self.assertRaises(LLMError):
            client._record_usage(operation="speech", usage={"prompt_tokens": 1})
        client.push_usage_scope("test")
        with patch("otomekairo.llm.client.complete_text", return_value="not-a-result"):
            with self.assertRaises(LLMError):
                client.generate_pre_send_check(
                    model_config={"model": "openrouter/google/gemini-3.5-flash-lite"},
                    review_context={"tool_name": "get_information", "arguments": {}},
                )
        with self.assertRaises(TypeError):
            consume_client_usage(object(), scope_id="test")
