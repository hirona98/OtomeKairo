import tempfile
import unittest
from pathlib import Path

from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.store.file_store import FileStore


class MemoryReplacementTests(unittest.TestCase):
    def test_replace_prior_closes_parallel_old_units_for_existing_and_new_target(self) -> None:
        for target_object in ("food:tea-b", "food:tea-c"):
            with self.subTest(target_object=target_object), tempfile.TemporaryDirectory() as directory:
                store = FileStore(Path(directory))
                resolver = MemoryActionResolver(store=store)
                common = {"memory_set_id": "memory_set:default", "event_ids": [], "cycle_ids": []}

                def candidate(object_ref: str, **hints: bool) -> dict:
                    return {
                        "memory_type": "preference", "scope_type": "entity",
                        "scope_key": "person:test", "subject_ref": "person:test",
                        "predicate": "favorite_drink", "object_ref_or_value": object_ref,
                        "summary_text": f"飲み物の好みは {object_ref}。",
                        "status": "confirmed", "commitment_state": None,
                        "confidence": 0.8, "salience": 0.8, "valid_from": None, "valid_to": None,
                        "qualifiers": {"source": "explicit_statement", **hints}, "reason": "本人の報告。",
                    }

                for index, object_ref in enumerate(("food:tea-a", "food:tea-b")):
                    store.persist_memory_actions(memory_actions=resolver.resolve_memory_actions(
                        **common, finished_at=f"2026-10-03T10:0{index}:00+09:00", candidate=candidate(object_ref),
                    ))
                comparison = {
                    "memory_set_id": common["memory_set_id"], "memory_type": "preference",
                    "scope_type": "entity", "scope_key": "person:test",
                    "subject_ref": "person:test", "predicate": "favorite_drink",
                }
                self.assertEqual(len(store.find_memory_units_for_compare(**comparison)), 2)
                stamp = "2026-10-03T10:10:00+09:00"
                store.persist_memory_actions(memory_actions=resolver.resolve_memory_actions(
                    **common, finished_at=stamp, candidate=candidate(target_object, replace_prior=True),
                ))
                units = store.find_memory_units_for_compare(**comparison)
                active = [unit for unit in units if unit["status"] == "confirmed"]
                self.assertEqual([unit["object_ref_or_value"] for unit in active], [target_object])
                for unit in units:
                    if unit["object_ref_or_value"] != target_object:
                        self.assertEqual(unit["status"], "superseded")
                        self.assertEqual(unit["valid_to"], stamp)
                # 過去に保存した置換ヒントを、今回の独立した追加へ持ち越さない。
                store.persist_memory_actions(memory_actions=resolver.resolve_memory_actions(
                    **common, finished_at="2026-10-03T10:11:00+09:00",
                    candidate=candidate("food:tea-d", allow_parallel=True),
                ))
                active = [unit for unit in store.find_memory_units_for_compare(**comparison) if unit["status"] == "confirmed"]
                self.assertEqual({unit["object_ref_or_value"] for unit in active}, {target_object, "food:tea-d"})
