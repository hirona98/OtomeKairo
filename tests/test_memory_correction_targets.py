from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from otomekairo.defaults import build_default_state
from otomekairo.llm.client import LLMClient
from otomekairo.recall.builder import RecallBuilder
from otomekairo.store.file_store import FileStore


class MemoryCorrectionTargetTests(unittest.TestCase):
    def test_recalled_memory_remains_correctable_after_recent_cycle_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FileStore(Path(directory))
            memory_set_id = "memory_set:default"
            old_cycle_id = "cycle:old"
            memory_unit_id = "memory_unit:old"
            with sqlite3.connect(store.memory_db_path) as conn:
                for index in range(7):
                    cycle_id = old_cycle_id if index == 0 else f"cycle:later-{index}"
                    stamp = f"2026-09-28T09:0{index}:00+09:00"
                    conn.execute(
                        """INSERT INTO cycle_summaries
                        (cycle_id, server_id, trigger_kind, started_at, finished_at,
                         selected_persona_id, selected_memory_set_id, selected_model_preset_id,
                         result_kind, failed, payload_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (cycle_id, "server:test", "user_message", stamp, stamp,
                         "persona:test", memory_set_id, "model:test", "speech", 0, "{}"),
                    )
                unit = {
                    "memory_unit_id": memory_unit_id,
                    "memory_set_id": memory_set_id,
                    "memory_type": "preference",
                    "scope_type": "entity",
                    "scope_key": "person:master",
                    "subject_ref": "person:master",
                    "predicate": "prefers",
                    "object_ref_or_value": "黒豆茶",
                    "summary_text": "マスターは夜に黒豆茶を好む。",
                    "status": "confirmed",
                    "confidence": 0.8,
                    "salience": 0.8,
                    "evidence_cycle_ids": [old_cycle_id],
                }
                conn.execute(
                    """INSERT INTO memory_units
                    (memory_unit_id, memory_set_id, memory_type, scope_type, scope_key,
                     subject_ref, predicate, object_ref_or_value, summary_text, status,
                     confidence, salience, formed_at, evidence_event_ids_json,
                     qualifiers_json, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (memory_unit_id, memory_set_id, "preference", "entity", "person:master",
                     "person:master", "prefers", "黒豆茶", unit["summary_text"], "confirmed",
                     0.8, 0.8, "2026-09-28T09:00:00+09:00", "[]", "{}",
                     json.dumps(unit, ensure_ascii=False)),
                )
                revision = {
                    "revision_id": "revision:old",
                    "memory_set_id": memory_set_id,
                    "memory_unit_id": memory_unit_id,
                    "operation": "create",
                    "evidence_event_ids": [],
                    "related_memory_unit_ids": [],
                    "reason": "本人が好みを述べた。",
                }
                conn.execute(
                    """INSERT INTO revisions
                    (revision_id, memory_set_id, memory_unit_id, occurred_at, operation,
                     related_memory_unit_ids_json, reason, evidence_event_ids_json, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("revision:old", memory_set_id, memory_unit_id,
                     "2026-09-28T09:00:00+09:00", "create", "[]", revision["reason"], "[]",
                     json.dumps(revision, ensure_ascii=False)),
                )

            params = {
                "memory_set_id": memory_set_id,
                "before_finished_at": "2026-09-28T10:00:00+09:00",
                "exclude_cycle_id": "cycle:current",
                "cycle_limit": 6,
                "limit": 12,
            }
            self.assertEqual(
                store.list_recent_memory_revision_targets_for_correction(
                    **params, candidate_memory_unit_ids=[],
                ),
                [],
            )
            targets = store.list_recent_memory_revision_targets_for_correction(
                **params, candidate_memory_unit_ids=[memory_unit_id],
            )
            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0]["memory_unit"]["memory_unit_id"], memory_unit_id)
            self.assertEqual(targets[0]["source_cycle_ids"], [old_cycle_id])

            # 返答用の採用がゼロでも、検索された旧記憶は訂正審査へ渡せる。
            state = build_default_state()
            state["selected_memory_set_id"] = memory_set_id
            state["model_presets"][state["selected_model_preset_id"]]["model"] = "mock"
            embedding = state["memory_sets"][memory_set_id]["embedding"]
            embedding["model"] = "mock"
            llm = LLMClient()
            with patch.object(LLMClient, "generate_recall_pack_selection", return_value={
                "section_selection": [], "conflict_summaries": [],
            }):
                recall = RecallBuilder(store=store, llm=llm).build_recall_pack(
                    state=state, augmented_query_text="飲み物の好みを訂正する。",
                    recall_hint={
                        "primary_recall_focus": "preference", "secondary_recall_focuses": [],
                        "focus_scopes": [], "mentioned_entities": [], "mentioned_topics": [],
                    }, current_person_ref="person:master",
                    current_time=params["before_finished_at"],
                )
            self.assertEqual(recall["selected_memory_ids"], [])
            self.assertIn(memory_unit_id, recall["retrieved_memory_ids"])
            targets = store.list_recent_memory_revision_targets_for_correction(
                **params, candidate_memory_unit_ids=recall["retrieved_memory_ids"],
            )
            self.assertEqual([target["revision"]["revision_id"] for target in targets], ["revision:old"])

            # 訂正後は recalled 経由でも直近履歴経由でも古い create を再提示しない。
            revision.update({"revision_id": "revision:corrected", "operation": "correct", "corrects_revision_id": "revision:old"})
            with sqlite3.connect(store.memory_db_path) as conn:
                conn.execute(
                    """INSERT INTO revisions
                    (revision_id, memory_set_id, memory_unit_id, occurred_at, operation,
                     related_memory_unit_ids_json, reason, evidence_event_ids_json, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("revision:corrected", memory_set_id, memory_unit_id,
                     "2026-09-28T09:07:00+09:00", "correct", "[]", "訂正済み。", "[]",
                     json.dumps(revision, ensure_ascii=False)),
                )
            params["cycle_limit"] = 10
            for recalled in ([], [memory_unit_id]):
                with self.subTest(recalled=recalled):
                    self.assertEqual(store.list_recent_memory_revision_targets_for_correction(
                        **params, candidate_memory_unit_ids=recalled,
                    ), [])


if __name__ == "__main__":
    unittest.main()
