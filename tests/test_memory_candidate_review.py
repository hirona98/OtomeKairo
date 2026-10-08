from __future__ import annotations

import unittest
from unittest.mock import Mock

from otomekairo.llm.contracts import LLMError, validate_memory_candidate_review_contract
from otomekairo.memory.consolidator import MemoryConsolidator
from otomekairo.memory.correction import MemoryCorrectionReconciler


class MemoryCandidateReviewTests(unittest.TestCase):
    def test_review_contract_requires_one_decision_per_candidate(self) -> None:
        episode_review = {
            "summary_text": "今日の出来事。", "outcome_text": None,
            "open_loops": [], "reason_summary": "元の発話に一致。",
        }
        valid = {"episode_review": episode_review, "correction_review": {
            "prior_claim_assessment": "not_reviewed", "contradicted_revision_ids": [], "replacement_candidate_indices": [], "reason_summary": "訂正はない。",
        }, "decisions": [
            {"index": 0, "retention_basis": "current_episode", "reason_summary": "一度の出来事。"},
            {"index": 1, "retention_basis": "explicit_pattern", "reason_summary": "普段の好み。"},
        ]}
        validate_memory_candidate_review_contract(valid, candidate_count=2)
        for invalid in (
            {**valid, "decisions": valid["decisions"][:1]},
            {**valid, "decisions": [valid["decisions"][0], valid["decisions"][0]]},
            {**valid, "decisions": [valid["decisions"][0], {**valid["decisions"][1], "retention_basis": "unknown"}]},
            {**valid, "correction_review": {"prior_claim_assessment": "unknown", "contradicted_revision_ids": [], "replacement_candidate_indices": [], "reason_summary": "不正。"}},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(LLMError):
                validate_memory_candidate_review_contract(invalid, candidate_count=2)

    def test_correction_review_requires_an_offered_revision(self) -> None:
        payload = {
            "episode_review": {"summary_text": "窓について訂正した。", "outcome_text": None, "open_loops": [], "reason_summary": "本人の訂正。"},
            "decisions": [],
            "correction_review": {"prior_claim_assessment": "contradicted", "contradicted_revision_ids": ["revision:window"], "replacement_candidate_indices": [], "reason_summary": "窓についての過去の理解が誤り。"},
        }
        with self.assertRaisesRegex(LLMError, "提示された revision_id"):
            validate_memory_candidate_review_contract(payload, candidate_count=0, target_revision_ids={"revision:drink"})
        validate_memory_candidate_review_contract(payload, candidate_count=0, target_revision_ids={"revision:window"})

    def test_episode_only_correction_does_not_suppress_unrelated_memory(self) -> None:
        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        consolidator.llm = Mock()
        episode_review = {"summary_text": "窓の発言を訂正し、信頼を伝えた。", "outcome_text": None, "open_loops": [], "reason_summary": "本人の発話に一致。"}
        consolidator.llm.generate_memory_candidate_review.return_value = {
            "episode_review": episode_review,
            "correction_review": {"prior_claim_assessment": "not_reviewed", "contradicted_revision_ids": [], "replacement_candidate_indices": [], "reason_summary": "窓の訂正対象となる長期記憶 revision はない。"},
            "decisions": [{"index": 0, "retention_basis": "explicit_pattern", "reason_summary": "現在も続く信頼関係。"}],
        }
        candidate = {"memory_type": "relationship", "summary_text": "対等な相談相手として信頼している。", "evidence_text": "信頼している。", "qualifiers_hint": {}}
        interpretation = {"episode": {"summary_text": "会話した。", "outcome_text": None, "open_loops": []}, "candidate_memory_units": [candidate], "correction_status": "no_correction", "selected_targets": []}
        selected, trace = consolidator._review_memory_candidates(
            decision={}, speech_text=None,
            selected_preset={}, selected_persona={}, input_text="窓を開けたというのは誤りだった。レイカを信頼している。",
            recall_hint={}, interpretation_context={}, interpretation=interpretation,
            correction_targets=[{"revision_id": "revision:drink", "summary_text": "麦茶を好む。"}],
        )
        self.assertEqual(selected, [candidate])
        self.assertFalse(trace["correction_selection_missed"])
        self.assertEqual(interpretation["episode"]["summary_text"], episode_review["summary_text"])

    def test_review_drops_one_off_fact_and_keeps_stated_preference(self) -> None:
        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        consolidator.llm = Mock()
        consolidator.llm.generate_memory_candidate_review.return_value = {"episode_review": {
            "summary_text": "今日の出来事。", "outcome_text": None,
            "open_loops": [], "reason_summary": "元の発話に一致。",
        }, "correction_review": {"prior_claim_assessment": "not_reviewed", "contradicted_revision_ids": [], "replacement_candidate_indices": [], "reason_summary": "訂正はない。"}, "decisions": [
            {"index": 0, "retention_basis": "current_episode", "reason_summary": "一度の仕事の出来事。"},
            {"index": 1, "retention_basis": "explicit_pattern", "reason_summary": "普段のお茶の好み。"},
        ]}
        candidates = [
            {"memory_type": "fact", "summary_text": "今日は段取りを誤った。", "evidence_text": "今日のこと。", "qualifiers_hint": {}},
            {"memory_type": "preference", "summary_text": "普段はほうじ茶を飲む。", "evidence_text": "普段のお茶。", "qualifiers_hint": {}},
        ]

        selected, trace = consolidator._review_memory_candidates(
            decision={}, speech_text=None,
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
        }, "correction_review": {"prior_claim_assessment": "contradicted", "contradicted_revision_ids": ["revision:1"], "replacement_candidate_indices": [0], "reason_summary": "先の説明の誤り。"}, "decisions": [
            {"index": 0, "retention_basis": "current_episode", "reason_summary": "根拠がない。"},
        ]}
        with self.assertRaisesRegex(ValueError, "during correction selection"):
            consolidator._review_memory_candidates(
                decision={}, speech_text=None,
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
                    "selected_targets": [{"revision_id": "revision:1", "memory_unit_id": "memory_unit:1"}],
                },
                correction_targets=[{"revision_id": "revision:1", "summary_text": "訂正前。"}],
            )

    def test_unrelated_episode_candidate_does_not_block_selected_correction(self) -> None:
        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        consolidator.llm = Mock()
        episode = {"summary_text": "好みの訂正と窓の報告。", "outcome_text": None, "open_loops": []}
        consolidator.llm.generate_memory_candidate_review.return_value = {
            "episode_review": {**episode, "reason_summary": "本人の発話に一致。"},
            "correction_review": {"prior_claim_assessment": "contradicted", "contradicted_revision_ids": ["revision:drink"], "replacement_candidate_indices": [1], "reason_summary": "訂正対象は飲み物の好みだけ。"},
            "decisions": [
                {"index": 0, "retention_basis": "current_episode", "reason_summary": "窓の開閉は今の状態。"},
                {"index": 1, "retention_basis": "explicit_pattern", "reason_summary": "本人が普段の好みを訂正した。"},
            ],
        }
        candidates = [
            {"memory_type": "fact", "summary_text": "窓は閉まっている。", "evidence_text": "今は閉まっている。", "qualifiers_hint": {}},
            {"memory_type": "preference", "summary_text": "普段はほうじ茶が好き。", "evidence_text": "本当はほうじ茶が好き。", "qualifiers_hint": {}},
        ]
        interpretation = {"episode": episode, "candidate_memory_units": candidates, "correction_status": "selected", "selected_targets": [{"revision_id": "revision:drink", "memory_unit_id": "memory_unit:drink"}]}
        selected, trace = consolidator._review_memory_candidates(
            decision={}, speech_text=None,
            selected_preset={}, selected_persona={}, input_text="窓は閉まっている。好きな飲み物は麦茶というのは間違いで、本当はほうじ茶だ。",
            recall_hint={}, interpretation_context={}, interpretation=interpretation,
            correction_targets=[{"revision_id": "revision:drink", "summary_text": "麦茶が好き。"}],
        )
        self.assertEqual(selected, candidates[1:])
        self.assertFalse(trace["correction_selection_missed"])
        self.assertEqual(interpretation["correction_status"], "selected")

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
                "prior_claim_assessment": "consistent", "contradicted_revision_ids": [], "replacement_candidate_indices": [], "reason_summary": "作業が終わったという時間経過の報告。",
            },
            "decisions": [{"index": 0, "retention_basis": "current_episode", "reason_summary": "一時的な作業状態。"}],
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
            decision={}, speech_text=None,
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
            "correction_review": {"prior_claim_assessment": "not_reviewed", "contradicted_revision_ids": [], "replacement_candidate_indices": [], "reason_summary": "未審査。"},
            "decisions": [],
        }
        with self.assertRaisesRegex(ValueError, "did not review selected correction"):
            consolidator._review_memory_candidates(
                decision={}, speech_text=None,
                selected_preset={}, selected_persona={}, input_text="訂正します。",
                recall_hint={}, interpretation_context={}, interpretation={
                    "episode": {"summary_text": "訂正の発話。", "outcome_text": None, "open_loops": []},
                    "candidate_memory_units": [], "correction_status": "selected",
                    "selected_targets": [{"revision_id": "revision:1", "memory_unit_id": "memory_unit:1"}],
                },
                correction_targets=[{"revision_id": "revision:1", "summary_text": "訂正前。"}],
            )

    def test_review_preserves_episode_when_interpretation_missed_correction(self) -> None:
        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        consolidator.llm = Mock()
        consolidator.llm.generate_memory_candidate_review.return_value = {
            "episode_review": {
                "summary_text": "先の飲み物の説明を訂正した。", "outcome_text": None,
                "open_loops": [], "reason_summary": "本人が先の説明の誤りを明示した。",
            },
            "correction_review": {
                "prior_claim_assessment": "contradicted", "contradicted_revision_ids": ["revision:1"], "replacement_candidate_indices": [], "reason_summary": "先の主張は当時から誤り。",
            },
            "decisions": [{"index": 0, "retention_basis": "explicit_pattern", "reason_summary": "継続理解。"}],
        }
        interpretation = {
            "episode": {"summary_text": "飲み物の話。", "outcome_text": None, "open_loops": []},
            "candidate_memory_units": [{
                "memory_type": "fact", "summary_text": "訂正後の理解。",
                "evidence_text": "本人が訂正した。", "qualifiers_hint": {},
            }],
            "correction_status": "no_correction", "selected_targets": [],
        }
        selected, trace = consolidator._review_memory_candidates(
            decision={}, speech_text=None,
            selected_preset={}, selected_persona={}, input_text="さっきの説明は間違いだった。",
            recall_hint={}, interpretation_context={}, interpretation=interpretation,
            correction_targets=[{"revision_id": "revision:1", "summary_text": "訂正前の飲み物の理解。"}],
        )
        self.assertEqual(selected, [])
        self.assertTrue(trace["correction_selection_missed"])
        self.assertEqual(trace["suppressed_count"], 1)
        self.assertEqual(interpretation["episode"]["summary_text"], "先の飲み物の説明を訂正した。")
        context = consolidator._build_correction_job_context(
            input_text="さっきの説明は間違いだった。", speech_payload=None,
            decision={"reason_summary": "訂正を受け止める。"}, event_ids=[], cycle_id="cycle:1",
            prepared={"targets": [{"revision": {"revision_id": "revision:1"}}]}, interpretation=interpretation, candidate_review_trace=trace,
        )
        self.assertIsNotNone(context)
        actions, result = MemoryCorrectionReconciler.__new__(MemoryCorrectionReconciler).run(
            context=context, finished_at="2026-09-28T12:00:00+09:00",
        )
        self.assertEqual(actions, [])
        self.assertEqual(result["result_status"], "failed")
        self.assertEqual(result["failure_reason"], "記憶候補審査が過去の主張の誤りを認めましたが、記憶解釈は訂正対象を選定しませんでした。")

    def test_review_allows_revocation_without_new_memory_candidate(self) -> None:
        consolidator = MemoryConsolidator.__new__(MemoryConsolidator)
        consolidator.llm = Mock()
        consolidator.llm.generate_memory_candidate_review.return_value = {
            "episode_review": {
                "summary_text": "先の好みを訂正した。", "outcome_text": None,
                "open_loops": [], "reason_summary": "本人の訂正に一致。",
            },
            "correction_review": {
                "prior_claim_assessment": "contradicted", "contradicted_revision_ids": ["revision:1"], "replacement_candidate_indices": [], "reason_summary": "以前の習慣という説明は誤り。",
            },
            "decisions": [{"index": 0, "retention_basis": "current_episode", "reason_summary": "一度だけの飲用。"}],
        }
        selected, trace = consolidator._review_memory_candidates(
            decision={}, speech_text=None,
            selected_preset={}, selected_persona={}, input_text="昨日初めて飲んだだけ。",
            recall_hint={}, interpretation_context={}, interpretation={
                "episode": {"summary_text": "訂正。", "outcome_text": None, "open_loops": []},
                "candidate_memory_units": [{
                    "memory_type": "fact", "summary_text": "昨日初めて飲んだ。",
                    "evidence_text": "本人の発話。", "qualifiers_hint": {},
                }],
                "correction_status": "selected",
                "selected_targets": [{
                    "revision_id": "revision:1", "memory_unit_id": "memory_unit:1",
                    "reason_summary": "誤作成。",
                }],
            },
            correction_targets=[{"revision_id": "revision:1", "summary_text": "毎晩飲む。"}],
        )
        self.assertEqual(selected, [])
        self.assertEqual(trace["dropped_count"], 1)
        self.assertFalse(trace["correction_selection_missed"])

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
            "correction_review": {"prior_claim_assessment": "not_reviewed", "contradicted_revision_ids": [], "replacement_candidate_indices": [], "reason_summary": "訂正はない。"},
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
            decision={}, speech_text=None,
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
