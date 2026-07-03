from __future__ import annotations

import unittest

from otomekairo.llm.contexts import CurrentInput, build_persona_context
from otomekairo.llm.contracts import (
    ANSWER_CONTRACT_REQUIRED_KEYS,
    LLMError,
    RECALL_HINT_REQUIRED_KEYS,
    validate_recall_hint_contract,
)
from otomekairo.llm.prompts import (
    build_input_interpretation_messages,
    build_input_interpretation_repair_prompt,
)


def _valid_recall_hint() -> dict:
    return {
        "primary_recall_focus": "user",
        "secondary_recall_focuses": [],
        "confidence": 0.8,
        "time_reference": "none",
        "focus_scopes": ["user"],
        "mentioned_entities": [],
        "mentioned_topics": [],
        "risk_flags": [],
    }


class InputInterpretationContractTests(unittest.TestCase):
    def test_recall_hint_accepts_required_keys(self) -> None:
        validate_recall_hint_contract(_valid_recall_hint())

    def test_recall_hint_key_error_reports_missing_keys(self) -> None:
        payload = _valid_recall_hint()
        del payload["risk_flags"]

        with self.assertRaisesRegex(LLMError, "不足=risk_flags"):
            validate_recall_hint_contract(payload)

    def test_recall_hint_key_error_reports_extra_keys(self) -> None:
        payload = _valid_recall_hint()
        payload["mode"] = "summary"

        with self.assertRaisesRegex(LLMError, "余計=mode"):
            validate_recall_hint_contract(payload)

    def test_input_interpretation_prompt_lists_exact_contract_keys(self) -> None:
        persona_context = build_persona_context(
            {
                "display_name": "テスト",
                "initiative_baseline": "medium",
                "reference_style": {"user_natural_reference": "マスター"},
                "persona_prompt": "入力を落ち着いて解釈する。",
            },
            role="input_interpretation",
        )
        messages = build_input_interpretation_messages(
            persona_context=persona_context,
            current_input=CurrentInput(
                sender="user",
                source_kind="conversation",
                response_target="user",
                text="今日は少し眠い。",
            ),
            recent_turns=[],
            current_time="2026-06-22T10:00:00+09:00",
            visual_observation_context=None,
        )
        system_prompt = messages[0]["content"]
        repair_prompt = build_input_interpretation_repair_prompt("test")
        recall_hint_keys = ", ".join(RECALL_HINT_REQUIRED_KEYS)
        answer_contract_keys = ", ".join(ANSWER_CONTRACT_REQUIRED_KEYS)

        self.assertIn(f"recall_hint は {recall_hint_keys} の 8 キーだけ", system_prompt)
        self.assertIn(f"answer_contract は {answer_contract_keys} の 5 キーだけ", system_prompt)
        self.assertIn("省略せず []", system_prompt)
        self.assertIn(f"recall_hint は {recall_hint_keys} の 8 キーだけ", repair_prompt)
        self.assertIn(f"answer_contract は {answer_contract_keys} の 5 キーだけ", repair_prompt)


if __name__ == "__main__":
    unittest.main()
