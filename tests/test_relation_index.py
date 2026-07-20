import sqlite3
import tempfile
import unittest
from pathlib import Path

from otomekairo.service.app import OtomeKairoService
from otomekairo.store.file_store import FileStore


NOW = "2026-07-20T12:00:00+09:00"


class RelationIndexTests(unittest.TestCase):
    def test_rebuild_aggregates_supported_edges_and_skips_multi_party_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileStore(Path(temp_dir))
            memory_set_id = store.read_state()["selected_memory_set_id"]
            units = [
                self._unit(
                    memory_set_id=memory_set_id,
                    memory_unit_id="memory_unit:trust_confirmed",
                    refs=("person:tanaka", "self"),
                    predicate="trusts",
                    status="confirmed",
                    confidence=0.82,
                    salience=0.74,
                ),
                self._unit(
                    memory_set_id=memory_set_id,
                    memory_unit_id="memory_unit:trust_inferred",
                    refs=("self", "person:tanaka"),
                    predicate="trusts",
                    status="inferred",
                    confidence=0.91,
                    salience=0.63,
                ),
                self._unit(
                    memory_set_id=memory_set_id,
                    memory_unit_id="memory_unit:weak",
                    refs=("self", "person:yamada"),
                    predicate="knows",
                    status="inferred",
                ),
                self._unit(
                    memory_set_id=memory_set_id,
                    memory_unit_id="memory_unit:inactive",
                    refs=("self", "person:sato"),
                    predicate="knows",
                    status="revoked",
                ),
                self._unit(
                    memory_set_id=memory_set_id,
                    memory_unit_id="memory_unit:multi",
                    refs=("self", "person:tanaka", "person:yamada"),
                    predicate="works_with",
                ),
                self._unit(
                    memory_set_id=memory_set_id,
                    memory_unit_id="memory_unit:invalid",
                    refs=("self",),
                    predicate="knows",
                ),
                self._unit(
                    memory_set_id=memory_set_id,
                    memory_unit_id="memory_unit:relation",
                    refs=("person:tanaka", "topic:music"),
                    predicate="discusses",
                    memory_type="relation",
                    scope_type="entity",
                    status="confirmed",
                ),
            ]
            self._persist_units(store, units)
            with store.memory_store._memory_db() as conn:
                store.memory_store._upsert_memory_link(
                    conn,
                    {
                        "memory_link_id": "memory_link:contradiction",
                        "memory_set_id": memory_set_id,
                        "source_memory_unit_id": "memory_unit:trust_confirmed",
                        "target_memory_unit_id": "memory_unit:trust_inferred",
                        "label": "contradicts",
                        "confidence": 0.8,
                        "evidence_revision_id": "revision:trust_inferred",
                        "created_at": NOW,
                        "updated_at": NOW,
                        "operation": "new",
                        "reason": "test contradiction",
                    },
                )

            result = store.rebuild_relation_index(memory_set_id=memory_set_id, updated_at=NOW)
            records = store.list_relation_index_records(memory_set_id=memory_set_id)
            records_by_identity = {
                (record["source_ref"], record["target_ref"], record["relation_predicate"]): record
                for record in records
            }

            self.assertEqual(result["edge_count"], 4)
            self.assertEqual(result["status_counts"], {"active": 1, "weak": 2, "inactive": 1})
            self.assertEqual(result["skipped_multi_party_count"], 1)
            self.assertEqual(result["skipped_invalid_count"], 1)
            trust = records_by_identity[("self", "person:tanaka", "trusts")]
            self.assertEqual(trust["derived_status"], "weak")
            self.assertEqual(trust["confidence"], 0.91)
            self.assertEqual(trust["salience"], 0.74)
            self.assertEqual(trust["supporting_memory_link_ids"], ["memory_link:contradiction"])
            self.assertEqual(trust["payload"]["contradiction_count"], 1)
            relation = records_by_identity[("person:tanaka", "topic:music", "discusses")]
            self.assertEqual(relation["derived_status"], "active")

    def test_recall_revalidates_support_and_downgrades_stale_active_edge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileStore(Path(temp_dir))
            memory_set_id = store.read_state()["selected_memory_set_id"]
            confirmed = self._unit(
                memory_set_id=memory_set_id,
                memory_unit_id="memory_unit:confirmed",
                refs=("self", "person:tanaka"),
                predicate="trusts",
                status="confirmed",
            )
            inferred = self._unit(
                memory_set_id=memory_set_id,
                memory_unit_id="memory_unit:inferred",
                refs=("self", "person:tanaka"),
                predicate="trusts",
                status="inferred",
            )
            self._persist_units(store, [confirmed, inferred])
            store.rebuild_relation_index(memory_set_id=memory_set_id, updated_at=NOW)

            revoked = {**confirmed, "status": "revoked"}
            with store.memory_store._memory_db() as conn:
                store.memory_store._upsert_memory_unit(conn, revoked)

            result = store.list_relation_index_for_recall(
                memory_set_id=memory_set_id,
                entity_refs=["person:tanaka"],
                current_time=NOW,
                limit=12,
            )

            self.assertEqual(
                [candidate["memory_unit"]["memory_unit_id"] for candidate in result["candidates"]],
                ["memory_unit:inferred"],
            )
            self.assertEqual(result["candidates"][0]["relation_derived_status"], "weak")
            self.assertEqual(result["status_counts"], {"active": 0, "weak": 1})
            self.assertEqual(result["stale_memory_unit_ids"], ["memory_unit:confirmed"])

    def test_recall_is_limited_to_twelve_candidates_in_priority_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileStore(Path(temp_dir))
            memory_set_id = store.read_state()["selected_memory_set_id"]
            units = [
                self._unit(
                    memory_set_id=memory_set_id,
                    memory_unit_id=f"memory_unit:edge_{index:02d}",
                    refs=("person:tanaka", f"topic:item_{index:02d}"),
                    predicate="related_to",
                    status="confirmed",
                    salience=index / 20,
                )
                for index in range(13)
            ]
            self._persist_units(store, units)
            store.rebuild_relation_index(memory_set_id=memory_set_id, updated_at=NOW)

            result = store.list_relation_index_for_recall(
                memory_set_id=memory_set_id,
                entity_refs=["person:tanaka"],
                current_time=NOW,
                limit=12,
            )

            candidate_ids = [
                candidate["memory_unit"]["memory_unit_id"]
                for candidate in result["candidates"]
            ]
            self.assertEqual(len(candidate_ids), 12)
            self.assertEqual(candidate_ids[0], "memory_unit:edge_12")
            self.assertNotIn("memory_unit:edge_00", candidate_ids)

    def test_failed_rebuild_keeps_last_completed_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileStore(Path(temp_dir))
            memory_set_id = store.read_state()["selected_memory_set_id"]
            unit = self._unit(
                memory_set_id=memory_set_id,
                memory_unit_id="memory_unit:stable",
                refs=("self", "person:tanaka"),
                predicate="trusts",
                status="confirmed",
            )
            self._persist_units(store, [unit])
            store.rebuild_relation_index(memory_set_id=memory_set_id, updated_at=NOW)
            before = store.list_relation_index_records(memory_set_id=memory_set_id)

            original_insert = store.memory_store._insert_relation_index_record

            def fail_insert(_conn: sqlite3.Connection, _record: dict) -> None:
                raise RuntimeError("forced relation index failure")

            store.memory_store._insert_relation_index_record = fail_insert
            try:
                with self.assertRaisesRegex(RuntimeError, "forced relation index failure"):
                    store.rebuild_relation_index(memory_set_id=memory_set_id, updated_at=NOW)
            finally:
                store.memory_store._insert_relation_index_record = original_insert

            self.assertEqual(store.list_relation_index_records(memory_set_id=memory_set_id), before)

    def test_clone_rebuilds_and_delete_removes_relation_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileStore(Path(temp_dir))
            source_memory_set_id = store.read_state()["selected_memory_set_id"]
            target_memory_set_id = "memory_set:clone"
            unit = self._unit(
                memory_set_id=source_memory_set_id,
                memory_unit_id="memory_unit:source",
                refs=("self", "person:tanaka"),
                predicate="trusts",
                status="confirmed",
            )
            self._persist_units(store, [unit])
            store.rebuild_relation_index(memory_set_id=source_memory_set_id, updated_at=NOW)

            store.clone_memory_set_records(
                source_memory_set_id=source_memory_set_id,
                target_memory_set_id=target_memory_set_id,
            )
            cloned = store.list_relation_index_records(memory_set_id=target_memory_set_id)

            self.assertEqual(len(cloned), 1)
            self.assertEqual(cloned[0]["memory_set_id"], target_memory_set_id)
            self.assertNotEqual(
                cloned[0]["supporting_memory_unit_ids"][0],
                unit["memory_unit_id"],
            )
            store.delete_memory_set_records(target_memory_set_id)
            self.assertEqual(store.list_relation_index_records(memory_set_id=target_memory_set_id), [])

    def test_schema_version_fifteen_is_rejected_without_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_dir = Path(temp_dir)
            with sqlite3.connect(root_dir / "memory.db") as conn:
                conn.execute("PRAGMA user_version = 15")

            with self.assertRaisesRegex(RuntimeError, "Expected 16"):
                FileStore(root_dir)

    def test_relation_sync_failure_marks_job_failed_and_appends_audit_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            memory_set_id = service.store.read_state()["selected_memory_set_id"]
            job = {
                "cycle_id": "cycle:relation_failure",
                "memory_set_id": memory_set_id,
                "queued_at": NOW,
                "started_at": None,
                "finished_at": None,
                "result_status": "queued",
            }
            service.store.upsert_memory_postprocess_job(job=job)
            service.memory.run_postprocess_job = lambda **_: {
                "vector_index_sync": {"result_status": "succeeded", "failure_reason": None},
                "relation_index_sync": service._relation_index_sync_trace(
                    "failed",
                    failure_reason="forced relation failure",
                ),
                "correction_reconciliation": {"result_status": "not_requested"},
                "reflective_consolidation": {"result_status": "not_triggered"},
            }

            service._run_memory_postprocess_job(job)

            completed = service.store.get_memory_postprocess_job(job["cycle_id"])
            with service.store.memory_store._memory_db() as conn:
                event_rows = conn.execute(
                    "SELECT kind, payload_json FROM events WHERE cycle_id = ?",
                    (job["cycle_id"],),
                ).fetchall()
            self.assertEqual(completed["result_status"], "failed")
            self.assertEqual([row["kind"] for row in event_rows], ["relation_index_sync_failure"])
            self.assertEqual(
                service.store.list_memory_postprocess_jobs(result_statuses=["queued", "running"]),
                [],
            )

    def test_current_state_inspection_exposes_compact_relation_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = OtomeKairoService(Path(temp_dir))
            state = service.store.read_state()
            state["console_access_token"] = "test-token"
            service.store.write_state(state)
            memory_set_id = state["selected_memory_set_id"]
            unit = self._unit(
                memory_set_id=memory_set_id,
                memory_unit_id="memory_unit:inspection",
                refs=("self", "person:tanaka"),
                predicate="trusts",
                status="confirmed",
            )
            self._persist_units(service.store, [unit])
            service.store.rebuild_relation_index(memory_set_id=memory_set_id, updated_at=NOW)

            snapshot = service.get_current_state_inspection("test-token")
            relation = snapshot["current_state"]["relation_index"][0]

            self.assertEqual(relation["source_ref"], "self")
            self.assertEqual(relation["target_ref"], "person:tanaka")
            self.assertEqual(relation["derived_status"], "active")
            self.assertEqual(relation["supporting_memory_unit_count"], 1)
            self.assertNotIn("supporting_memory_unit_ids", relation)

    def _persist_units(self, store: FileStore, units: list[dict]) -> None:
        actions = [
            {
                "operation": "new",
                "revision_id": f"revision:{unit['memory_unit_id'].split(':', 1)[1]}",
                "memory_set_id": unit["memory_set_id"],
                "memory_unit_id": unit["memory_unit_id"],
                "occurred_at": NOW,
                "memory_unit": unit,
                "related_memory_unit_ids": [],
                "before_snapshot": None,
                "after_snapshot": unit,
                "reason": "relation index test",
                "evidence_event_ids": [],
            }
            for unit in units
        ]
        store.persist_memory_actions(memory_actions=actions)

    def _unit(
        self,
        *,
        memory_set_id: str,
        memory_unit_id: str,
        refs: tuple[str, ...],
        predicate: str,
        memory_type: str = "interpretation",
        scope_type: str = "relationship",
        status: str = "confirmed",
        confidence: float = 0.7,
        salience: float = 0.6,
    ) -> dict:
        subject_ref = refs[0] if refs else "self"
        object_ref = refs[1] if len(refs) > 1 else None
        return {
            "memory_unit_id": memory_unit_id,
            "memory_set_id": memory_set_id,
            "memory_type": memory_type,
            "scope_type": scope_type,
            "scope_key": "|".join(refs),
            "subject_ref": subject_ref,
            "predicate": predicate,
            "object_ref_or_value": object_ref,
            "summary_text": f"{subject_ref} と {object_ref} の関係",
            "status": status,
            "commitment_state": None,
            "confidence": confidence,
            "salience": salience,
            "formed_at": "2026-07-20T11:00:00+09:00",
            "last_confirmed_at": NOW if status == "confirmed" else None,
            "valid_from": None,
            "valid_to": None,
            "evidence_event_ids": [],
            "evidence_cycle_ids": [],
            "qualifiers": {},
        }


if __name__ == "__main__":
    unittest.main()
