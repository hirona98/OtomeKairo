from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from otomekairo.llm.client import LLMClient


class MemoryRetentionReviewTests(unittest.TestCase):
    def test_correction_review_cannot_override_retention_decisions(self) -> None:
        retention = {"decisions": [{"index": 0, "retention_basis": "current_episode", "reason_summary": "今の窓の状態。"}]}
        assessment = {
            "episode_review": {"summary_text": "窓の発言を訂正した。", "outcome_text": None, "open_loops": [], "reason_summary": "本人の発言。"},
            "correction_review": {"prior_claim_assessment": "not_reviewed", "contradicted_revision_ids": [], "replacement_candidate_indices": [], "reason_summary": "窓に関する長期記憶はない。"},
        }
        invalid_assessment = {**assessment, "decisions": [{"index": 0, "retention_basis": "explicit_pattern", "reason_summary": "訂正なので保存。"}]}
        context = {
            "persona_context": {}, "input_text": "窓を開けたというのは誤りで、閉めたままだった。",
            "decision": {"kind": "speech"}, "speech_text": "訂正を受け取りました。",
            "memory_context": {"events": [{"role": "user", "text": "窓は閉まっている。"}, {"role": "assistant", "text": "いつも閉めているのですね。"}]},
            "episode": {"summary_text": "訂正の会話。", "outcome_text": None, "open_loops": []},
            "candidates": [{"index": 0, "summary_text": "窓は閉まっている。"}],
            "correction_selection": {"correction_status": "no_correction", "selected_targets": [], "target_candidates": []},
        }
        with patch("otomekairo.llm.client.complete_text", side_effect=[json.dumps(payload) for payload in (retention, invalid_assessment, assessment)]) as complete:
            result = LLMClient().generate_memory_candidate_review(model_config={"model": "real-model"}, review_context=context)
        self.assertEqual(result["decisions"], retention["decisions"])
        self.assertEqual(complete.call_count, 3)
        retention_input = complete.call_args_list[0].kwargs["messages"][1]["content"]
        self.assertNotIn('"correction_selection"', retention_input)
        self.assertNotIn('"episode"', retention_input)
        self.assertNotIn('"role": "assistant"', retention_input)


    def test_self_commitment_review_receives_actual_response_and_registered_run(self) -> None:
        retention = {"decisions": [{"index": 0, "retention_basis": "future_commitment", "reason_summary": "自己が通知を引き受けて登録した。"}]}
        assessment = {
            "episode_review": {"summary_text": "一度限りの通知を引き受けた。", "outcome_text": "通知を登録した。", "open_loops": ["指定時刻の通知"], "reason_summary": "実際の応答と登録結果。"},
            "correction_review": {"prior_claim_assessment": "not_reviewed", "contradicted_revision_ids": [], "replacement_candidate_indices": [], "reason_summary": "訂正ではない。"},
        }
        decision = {"kind": "autonomous_run", "autonomous_run": {"objective_summary": "一度通知する。"}}
        speech = "指定時刻に一度お知らせします。"
        run = {"run_id": "run:notification", "status": "waiting_timer", "next_run_at": "2026-10-04T09:05:47+09:00"}
        context = {
            "persona_context": {}, "input_text": "5分後に一度知らせて。", "decision": decision, "speech_text": speech,
            "episode": {}, "candidates": [{"index": 0, "memory_type": "commitment", "qualifiers_hint": {"commitment_actor": "self"}}],
            "memory_context": {"autonomous_run_summary": run, "events": [
                {"role": "person", "text": "5分後に一度知らせて。"},
                {"role": "assistant", "text": "以前は毎日の習慣のように言い換えた。"}]},
            "correction_selection": {"correction_status": "no_correction", "selected_targets": [], "target_candidates": []},
        }
        with patch("otomekairo.llm.client.complete_text", side_effect=[json.dumps(retention), json.dumps(assessment)]) as complete:
            result = LLMClient().generate_memory_candidate_review(model_config={"model": "real-model"}, review_context=context)
        self.assertEqual(result["decisions"], retention["decisions"])
        for call in complete.call_args_list:
            payload = json.loads(call.kwargs["messages"][1]["content"].split("\n")[1])
            self.assertEqual(payload["decision"], decision)
            self.assertEqual(payload["speech_text"], speech)
        retained_evidence = json.loads(complete.call_args_list[0].kwargs["messages"][1]["content"].split("\n")[1])["evidence_context"]
        self.assertEqual(retained_evidence["autonomous_run_summary"], run)
        self.assertEqual(retained_evidence["events"], context["memory_context"]["events"][:1])


if __name__ == "__main__":
    unittest.main()
