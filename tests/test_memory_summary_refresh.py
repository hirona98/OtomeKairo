from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.store.file_store import FileStore


class MemorySummaryRefreshTests(unittest.TestCase):
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
                "object_ref_or_value": "entity:person:test:summary",
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
