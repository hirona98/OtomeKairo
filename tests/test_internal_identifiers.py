from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from otomekairo.store.file_store import SQLiteMemoryStore
from otomekairo.store.identifiers import InternalIdentityConflictError, MAX_INTERNAL_ID


def number(identifier):
    return int(identifier.split(":", 1)[1])


def test_sequence_is_shared_across_kinds_and_survives_reopening(tmp_path):
    store = SQLiteMemoryStore(tmp_path)
    cycle = store.allocate_id("cycle")
    event = store.allocate_id("event")
    reopened = SQLiteMemoryStore(tmp_path)
    episode = reopened.allocate_id("episode")
    assert [cycle, event, episode] == ["cycle:1", "event:2", "episode:3"]


def test_concurrent_stores_allocate_distinct_numbers(tmp_path):
    stores = [SQLiteMemoryStore(tmp_path) for _ in range(4)]

    def allocate(store):
        return [store.allocate_id("event") for _ in range(12)]

    with ThreadPoolExecutor(max_workers=4) as pool:
        identifiers = [identifier for batch in pool.map(allocate, stores) for identifier in batch]
    assert {number(identifier) for identifier in identifiers} == set(range(1, 49))


def test_reserved_id_is_not_reused_when_the_record_write_fails(tmp_path):
    store = SQLiteMemoryStore(tmp_path)
    reserved = store.allocate_id("event")
    with pytest.raises(sqlite3.IntegrityError):
        with store._memory_db() as conn:
            conn.execute("INSERT INTO events(event_id) VALUES (?)", (reserved,))
    assert number(store.allocate_id("event")) > number(reserved)


def test_resolved_identity_is_stable_and_rejects_different_source_data(tmp_path):
    store = SQLiteMemoryStore(tmp_path)
    first = store.resolve_internal_id("observation", scope="", key="source/channel/sample", fingerprint="one")
    reopened = SQLiteMemoryStore(tmp_path)
    assert reopened.resolve_internal_id("observation", scope="", key="source/channel/sample", fingerprint="one") == first
    with pytest.raises(InternalIdentityConflictError):
        reopened.resolve_internal_id("observation", scope="", key="source/channel/sample", fingerprint="two")
    assert reopened.allocate_id("event") == "event:2"


def test_deleting_a_memory_set_does_not_recycle_its_ids(tmp_path):
    store = SQLiteMemoryStore(tmp_path)
    old = store.resolve_internal_id("drive", scope="memory_set:test", key="identity")
    store.delete_memory_set_records("memory_set:test")
    new = store.resolve_internal_id("drive", scope="memory_set:test", key="identity")
    assert number(new) > number(old)


def test_sequence_failure_is_explicit(tmp_path):
    store = SQLiteMemoryStore(tmp_path)
    with pytest.raises(ValueError, match="kind"):
        store.allocate_id("unknown")
    with store._memory_db() as conn:
        conn.execute("UPDATE internal_id_sequence SET last_value = ?", (MAX_INTERNAL_ID,))
    with pytest.raises(RuntimeError, match="exhausted"):
        store.allocate_id("event")
    with store._memory_db() as conn:
        conn.execute("DELETE FROM internal_id_sequence")
    with pytest.raises(RuntimeError, match="missing"):
        SQLiteMemoryStore(tmp_path)


def test_old_schema_is_rejected_without_conversion(tmp_path):
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 17")
    with pytest.raises(RuntimeError, match="Unsupported memory.db schema version: 17"):
        SQLiteMemoryStore(tmp_path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 17
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name = 'internal_id_sequence'").fetchall()
