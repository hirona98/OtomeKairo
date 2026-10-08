from concurrent.futures import ThreadPoolExecutor
import threading

from otomekairo.store.file_store import SQLiteMemoryStore


def test_read_then_write_scope_keeps_a_consistent_snapshot_until_commit(tmp_path):
    reader = SQLiteMemoryStore(tmp_path)
    writer = SQLiteMemoryStore(tmp_path)
    with reader._memory_db() as conn:
        conn.execute("CREATE TABLE transaction_probe(value INTEGER NOT NULL)")
        conn.execute("INSERT INTO transaction_probe VALUES (0)")
    reading = threading.Event()
    release = threading.Event()
    writing = threading.Event()
    written = threading.Event()

    def read_scope():
        with reader._memory_db() as conn:
            before = conn.execute("SELECT value FROM transaction_probe").fetchone()[0]
            reading.set()
            assert release.wait(2)
            after = conn.execute("SELECT value FROM transaction_probe").fetchone()[0]
            return before, after

    def write_scope():
        writing.set()
        with writer._memory_db() as conn:
            conn.execute("UPDATE transaction_probe SET value = 1")
        written.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(read_scope)
        assert reading.wait(2)
        second = pool.submit(write_scope)
        assert writing.wait(2)
        try:
            assert not written.wait(0.05)
        finally:
            release.set()
        assert first.result(timeout=2) == (0, 0)
        second.result(timeout=2)
    with reader._memory_db() as conn:
        assert conn.execute("SELECT value FROM transaction_probe").fetchone()[0] == 1


def test_concurrent_read_modify_write_does_not_lose_updates(tmp_path):
    stores = [SQLiteMemoryStore(tmp_path), SQLiteMemoryStore(tmp_path)]
    with stores[0]._memory_db() as conn:
        conn.execute("CREATE TABLE transaction_probe(value INTEGER NOT NULL)")
        conn.execute("INSERT INTO transaction_probe VALUES (0)")

    def increment(store):
        for _ in range(50):
            with store._memory_db() as conn:
                previous = conn.execute("SELECT value FROM transaction_probe").fetchone()[0]
                conn.execute("UPDATE transaction_probe SET value = ?", (previous + 1,))

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(increment, stores))
    with stores[0]._memory_db() as conn:
        assert conn.execute("SELECT value FROM transaction_probe").fetchone()[0] == 100


def test_same_store_opens_its_next_connection_only_after_the_current_scope_closes(tmp_path, monkeypatch):
    store = SQLiteMemoryStore(tmp_path)
    original_open = store._open_memory_db
    attempted = threading.Event()
    opened = threading.Event()
    release = threading.Event()
    holding = threading.Event()

    def observed_open():
        if threading.current_thread().name == "next-memory-scope":
            opened.set()
        return original_open()
    monkeypatch.setattr(store, "_open_memory_db", observed_open)

    def owner():
        with store._memory_db() as conn:
            store._ensure_vector_tables(conn, 4)
            holding.set()
            assert release.wait(2)

    def next_scope():
        attempted.set()
        with store._memory_db() as conn:
            assert conn.execute("SELECT 1").fetchone()[0] == 1

    with ThreadPoolExecutor(max_workers=1) as pool:
        current = pool.submit(owner)
        assert holding.wait(2)
        waiting = threading.Thread(target=next_scope, name="next-memory-scope")
        waiting.start()
        assert attempted.wait(2)
        try:
            assert not opened.wait(0.05)
        finally:
            release.set()
            waiting.join(2)
        current.result(timeout=2)
        assert not waiting.is_alive()
        assert opened.is_set()
