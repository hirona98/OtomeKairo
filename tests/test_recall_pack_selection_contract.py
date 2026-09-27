from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from otomekairo.llm.client import LLMClient
from otomekairo.llm.contexts import PersonaContext
from otomekairo.llm.contracts import LLMError, validate_recall_pack_selection_contract


def _source_pack(*, conflicts: list[dict] | None = None) -> dict:
    return {
        "candidate_sections": [
            {
                "section_name": "active_topics",
                "candidates": [
                    {
                        "candidate_ref": "candidate:active_topics:1",
                        "summary_text": "値を旧から新へ訂正した。",
                    }
                ],
            }
        ],
        "conflicts": [] if conflicts is None else conflicts,
    }


class RecallPackSelectionContractTests(unittest.TestCase):
    def test_empty_conflicts_repair_rejects_candidate_ref_then_accepts_empty_array(self) -> None:
        pack = _source_pack()
        initial = {
            "section_selection": [
                {"section_name": "active_topics", "candidate_refs": ["candidate:active_topics:1"]}
            ],
            "conflict_summaries": [
                {"conflict_ref": "candidate:active_topics:1", "summary_text": "旧値と新値が競合する。"}
            ],
        }
        repaired = {**initial, "conflict_summaries": []}
        calls: list[dict] = []

        def complete(**kwargs):
            calls.append(kwargs)
            return json.dumps(initial if len(calls) == 1 else repaired, ensure_ascii=False)

        persona = PersonaContext(
            display_name="Test",
            persona_prompt_text="テスト人格。",
            expression_addon=None,
            use_policy="テスト判断に使う。",
        )
        with patch("otomekairo.llm.client.complete_text", side_effect=complete):
            result = LLMClient().generate_recall_pack_selection(
                model_config={"model": "openai/gpt-4o"},
                persona_context=persona,
                source_pack=pack,
            )

        self.assertEqual(result, repaired)
        self.assertEqual(len(calls), 2)
        self.assertIn("source_pack.conflicts は空", calls[1]["messages"][-1]["content"])
        self.assertIn("conflict_summaries は空配列 []", calls[1]["messages"][-1]["content"])

    def test_nonempty_conflicts_require_only_their_refs(self) -> None:
        pack = _source_pack(conflicts=[{"conflict_ref": "conflict:1"}])
        payload = {"section_selection": [], "conflict_summaries": []}
        with self.assertRaisesRegex(LLMError, "conflict:1"):
            validate_recall_pack_selection_contract(payload, source_pack=pack)

        payload["conflict_summaries"] = [
            {"conflict_ref": "conflict:1", "summary_text": "同じ対象に異なる理解がある。"}
        ]
        validate_recall_pack_selection_contract(payload, source_pack=pack)

        payload["conflict_summaries"][0]["conflict_ref"] = "candidate:active_topics:1"
        with self.assertRaisesRegex(LLMError, "今回の有効な conflict_ref: conflict:1"):
            validate_recall_pack_selection_contract(payload, source_pack=pack)


if __name__ == "__main__":
    unittest.main()
