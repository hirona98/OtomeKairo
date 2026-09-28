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
        valid = {"episode_review": episode_review, "correction_review": {
            "prior_claim_assessment": "not_reviewed", "reason_summary": "訂正はない。",
        }, "decisions": [
            {"index": 0, "outcome": "drop", "reason_summary": "一度の出来事。"},
            {"index": 1, "outcome": "keep", "reason_summary": "普段の好み。"},
        ]}
        validate_memory_candidate_review_contract(valid, candidate_count=2)
        for invalid in (
            {**valid, "decisions": valid["decisions"][:1]},
            {**valid, "decisions": [valid["decisions"][0], valid["decisions"][0]]},
            {**valid, "decisions": [valid["decisions"][0], {**valid["decisions"][1], "outcome": "unknown"}]},
            {**valid, "correction_review": {"prior_claim_assessment": "unknown", "reason_summary": "不正。"}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(LLMError):
                validate_memory_candidate_review_contract(invalid, candidate_count=2)

    def test_review_drops_one_off_fact_and_keeps_stated_preference(self) -> None:
        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        consolidator.llm = Mock()
        consolidator.llm.generate_memory_candidate_review.return_value = {"episode_review": {
            "summary_text": "今日の出来事。", "outcome_text": None,
            "open_loops": [], "reason_summary": "元の発話に一致。",
        }, "correction_review": {"prior_claim_assessment": "not_reviewed", "reason_summary": "訂正はない。"}, "decisions": [
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
            correction_targets=[],
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
        }, "correction_review": {"prior_claim_assessment": "contradicted", "reason_summary": "先の説明の誤り。"}, "decisions": [
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
                correction_targets=[{"revision_id": "revision:1", "summary_text": "訂正前。"}],
            )

    def test_review_rejects_progression_mistaken_for_correction(self) -> None:
        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        consolidator.llm = Mock()
        consolidator.llm.generate_memory_candidate_review.return_value = {
            "episode_review": {
                "summary_text": "マスターはメモを片づけて休憩に移った。",
                "outcome_text": None,
                "open_loops": [],
                "reason_summary": "現在の発話に一致。",
            },
            "correction_review": {
                "prior_claim_assessment": "consistent", "reason_summary": "作業が終わったという時間経過の報告。",
            },
            "decisions": [{"index": 0, "outcome": "drop", "reason_summary": "一時的な作業状態。"}],
        }
        interpretation = {
            "episode": {"summary_text": "休憩に移った。", "outcome_text": None, "open_loops": []},
            "candidate_memory_units": [{
                "memory_type": "fact", "summary_text": "今夜は休憩中。",
                "evidence_text": "片づけが済んだ。", "qualifiers_hint": {},
            }],
            "correction_status": "selected",
            "selected_targets": [{"revision_id": "revision:1", "memory_unit_id": "memory_unit:1"}],
        }
        selected, trace = consolidator._review_memory_candidates(
            selected_preset={"model": "mock-test"}, selected_persona={},
            input_text="メモは片づいたので、今はお茶を飲んで休んでいます。",
            recall_hint={}, interpretation_context={}, interpretation=interpretation,
            correction_targets=[{"revision_id": "revision:1", "summary_text": "メモを整理中。"}],
        )
        self.assertEqual(selected, [])
        self.assertEqual(interpretation["correction_status"], "no_correction")
        self.assertEqual(interpretation["selected_targets"], [])
        self.assertEqual(trace["correction_review"]["prior_claim_assessment"], "consistent")
        self.assertEqual(
            consolidator.llm.generate_memory_candidate_review.call_args.kwargs["review_context"]["correction_selection"]["target_candidates"][0]["revision_id"],
            "revision:1",
        )

    def test_selected_correction_requires_review(self) -> None:
        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        consolidator.llm = Mock()
        consolidator.llm.generate_memory_candidate_review.return_value = {
            "episode_review": {
                "summary_text": "訂正の発話。", "outcome_text": None,
                "open_loops": [], "reason_summary": "発話に一致。",
            },
            "correction_review": {"prior_claim_assessment": "not_reviewed", "reason_summary": "未審査。"},
            "decisions": [],
        }
        with self.assertRaisesRegex(ValueError, "did not review selected correction"):
            consolidator._review_memory_candidates(
                selected_preset={}, selected_persona={}, input_text="訂正します。",
                recall_hint={}, interpretation_context={}, interpretation={
                    "episode": {"summary_text": "訂正の発話。", "outcome_text": None, "open_loops": []},
                    "candidate_memory_units": [], "correction_status": "selected",
                    "selected_targets": [{"revision_id": "revision:1", "memory_unit_id": "memory_unit:1"}],
                },
                correction_targets=[{"revision_id": "revision:1", "summary_text": "訂正前。"}],
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
            "correction_review": {"prior_claim_assessment": "not_reviewed", "reason_summary": "訂正はない。"},
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
            correction_targets=[],
        )

        self.assertEqual(selected, [])
        self.assertEqual(trace["reviewed_count"], 0)
        self.assertTrue(trace["episode_review"]["changed"])
        self.assertEqual(interpretation["episode"]["summary_text"], "マスターは今夜、窓を開けて換気しようかと考えた。")


if __name__ == "__main__":
    unittest.main()
