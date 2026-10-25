from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from otomekairo.memory.actions import MemoryActionResolver
from otomekairo.memory.reflection.summary import MemoryReflectionSummaryMixin
from otomekairo.store.file_store import SQLiteMemoryStore
from otomekairo.store.memory_links import MemoryWriteConflictError


MEMORY_SET = "memory_set:test"
STAMP = "2026-10-25T10:00:00+09:00"


@pytest.fixture
def context(tmp_path):
    store = SQLiteMemoryStore(tmp_path)
    resolver = MemoryActionResolver(store=store)
    candidate = {
        "memory_type": "preference", "scope_type": "entity", "scope_key": "person:test",
        "subject_ref": "person:test", "predicate": "favorite_drink", "object_ref_or_value": "food:tea",
        "summary_text": "検証用の好み。", "status": "inferred", "confidence": 0.7, "salience": 0.5,
        "qualifiers": {},
    }
    unit = resolver.build_new_memory_unit(
        memory_set_id=MEMORY_SET, finished_at=STAMP, event_ids=[],
        cycle_ids=["cycle:one", "cycle:two", "cycle:three"], candidate=candidate,
    )
    store.persist_memory_actions(memory_actions=[action(resolver, "create", unit, None)])
    return store, resolver, unit


def action(resolver, operation, unit, before, *, related=None):
    return resolver.build_memory_action(
        operation=operation, memory_set_id=MEMORY_SET, finished_at=STAMP,
        memory_unit=unit, related_memory_unit_ids=related or [], before_snapshot=before,
        after_snapshot=unit, reason="検証用の更新。", event_ids=[],
    )


def current(store, unit):
    return store.list_memory_units_by_id(
        memory_set_id=MEMORY_SET, memory_unit_ids=[unit["memory_unit_id"]],
    )[0]


def persist(store, actions, *, turn=False):
    if turn:
        return store.persist_turn_consolidation(
            episode={
                "episode_id": "episode:conflict", "cycle_id": "cycle:conflict", "memory_set_id": MEMORY_SET,
                "episode_type": "conversation", "primary_scope_type": "entity", "primary_scope_key": "person:test",
                "summary_text": "検証用の出来事。", "salience": 0.5, "formed_at": STAMP,
            },
            memory_actions=actions, episode_affects=[],
        )
    return store.persist_memory_actions(memory_actions=actions)


@pytest.mark.parametrize("turn", [False, True])
def test_stale_reflection_cannot_resurrect_corrected_memory(context, turn):
    store, resolver, unit = context
    reflection = MemoryReflectionSummaryMixin()
    reflection.store = store
    reflection.action_resolver = resolver
    pending = reflection._build_reflective_confirmation_actions(
        memory_set_id=MEMORY_SET, finished_at=STAMP, active_units=[deepcopy(unit)],
    )
    assert len(pending) == 1
    corrected = resolver.build_revoked_memory_unit(
        existing=unit, finished_at=STAMP, event_ids=[], cycle_ids=["cycle:correction"],
    )
    store.persist_memory_actions(memory_actions=[action(resolver, "correct", corrected, unit)])

    with pytest.raises(MemoryWriteConflictError):
        persist(store, pending, turn=turn)

    assert current(store, unit) == corrected
    assert "cycle:correction" in current(store, unit)["evidence_cycle_ids"]


@pytest.mark.parametrize("turn", [False, True])
@pytest.mark.parametrize("conflict", ["changed", "deleted", "create"])
def test_conflict_rolls_back_the_entire_batch(context, turn, conflict):
    store, resolver, unit = context
    new_unit = {**deepcopy(unit), "memory_unit_id": "memory_unit:new"}
    first = action(resolver, "create", new_unit, None, related=[unit["memory_unit_id"]])
    stale = action(resolver, "reinforce", {**unit, "confidence": 0.9}, unit)
    if conflict == "changed":
        store.persist_memory_actions(memory_actions=[action(resolver, "refine", {**unit, "salience": 0.8}, unit)])
    elif conflict == "deleted":
        with store._memory_db() as conn:
            conn.execute("DELETE FROM memory_units WHERE memory_unit_id = ?", (unit["memory_unit_id"],))
    else:
        stale = action(resolver, "create", unit, None)
    tables = ("memory_units", "revisions", "memory_links", "episodes", "episode_affects", "mood_state")
    with store._memory_db() as conn:
        before = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] for table in tables}

    with pytest.raises(MemoryWriteConflictError):
        persist(store, [first, stale], turn=turn)

    with store._memory_db() as conn:
        after = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table}")] for table in tables}
    assert after == before


def test_sequential_actions_must_reference_the_previous_saved_result(context):
    store, resolver, unit = context
    reinforced = {**unit, "confidence": 0.8}
    refined = {**reinforced, "summary_text": "補足された検証用の好み。"}
    store.persist_memory_actions(memory_actions=[
        action(resolver, "reinforce", reinforced, unit),
        action(resolver, "refine", refined, reinforced),
    ])
    assert current(store, unit) == refined
    with pytest.raises(MemoryWriteConflictError):
        store.persist_memory_actions(memory_actions=[
            action(resolver, "reinforce", {**refined, "confidence": 0.9}, refined),
            action(resolver, "dormant", {**refined, "status": "dormant"}, refined),
        ])
    assert current(store, unit) == refined


def test_concurrent_updates_accept_only_one_writer(context, tmp_path):
    store, resolver, unit = context
    other = SQLiteMemoryStore(tmp_path)
    barrier = threading.Barrier(2)

    def update(target, confidence):
        change = action(resolver, "reinforce", {**unit, "confidence": confidence}, unit)
        barrier.wait(timeout=3)
        try:
            target.persist_memory_actions(memory_actions=[change])
        except MemoryWriteConflictError:
            return "conflict"
        return "saved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(update, target, value) for target, value in ((store, 0.8), (other, 0.9))]
        assert sorted(f.result(timeout=5) for f in futures) == ["conflict", "saved"]
    assert current(store, unit)["confidence"] in {0.8, 0.9}
