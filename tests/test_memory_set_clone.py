import json
from copy import deepcopy

import pytest

from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.memory.correction import MemoryCorrectionReconciler
from otomekairo.service.visual_daily import ServiceVisualDailyMixin
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


@pytest.fixture
def visual_memory(tmp_path):
    store = SQLiteMemoryStore(tmp_path)
    runner = ServiceVisualDailyMixin()
    runner._now_iso = lambda: STAMP
    digests, evidence = [], []
    for day in ("2026-10-24", "2026-10-25"):
        group_id = runner._visual_daily_group_id(memory_set_id=SOURCE, local_date=day, index=1)
        digest_id = runner._visual_daily_digest_id(memory_set_id=SOURCE, local_date=day)
        records = [{
            "visual_observation_id": f"visual_observation:{day}:{index}", "memory_set_id": SOURCE,
            "cycle_id": f"cycle:{day}", "observed_at": f"{day}T09:0{index}:00+09:00", "source_kind": "camera",
            "vision_source_id": "vision_source:test", "image_input_kind": "vision_capture_result",
            "retention_status": status, "detailed_summary_text": f"元の観測本文 {day} {index}。",
            "daily_digest_id": digest_id, "duplicate_group_id": group_id,
        } for index, status in enumerate(("active", "compressed", "excluded"))]
        digest = runner._build_visual_daily_digest(
            digest_id=digest_id, memory_set_id=SOURCE, local_date=day,
            started_at=STAMP, finished_at=STAMP, records=records, groups=[{
                "duplicate_group_id": group_id, "source_key": "vision:vision_source:test", "records": records,
                "summary_text": "検証用の観測要約。", "reason_summary": "検証用の整理根拠。",
            }],
        )
        digest["memory_promotion"]["result_status"] = "skipped"
        store.upsert_daily_visual_digest(digest=digest, updated_records=records)
        digests.append(digest)
        evidence.append({
            "digest_id": digest_id, "duplicate_group_id": group_id, "local_date": day,
            "source_key": "vision:vision_source:test", "representative_visual_observation_id": records[0]["visual_observation_id"],
        })
    resolver = MemoryActionResolver(store=store)
    unit = resolver.build_new_memory_unit(
        memory_set_id=SOURCE, finished_at=STAMP, event_ids=[], cycle_ids=[], candidate={
            "memory_type": "interpretation", "scope_type": "topic", "scope_key": "topic:visual_test",
            "subject_ref": "topic:visual_test", "predicate": "visual_daily_pattern", "object_ref_or_value": None,
            "summary_text": "更新前の検証用の視覚記憶。", "status": "inferred", "confidence": 0.5, "salience": 0.4,
            "qualifiers": {
                **evidence[-1], "source": "daily_visual_digest", "visual_evidence": evidence,
                "support_group_refs": [evidence[0]["duplicate_group_id"]],
            },
        },
    )

    def save(operation, current, previous, relation):
        action = resolver.build_memory_action(
            operation=operation, memory_set_id=SOURCE, finished_at=STAMP, memory_unit=current,
            related_memory_unit_ids=[], before_snapshot=previous, after_snapshot=current,
            reason="検証用の視覚記憶更新。", event_ids=[],
        )
        action["visual_daily_review"] = {
            "relation": relation, "target_memory_unit_id": previous["memory_unit_id"] if previous else None,
            "candidate_indexes": [0],
        }
        store.persist_memory_actions(memory_actions=[action], expected_memory_units=[previous] if previous else [])

    save("create", unit, None, "new")
    refined = {**unit, "summary_text": "更新後の検証用の視覚記憶。"}
    save("refine", refined, unit, "refine")
    current = digests[-1]
    current["memory_promotion"].update(result_status="succeeded", promoted_memory_unit_ids=[unit["memory_unit_id"]])
    current["support_review"] = {
        "decisions": [{"reason_summary": unit["qualifiers"]["digest_id"]}],
        "references": {
            "candidate_groups": {"0": evidence[-1]["duplicate_group_id"]},
            "evidence_groups": {"past:0": evidence[0]["duplicate_group_id"]},
            "memory_unit_ids": {"memory:0": unit["memory_unit_id"]},
        },
    }
    store.upsert_daily_visual_digest(digest=current, updated_records=[])
    return store, refined, digests


