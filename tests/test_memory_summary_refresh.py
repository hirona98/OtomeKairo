from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from otomekairo.defaults import build_default_state
from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.memory.consolidator import MemoryConsolidator
from otomekairo.memory.reflection.summary import MemoryReflectionSummaryMixin
from otomekairo.store.file_store import FileStore


class MemorySummaryRefreshTests(unittest.TestCase):
    def test_reflection_summary_has_no_synthetic_entity_object(self) -> None:
        reflection = MemoryReflectionSummaryMixin()
        candidate = reflection._build_reflective_summary_candidate(
            scope_type="entity", scope_key="person:test", summary_text="普段はほうじ茶が好き。",
            evidence_pack={"dominant_memory_types": ["preference"], "summary_status_candidate": "inferred",
                           "evidence_counts": {"episodes": 2, "memory_units": 1, "support_cycles": 2, "open_loops": 0}},
        )
        self.assertEqual(candidate["subject_ref"], "person:test")
        self.assertIsNone(candidate["object_ref_or_value"])

    def test_summary_evidence_preserves_memory_validity_and_confirmation_times(self) -> None:
        reflection = MemoryReflectionSummaryMixin()
        unit = {
            "memory_type": "preference", "predicate": "usual_drink",
            "object_ref_or_value": "ほうじ茶", "summary_text": "当時の好みはほうじ茶。",
            "status": "superseded", "confidence": 0.8, "salience": 0.5,
            "formed_at": "2026-10-03T23:00:00Z",
            "last_confirmed_at": "2026-10-04T09:00:32+09:00",
            "valid_from": "2026-10-01T00:00:00+09:00",
            "valid_to": "2026-10-04T09:00:36+09:00",
        }
        item = reflection._summary_pack_memory_item(unit)
        self.assertEqual(item["formed_time_label"], "2026年10月4日 8時00分")
        self.assertEqual(item["last_confirmed_time_label"], "2026年10月4日 9時00分")
        self.assertEqual(item["valid_from_time_label"], "2026年10月1日 0時00分")
        self.assertEqual(item["valid_to_time_label"], "2026年10月4日 9時00分")
        self.assertEqual(item["status"], "superseded")
        current = reflection._summary_pack_memory_item({
            **unit, "object_ref_or_value": "麦茶", "status": "confirmed",
            "valid_from": None, "valid_to": None,
        })
        self.assertIsNone(current["valid_from_time_label"])
        self.assertIsNone(current["valid_to_time_label"])
        self.assertEqual(current["status"], "confirmed")

    def test_correction_refreshes_existing_summary_below_new_summary_evidence_floor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            llm = Mock()
            llm.generate_memory_reflection_summary.return_value = {"summaries": [
                {"scope_ref": "scope:0", "summary_text": "本人の訂正後の好みはほうじ茶。"},
            ]}
            reflection = MemoryConsolidator(store=FileStore(Path(directory)), llm=llm).reflective
            reflection.action_resolver = Mock()
            reflection.action_resolver.resolve_memory_actions.return_value = []
            scope = {"scope_type": "entity", "scope_key": "person:test"}
            unit = {**scope, "memory_unit_id": "memory_unit:drink", "memory_type": "preference",
                    "summary_text": "普段はほうじ茶。", "evidence_cycle_ids": ["cycle:correction"]}
            summary = {**scope, "memory_unit_id": "memory_unit:summary", "memory_type": "summary",
                       "summary_text": "訂正前の誤った好み。"}
            state = build_default_state()
            params = dict(memory_set_id="memory_set:default", finished_at="2026-10-04T09:00:00+09:00",
                          episodes=[], embedding_definition={"model": "mock", "embedding_dimension": 8},
                          reflection_summary_model_config={"model": "mock"},
                          selected_persona=state["personas"][state["selected_persona_id"]],
                          scope_support_index={}, memory_actions=[{"operation": "correct", "memory_unit": unit}])
            _, trace = reflection._build_reflective_summary_actions(active_units=[unit, summary], **params)
            self.assertEqual(trace["requested_scope_count"], 1)
            self.assertEqual(trace["succeeded_scope_count"], 1)
            source = llm.generate_memory_reflection_summary.call_args.kwargs["source_pack"]
            self.assertEqual(source["scopes"][0]["evidence_counts"]["memory_units"], 1)
            llm.reset_mock()
            _, trace = reflection._build_reflective_summary_actions(active_units=[unit], **params)
            self.assertEqual(trace["requested_scope_count"], 0)
            llm.generate_memory_reflection_summary.assert_not_called()

    def test_regenerated_summary_replaces_old_text_with_same_scope_and_evidence_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FileStore(Path(directory))
            resolver = MemoryActionResolver(store=store)
            candidate = {
                "memory_type": "summary",
                "scope_type": "entity",
                "scope_key": "person:test",
                "subject_ref": "person:test",
                "predicate": "long_term_pattern",
                "object_ref_or_value": None,
                "summary_text": "普段は無糖のほうじ茶を好み、率直な対話を望む。",
                "status": "inferred",
                "commitment_state": None,
                "confidence": 0.7,
                "salience": 0.7,
                "valid_from": None,
                "valid_to": None,
                "qualifiers": {"summary_scope": "entity", "evidence_memory_count": 3},
                "reason": "内省で現在の根拠を要約した。",
            }
            common = {"memory_set_id": "memory_set:default", "allow_summary": True}
            created = resolver.resolve_memory_actions(
                **common, finished_at="2026-10-03T10:00:00+09:00",
                event_ids=["event:before"], cycle_ids=["cycle:before"], candidate=candidate,
            )
            store.persist_memory_actions(memory_actions=created)
            corrected_text = "普段は無糖のジャスミン茶を好み、率直な対話を望む。"
            updated = resolver.resolve_memory_actions(
                **common, finished_at="2026-10-03T10:10:00+09:00",
                event_ids=["event:after"], cycle_ids=["cycle:after"],
                candidate={**candidate, "summary_text": corrected_text},
            )
            self.assertEqual(len(updated), 1)
            self.assertEqual(updated[0]["operation"], "refine")
            self.assertEqual(updated[0]["memory_unit_id"], created[0]["memory_unit_id"])
            self.assertEqual(updated[0]["before_snapshot"]["summary_text"], candidate["summary_text"])
            store.persist_memory_actions(memory_actions=updated)
            stored = store.find_memory_units_for_compare(
                memory_set_id=common["memory_set_id"], memory_type="summary",
                scope_type="entity", scope_key="person:test", subject_ref="person:test",
                predicate="long_term_pattern",
            )
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["summary_text"], corrected_text)
            self.assertEqual(stored[0]["evidence_event_ids"], ["event:before", "event:after"])
