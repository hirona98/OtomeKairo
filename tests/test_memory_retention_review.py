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


if __name__ == "__main__":
    unittest.main()
