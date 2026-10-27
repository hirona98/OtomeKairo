import json

import pytest

from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.memory.correction import MemoryCorrectionReconciler
from otomekairo.store.file_store import SQLiteMemoryStore


SOURCE = "memory_set:source"
TARGET = "memory_set:copy"
STAMP = "2026-10-26T12:00:00+09:00"


@pytest.mark.parametrize("operation", ["reinforce", "refine", "revoke", "dormant", "supersede"])
def test_cloned_revision_can_restore_previous_memory_in_the_destination(tmp_path, operation):
    store = SQLiteMemoryStore(tmp_path)
    resolver = MemoryActionResolver(store=store)
    with store._memory_db() as conn:
        store._insert_event(conn, {
            "event_id": "event:source", "cycle_id": "cycle:source", "memory_set_id": SOURCE,
            "kind": "conversation_input", "created_at": STAMP,
        })
    original = resolver.build_new_memory_unit(
        memory_set_id=SOURCE, finished_at=STAMP, event_ids=["event:source"],
        cycle_ids=["cycle:source"], candidate={
            "memory_type": "preference", "scope_type": "entity", "scope_key": "person:test",
            "subject_ref": "person:test", "predicate": "prefers", "object_ref_or_value": "food:tea",
            "summary_text": "更新前の検証用の好み。", "status": "confirmed",
            "confidence": 0.8, "salience": 0.5, "qualifiers": {},
        },
    )

    def action(kind, unit, before):
        return resolver.build_memory_action(
            operation=kind, memory_set_id=SOURCE, finished_at=STAMP, memory_unit=unit,
            related_memory_unit_ids=[], before_snapshot=before, after_snapshot=unit,
            reason="検証用の更新。", event_ids=["event:source"],
        )

    store.persist_memory_actions(memory_actions=[action("create", original, None)])
    changed = {**original, "summary_text": "更新後の検証用の好み。"}
    if operation in {"revoke", "dormant", "supersede"}:
        changed["status"] = {"revoke": "revoked", "dormant": "dormant", "supersede": "superseded"}[operation]
    store.persist_memory_actions(memory_actions=[action(operation, changed, original)])
    store.clone_memory_set_records(source_memory_set_id=SOURCE, target_memory_set_id=TARGET)
    store.delete_memory_set_records(SOURCE)
    with store._memory_db() as conn:
        clone = json.loads(conn.execute(
            "SELECT payload_json FROM memory_units WHERE memory_set_id = ?", (TARGET,),
        ).fetchone()[0])
        revision = json.loads(conn.execute(
            "SELECT payload_json FROM revisions WHERE memory_set_id = ? AND operation = ?",
            (TARGET, operation),
        ).fetchone()[0])
    for snapshot in (revision["before_snapshot"], revision["after_snapshot"]):
        assert snapshot["memory_set_id"] == TARGET
        assert snapshot["memory_unit_id"] == clone["memory_unit_id"]
        assert snapshot["evidence_event_ids"] != ["event:source"]

    reconciler = MemoryCorrectionReconciler(store=store, action_resolver=resolver)
    prepared = reconciler.prepare(
        memory_set_id=TARGET, cycle_id="cycle:correction", finished_at="2026-10-26T12:01:00+09:00",
        candidate_memory_unit_ids=[clone["memory_unit_id"]],
    )
    actions, trace = reconciler.run(context={
        **prepared,
        "selection": {"correction_status": "selected", "selected_targets": [{
            "revision_id": revision["revision_id"], "memory_unit_id": clone["memory_unit_id"],
            "reason_summary": "検証用の訂正。",
        }]},
        "event_ids": [], "cycle_ids": ["cycle:correction"],
    }, finished_at="2026-10-26T12:01:00+09:00")
    store.persist_memory_actions(memory_actions=actions)
    restored = store.list_memory_units_by_id(memory_set_id=TARGET, memory_unit_ids=[clone["memory_unit_id"]])
    assert trace["selected_target_count"] == 1
    assert len(restored) == 1
    assert restored[0] == revision["before_snapshot"]
    assert restored[0]["summary_text"] == original["summary_text"]
    assert store.list_memory_units_by_id(memory_set_id=SOURCE, memory_unit_ids=[original["memory_unit_id"]]) == []
