from __future__ import annotations

import json
import unittest

from otomekairo.llm.contexts import PersonaContext
from otomekairo.llm.contracts import validate_recall_pack_selection_contract
from otomekairo.recall.selection import RecallSelectionMixin


class _RecallSelectionHarness(RecallSelectionMixin):
    def _record_id(self, item: dict) -> str:
        return str(item.get("memory_unit_id") or item["episode_id"])


class RecallSelectionSourceTests(unittest.TestCase):
    def test_compact_source_refs_restore_original_candidates(self) -> None:
        selector = _RecallSelectionHarness()
        memory = {
            "source_kind": "memory_unit",
            "memory_unit_id": "memory_unit:1",
            "summary_text": "続けて話す約束が残っている。",
            "salience": 0.9,
            "retrieval_lane": "structured",
            "memory_type": "commitment",
            "scope_type": "relationship",
            "scope_key": "self|person:test",
            "status": "active",
            "commitment_state": "open",
            "memory_link_summary": {
                "label_counts": {"supports": 1},
                "representative_links": [
                    {
                        "label": "supports",
                        "direction": "incoming",
                        "summary_text": "supports/incoming: 続きを話す約束がある。",
                        "related_summary_text": "続きを話す約束がある。",
                    }
                ],
            },
        }
        episode = {
            "source_kind": "episode",
            "episode_id": "episode:1",
            "summary_text": "前回は続きを後日に回した。",
            "salience": 0.8,
            "retrieval_lane": "association",
            "association_score": 0.75,
            "primary_scope_type": "relationship",
            "primary_scope_key": "self|person:test",
            "open_loops": ["続きを確認する"],
            "outcome_text": "後日に続けることになった。",
        }
        conflict = {
            "source_kind": "conflict",
            "compare_key": {
                "memory_type": "commitment",
                "scope_type": "relationship",
                "scope_key": "self|person:test",
                "subject_ref": "self",
                "predicate": "talk_again",
            },
            "variant_summaries": ["続きを話す。", "いったん区切る。"],
            "summary_text": "理解が並んでいる。",
        }
        candidate_sections = {
            "active_commitments": [memory],
            "episodic_evidence": [episode],
        }

        source_pack = selector._build_recall_pack_selection_source_pack(
            augmented_query_text="前回の続き",
            recall_hint={"primary_recall_focus": "commitment"},
            candidate_sections=candidate_sections,
            conflicts=[conflict],
            persona_context=PersonaContext(
                display_name="Test",
                initiative_baseline={"level": "medium", "summary_text": "test"},
                persona_prompt_text="テスト人格。",
                expression_addon=None,
                use_policy="候補の優先順位だけに使う。",
            ),
        )
        selection = {
            "section_selection": [
                {"section_name": "active_commitments", "candidate_refs": ["c1"]},
                {"section_name": "episodic_evidence", "candidate_refs": ["c2"]},
            ],
            "conflict_summaries": [
                {"conflict_ref": "x1", "summary_text": "続ける理解と区切る理解が並んでいる。"}
            ],
        }

        validate_recall_pack_selection_contract(selection, source_pack=source_pack)
        restored = selector._apply_recall_pack_selection(
            payload=selection,
            source_pack=source_pack,
            candidate_sections=candidate_sections,
            conflicts=[conflict],
        )

        compact_json = json.dumps(
            {
                "candidate_sections": source_pack["candidate_sections"],
                "conflicts": source_pack["conflicts"],
            },
            ensure_ascii=False,
        )
        old_shape_json = json.dumps(
            {
                "candidate_sections": [
                    {
                        "section_name": "active_commitments",
                        "candidates": [
                            {
                                "candidate_ref": "candidate:active_commitments:1",
                                **memory,
                            }
                        ],
                    },
                    {
                        "section_name": "episodic_evidence",
                        "candidates": [
                            {
                                "candidate_ref": "candidate:episodic_evidence:1",
                                **episode,
                            }
                        ],
                    },
                ],
                "conflicts": [{"conflict_ref": "conflict:1", **conflict}],
            },
            ensure_ascii=False,
        )

        self.assertLess(len(compact_json), len(old_shape_json))
        self.assertNotIn("candidate_ref", compact_json)
        self.assertNotIn("source_kind", compact_json)
        self.assertNotIn('"lane": "structured"', compact_json)
        self.assertIn('"examples": [["supports", "incoming", "続きを話す約束がある。"]]', compact_json)
        self.assertNotIn("related_summary_text", compact_json)
        self.assertEqual(restored["sections"]["active_commitments"], [memory])
        self.assertEqual(restored["sections"]["episodic_evidence"], [episode])
        self.assertEqual(restored["sections"]["conflicts"][0]["summary_text"], selection["conflict_summaries"][0]["summary_text"])


if __name__ == "__main__":
    unittest.main()
