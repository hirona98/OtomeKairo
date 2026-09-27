from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import Mock

from otomekairo.llm.contracts import LLMError, validate_affect_review_contract
from otomekairo.memory.consolidator import MemoryConsolidator


SELF_CONCERN = {
    "target_scope_type": "self", "target_scope_key": "self",
    "affect_label": "心配", "vad": {"v": -0.4, "a": 0.3, "d": -0.1},
    "intensity": 0.5, "confidence": 0.8,
    "summary_text": "友人の体調が気がかりになった。",
}
RELATIONSHIP_CARE = {
    "target_scope_type": "relationship", "target_scope_key": "self|person:master",
    "affect_label": "気遣い", "vad": {"v": 0.4, "a": 0.1, "d": 0.2},
    "intensity": 0.4, "confidence": 0.8,
    "summary_text": "マスターを気遣い、関係に温かさを感じた。",
}


class AffectReviewTests(unittest.TestCase):
    def test_contract_separates_self_reaction_and_target_affects(self) -> None:
        valid = {
            "self_reaction": {"affect": SELF_CONCERN, "reason_summary": "体調不良を案じた。"},
            "other_affects": [RELATIONSHIP_CARE],
            "reason_summary": "心配と気遣いを分けた。",
        }
        validate_affect_review_contract(valid)
        validate_affect_review_contract({
            "self_reaction": {"affect": None, "reason_summary": "自己反応の根拠がない。"},
            "other_affects": [], "reason_summary": "反応を記録しない。",
        })
        invalid_cases = [
            {**valid, "self_reaction": {"affect": RELATIONSHIP_CARE, "reason_summary": "誤分類。"}},
            {**valid, "other_affects": [SELF_CONCERN]},
            {**valid, "other_affects": [RELATIONSHIP_CARE] * 4},
            {**valid, "self_reaction": {"affect": {**SELF_CONCERN, "vad": {"v": 1.2, "a": 0.3, "d": 0}}, "reason_summary": "範囲外。"}},
        ]
        for invalid in invalid_cases:
            with self.subTest(invalid=invalid), self.assertRaises(LLMError):
                validate_affect_review_contract(invalid)

    def test_review_can_restore_concern_without_erasing_care(self) -> None:
        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        consolidator.llm = Mock()
        consolidator.llm.generate_affect_review.return_value = {
            "self_reaction": {"affect": SELF_CONCERN, "reason_summary": "友人の発熱を案じた。"},
            "other_affects": [RELATIONSHIP_CARE],
            "reason_summary": "心配と気遣いを分けた。",
        }
        interpretation = {
            "episode": {"summary_text": "友人の発熱を聞いた。"},
            "episode_affects": [deepcopy(RELATIONSHIP_CARE)],
        }

        reviewed, trace = consolidator._review_episode_affects(
            selected_preset={"model": "mock-test"}, selected_persona={},
            input_text="友人が熱を出したと聞いて心配だ。",
            decision={}, speech_text="それは心配ですね。",
            interpretation_context={"people_context": [{"person_ref": "person:master"}]},
            interpretation=interpretation,
        )

        self.assertEqual(reviewed, [SELF_CONCERN, RELATIONSHIP_CARE])
        self.assertEqual(interpretation["episode_affects"], [RELATIONSHIP_CARE])
        self.assertTrue(trace["changed"])
        self.assertTrue(trace["self_reaction_present"])
        review_context = consolidator.llm.generate_affect_review.call_args.kwargs["review_context"]
        self.assertEqual(review_context["speech_text"], "それは心配ですね。")
        self.assertEqual(review_context["candidate_episode_affects"], [RELATIONSHIP_CARE])


if __name__ == "__main__":
    unittest.main()
