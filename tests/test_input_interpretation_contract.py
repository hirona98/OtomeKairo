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
        "primary_recall_focus": "person",
        "secondary_recall_focuses": [],
        "confidence": 0.8,
        "time_reference": "none",
        "focus_scopes": ["entity:person:test"],
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
                "persona_prompt": "入力を落ち着いて解釈する。",
            },
            role="input_interpretation",
        )
        messages = build_input_interpretation_messages(
            persona_context=persona_context,
            current_input=CurrentInput(
                sender_kind="person",
                sender_ref="person:test",
                source_kind="conversation",
                response_target_refs=("person:test",),
                interaction_context=None,
                text="今日は少し眠い。",
            ),
            recent_turns=[],
            current_time="2026-06-22T10:00:00+09:00",
            visual_observation_context=None,
        )
        system_prompt = messages[0]["content"]
        repair_prompt = build_input_interpretation_repair_prompt("test")
        self.assertNotIn("出力 JSON は structured schema の必須キーと enum に従います", system_prompt)
        self.assertNotIn("トップレベルは recall_hint と answer_contract だけです", system_prompt)
        self.assertNotIn("省略せず []", system_prompt)
        self.assertNotIn("の 8 キーだけ", repair_prompt)
        self.assertNotIn("の 5 キーだけ", repair_prompt)
        self.assertIn("入力が求める根拠境界", repair_prompt)

    def test_input_interpretation_prompt_includes_self_activity_orientation(self) -> None:
        persona_context = build_persona_context(
            {
                "display_name": "テスト",
                "initiative_baseline": "medium",
                "persona_prompt": "入力を落ち着いて解釈する。",
            },
            role="input_interpretation",
        )
        messages = build_input_interpretation_messages(
            persona_context=persona_context,
            current_input=CurrentInput(
                sender_kind="system",
                sender_ref=None,
                source_kind="background_thinking",
                response_target_refs=(),
                interaction_context=None,
                text="自己評価。しばらく関わっていない気にかけていることがある。",
            ),
            recent_turns=[],
            current_time="2026-06-22T10:00:00+09:00",
            visual_observation_context=None,
            self_activity_orientation=[
                {"kind": "standing_concern", "summary_text": "他の個との関係を育てる。"}
            ],
        )
        system_prompt = messages[0]["content"]
        context_prompt = messages[1]["content"]
        self.assertIn("self_activity_orientation は自身の活動の向き本文", system_prompt)
        self.assertIn("他の個との関係を育てる。", context_prompt)
        self.assertIn("standing_concern", context_prompt)


if __name__ == "__main__":
    unittest.main()
