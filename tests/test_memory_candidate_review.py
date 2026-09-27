from __future__ import annotations

import unittest
from unittest.mock import Mock

from otomekairo.llm.contracts import LLMError, validate_memory_candidate_review_contract
from otomekairo.memory.consolidator import MemoryConsolidator


class MemoryCandidateReviewTests(unittest.TestCase):
    def test_review_contract_requires_one_decision_per_candidate(self) -> None:
        episode_review = {
            "summary_text": "今日の出来事。", "outcome_text": None,
            "open_loops": [], "reason_summary": "元の発話に一致。",
        }
        valid = {"episode_review": episode_review, "decisions": [
            {"index": 0, "outcome": "drop", "reason_summary": "一度の出来事。"},
            {"index": 1, "outcome": "keep", "reason_summary": "普段の好み。"},
        ]}
        validate_memory_candidate_review_contract(valid, candidate_count=2)
        for invalid in (
            {**valid, "decisions": valid["decisions"][:1]},
            {**valid, "decisions": [valid["decisions"][0], valid["decisions"][0]]},
            {**valid, "decisions": [valid["decisions"][0], {**valid["decisions"][1], "outcome": "unknown"}]},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(LLMError):
                validate_memory_candidate_review_contract(invalid, candidate_count=2)

    def test_review_drops_one_off_fact_and_keeps_stated_preference(self) -> None:
        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        consolidator.llm = Mock()
        consolidator.llm.generate_memory_candidate_review.return_value = {"episode_review": {
            "summary_text": "今日の出来事。", "outcome_text": None,
            "open_loops": [], "reason_summary": "元の発話に一致。",
        }, "decisions": [
            {"index": 0, "outcome": "drop", "reason_summary": "一度の仕事の出来事。"},
            {"index": 1, "outcome": "keep", "reason_summary": "普段のお茶の好み。"},
        ]}
        candidates = [
            {"memory_type": "fact", "summary_text": "今日は段取りを誤った。", "evidence_text": "今日のこと。", "qualifiers_hint": {}},
            {"memory_type": "preference", "summary_text": "普段はほうじ茶を飲む。", "evidence_text": "普段のお茶。", "qualifiers_hint": {}},
        ]

        selected, trace = consolidator._review_memory_candidates(
            selected_preset={"model": "mock-test"},
            selected_persona={},
            input_text="今日は段取りを誤った。普段はほうじ茶を飲む。",
            recall_hint={},
            interpretation_context={},
            interpretation={"episode": {"summary_text": "今日の出来事。", "outcome_text": None, "open_loops": []}, "candidate_memory_units": candidates},
        )

        self.assertEqual(selected, candidates[1:])
        self.assertEqual(trace["dropped_count"], 1)
        review_context = consolidator.llm.generate_memory_candidate_review.call_args.kwargs["review_context"]
        self.assertEqual(review_context["candidates"][0]["index"], 0)

    def test_review_does_not_partially_apply_selected_correction(self) -> None:
        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        consolidator.llm = Mock()
        consolidator.llm.generate_memory_candidate_review.return_value = {"episode_review": {
            "summary_text": "訂正。", "outcome_text": None,
            "open_loops": [], "reason_summary": "元の発話に一致。",
        }, "decisions": [
            {"index": 0, "outcome": "drop", "reason_summary": "根拠がない。"},
        ]}
        with self.assertRaisesRegex(ValueError, "during correction selection"):
            consolidator._review_memory_candidates(
                selected_preset={"model": "mock-test"},
                selected_persona={},
                input_text="訂正します。",
                recall_hint={},
                interpretation_context={},
                interpretation={
                    "episode": {"summary_text": "訂正。", "outcome_text": None, "open_loops": []},
                    "candidate_memory_units": [{
                        "memory_type": "fact", "summary_text": "訂正後。", "evidence_text": "訂正。", "qualifiers_hint": {},
                    }],
                    "correction_status": "selected",
                },
            )

    def test_review_corrects_episode_even_without_memory_candidates(self) -> None:
        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        consolidator.llm = Mock()
        consolidator.llm.generate_memory_candidate_review.return_value = {
            "episode_review": {
                "summary_text": "マスターは今夜、窓を開けて換気しようかと考えた。",
                "outcome_text": None,
                "open_loops": [],
                "reason_summary": "一回の予定で、実行はまだ確認されていない。",
            },
            "decisions": [],
        }
        interpretation = {
            "episode": {
                "summary_text": "マスターは換気を習慣として行った。",
                "outcome_text": None,
                "open_loops": [],
            },
            "candidate_memory_units": [],
        }

        selected, trace = consolidator._review_memory_candidates(
            selected_preset={"model": "mock-test"},
            selected_persona={},
            input_text="今夜は窓を開けて換気しようかな。",
            recall_hint={},
            interpretation_context={},
            interpretation=interpretation,
        )

        self.assertEqual(selected, [])
        self.assertEqual(trace["reviewed_count"], 0)
        self.assertTrue(trace["episode_review"]["changed"])
        self.assertEqual(interpretation["episode"]["summary_text"], "マスターは今夜、窓を開けて換気しようかと考えた。")


if __name__ == "__main__":
    unittest.main()
