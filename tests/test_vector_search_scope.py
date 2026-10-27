import pytest

from otomekairo.store.file_store import SQLiteMemoryStore


MEMORY_SET = "memory_set:test"
STAMP = "2026-10-26T12:00:00+09:00"


def seed(store, source_kind, key, distance, **changes):
    record = {
        "memory_set_id": MEMORY_SET, "summary_text": "検証用の記録。",
        "salience": 0.5, "formed_at": STAMP,
    }
    if source_kind == "memory_unit":
        record.update({
            "memory_unit_id": key, "memory_type": "preference", "scope_type": "entity",
            "scope_key": "person:test", "subject_ref": "person:test", "predicate": "prefers",
            "status": "confirmed", "confidence": 0.8,
        })
    else:
        record.update({
            "episode_id": key, "cycle_id": f"cycle:{key}", "episode_type": "conversation",
            "primary_scope_type": "entity", "primary_scope_key": "person:test",
            "open_loops": ["task:test"],
        })
    record.update(changes)
    with store._memory_db() as conn:
        if source_kind == "memory_unit":
            store._upsert_memory_unit(conn, record)
        else:
            store._insert_episode(conn, record)
    entry = {
        "memory_set_id": record["memory_set_id"], "source_kind": source_kind, "source_id": key,
        "source_text": record["summary_text"], "salience": record["salience"],
        "updated_at": STAMP, "text_hash": "test", "embedding": [distance, 0.0],
        "scope_type": record["scope_type"] if source_kind == "memory_unit" else record["primary_scope_type"],
        "scope_key": record["scope_key"] if source_kind == "memory_unit" else record["primary_scope_key"],
        "source_type": record["memory_type"] if source_kind == "memory_unit" else record["episode_type"],
        "status": record["status"] if source_kind == "memory_unit" else "active",
        "has_open_loops": bool(record.get("open_loops")),
    }
    store.upsert_vector_index_entries(entries=[entry], embedding_dimension=2)


@pytest.mark.parametrize("excluded,filters", [
    ({"memory_set_id": "memory_set:other"}, {}),
    ({"scope_key": "person:other"}, {"scope_filters": [("entity", "person:test")]}),
    ({"scope_type": "topic"}, {"scope_types": ["entity"]}),
    ({"memory_type": "commitment"}, {"exclude_source_types": ["commitment"]}),
    ({"status": "revoked"}, {"statuses": ["inferred", "confirmed"]}),
])
def test_memory_knn_filters_candidates_before_limit(tmp_path, excluded, filters):
    store = SQLiteMemoryStore(tmp_path)
    seed(store, "memory_unit", "memory_unit:valid", 1.0)
    for index in range(3):
        seed(store, "memory_unit", f"memory_unit:excluded:{index}", 0.0, **excluded)
    hits = store.search_memory_unit_vector_entries(
        memory_set_id=MEMORY_SET, query_embedding=[0.0, 0.0], embedding_dimension=2,
        limit=1, **filters,
    )
    assert [hit["record"]["memory_unit_id"] for hit in hits] == ["memory_unit:valid"]
    assert hits[0]["distance"] == pytest.approx(1.0)


@pytest.mark.parametrize("excluded,filters", [
    ({"memory_set_id": "memory_set:other"}, {}),
    ({"primary_scope_key": "person:other"}, {"scope_filters": [("entity", "person:test")]}),
    ({"open_loops": []}, {"require_open_loops": True}),
])
def test_episode_knn_filters_candidates_before_limit(tmp_path, excluded, filters):
    store = SQLiteMemoryStore(tmp_path)
    seed(store, "episode", "episode:valid", 1.0)
    for index in range(3):
        seed(store, "episode", f"episode:excluded:{index}", 0.0, **excluded)
    hits = store.search_episode_vector_entries(
        memory_set_id=MEMORY_SET, query_embedding=[0.0, 0.0], embedding_dimension=2,
        limit=1, **filters,
    )
    assert [hit["record"]["episode_id"] for hit in hits] == ["episode:valid"]


@pytest.mark.parametrize("source_kind", ["memory_unit", "episode"])
def test_knn_returns_only_matching_records_in_distance_order(tmp_path, source_kind):
    store = SQLiteMemoryStore(tmp_path)
    for key, distance in (("far", 2.0), ("near", 0.5), ("middle", 1.0)):
        seed(store, source_kind, f"{source_kind}:{key}", distance)
    seed(store, source_kind, f"{source_kind}:other", 0.0, memory_set_id="memory_set:other")
    search = (store.search_memory_unit_vector_entries if source_kind == "memory_unit"
              else store.search_episode_vector_entries)
    params = {"query_embedding": [0.0, 0.0], "embedding_dimension": 2, "limit": 2}
    hits = search(memory_set_id=MEMORY_SET, **params)
    assert [hit["record"][f"{source_kind}_id"] for hit in hits] == [
        f"{source_kind}:near", f"{source_kind}:middle",
    ]
    assert search(memory_set_id="memory_set:empty", **params) == []