@pytest.mark.parametrize("clone_twice", [False, True])
def test_cloned_visual_memory_keeps_evidence_and_can_be_reinforced_without_the_source(visual_memory, clone_twice):
    store, source_unit, source_digests = visual_memory
    store.clone_memory_set_records(source_memory_set_id=SOURCE, target_memory_set_id=TARGET)
    assert store.list_memory_units_by_id(memory_set_id=SOURCE, memory_unit_ids=[source_unit["memory_unit_id"]]) == [source_unit]
    destination = TARGET
    if clone_twice:
        destination = "memory_set:second_copy"
        store.clone_memory_set_records(source_memory_set_id=TARGET, target_memory_set_id=destination)
        store.delete_memory_set_records(TARGET)
    store.delete_memory_set_records(SOURCE)
    comparison = store.load_visual_daily_comparison_memories(
        memory_set_id=destination, topic_refs=["topic:visual_test"], memory_unit_ids=[],
    )
    copied, = [item["memory_unit"] for item in comparison]
    assert copied["summary_text"] == source_unit["summary_text"]
    assert copied["scope_key"] == source_unit["scope_key"]
    assert copied["memory_unit_id"] != source_unit["memory_unit_id"]
    assert copied["qualifiers"]["digest_id"] != source_unit["qualifiers"]["digest_id"]
    digests = store.list_daily_visual_digests(memory_set_id=destination, limit=10)
    assert len(digests) == 2
    latest = digests[0]
    assert latest["memory_promotion"]["promoted_memory_unit_ids"] == [copied["memory_unit_id"]]
    assert latest["support_review"]["references"] == {
        "candidate_groups": {"0": copied["qualifiers"]["duplicate_group_id"]},
        "evidence_groups": {"past:0": copied["qualifiers"]["support_group_refs"][0]},
        "memory_unit_ids": {"memory:0": copied["memory_unit_id"]},
    }
    # IDと同じ文字列でも、判断理由の本文は書き換えない。
    assert latest["support_review"]["decisions"] == source_digests[-1]["support_review"]["decisions"]
    assert store.list_pending_visual_daily_dates(memory_set_id=destination, before_local_date="2026-10-28") == []
    assert store.list_pending_visual_daily_promotions(memory_set_id=destination, limit=10) == []
    with store._memory_db() as conn:
        store._validate_visual_daily_evidence(conn, destination, copied["qualifiers"])
        revisions = [json.loads(row[0]) for row in conn.execute(
            "SELECT payload_json FROM revisions WHERE memory_set_id = ?", (destination,),
        )]
        for revision in revisions:
            for snapshot in (revision["before_snapshot"], revision["after_snapshot"]):
                if snapshot is not None:
                    assert snapshot["memory_set_id"] == destination
                    assert snapshot["memory_unit_id"] == copied["memory_unit_id"]
                    store._validate_visual_daily_evidence(conn, destination, snapshot["qualifiers"])
            if revision["operation"] == "refine":
                assert revision["visual_daily_review"]["target_memory_unit_id"] == copied["memory_unit_id"]
        for day in ("2026-10-24", "2026-10-25"):
            records = [json.loads(row[0]) for row in conn.execute(
                "SELECT payload_json FROM visual_observation_records WHERE memory_set_id = ? ORDER BY observed_at",
                (destination,),
            ) if json.loads(row[0])["observed_at"].startswith(day)]
            assert [record["retention_status"] for record in records] == ["active", "compressed", "excluded"]
            assert [record["detailed_summary_text"] for record in records] == [f"元の観測本文 {day} {i}。" for i in range(3)]

    resolver = MemoryActionResolver(store=store)
    action = resolver.build_memory_action(
        operation="reinforce", memory_set_id=destination, finished_at=STAMP, memory_unit=copied,
        related_memory_unit_ids=[], before_snapshot=copied, after_snapshot=copied,
        reason="複製先だけでの根拠確認。", event_ids=[],
    )
    action["visual_daily_review"] = {
        "relation": "same", "target_memory_unit_id": copied["memory_unit_id"], "candidate_indexes": [0],
    }
    store.persist_memory_actions(memory_actions=[action], expected_memory_units=[copied])


@pytest.mark.parametrize("missing", ["digest_id", "duplicate_group_id", "representative_visual_observation_id"])
def test_visual_clone_rejects_missing_evidence_atomically(visual_memory, missing):
    store, unit, _ = visual_memory
    broken = deepcopy(unit)
    broken["qualifiers"]["visual_evidence"][0][missing] = "missing:reference"
    with store._memory_db() as conn:
        store._insert_event(conn, {
            "event_id": "event:source", "cycle_id": "cycle:source", "memory_set_id": SOURCE,
            "kind": "conversation_input", "created_at": STAMP,
        })
        store._upsert_memory_unit(conn, broken)
    with pytest.raises(ValueError, match="複製元参照がありません"):
        store.clone_memory_set_records(source_memory_set_id=SOURCE, target_memory_set_id=TARGET)
    with store._memory_db() as conn:
        for table in ("events", "memory_units", "revisions", "visual_observation_records", "daily_visual_digests"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE memory_set_id = ?", (TARGET,)).fetchone()[0] == 0


@pytest.mark.parametrize("result,promotion,pending", [
    ("succeeded", "not_started", True),
    ("succeeded", "failed", False),
    ("failed", "not_started", False),
])
def test_visual_clone_preserves_pending_and_failed_processing_results(tmp_path, result, promotion, pending):
    store = SQLiteMemoryStore(tmp_path)
    runner = ServiceVisualDailyMixin()
    digest = runner._build_visual_daily_digest(
        digest_id=runner._visual_daily_digest_id(memory_set_id=SOURCE, local_date="2026-10-25"),
        memory_set_id=SOURCE, local_date="2026-10-25", started_at=STAMP, finished_at=STAMP,
        records=[], groups=[],
    )
    digest["result_status"] = result
    digest["memory_promotion"].update(result_status=promotion, failure_reason="検証用の失敗。" if promotion == "failed" else None)
    store.upsert_daily_visual_digest(digest=digest, updated_records=[])
    store.clone_memory_set_records(source_memory_set_id=SOURCE, target_memory_set_id=TARGET)
    copied = store.get_daily_visual_digest(memory_set_id=TARGET, local_date="2026-10-25")
    assert copied == {
        **digest, "memory_set_id": TARGET,
        "digest_id": runner._visual_daily_digest_id(memory_set_id=TARGET, local_date="2026-10-25"),
    }
    assert bool(store.list_pending_visual_daily_promotions(memory_set_id=TARGET, limit=10)) == pending
