from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import sqlite_vec


# 定数
MEMORY_DB_FILE_NAME = "memory.db"
CURRENT_MEMORY_DB_VERSION = 6


# スキーマMixin
class StoreSchemaMixin:
    def _initialize_memory_db(self) -> None:
        # 現行 schema 以外は受け付けない。
        with self._memory_db() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, CURRENT_MEMORY_DB_VERSION}:
                raise RuntimeError(
                    f"Unsupported memory.db schema version: {version}. "
                    f"Expected {CURRENT_MEMORY_DB_VERSION}."
                )

            self._apply_current_schema(conn)
            if version == 0:
                conn.execute(f"PRAGMA user_version = {CURRENT_MEMORY_DB_VERSION}")

    def _open_memory_db(self) -> sqlite3.Connection:
        # 接続
        conn = sqlite3.connect(self.memory_db_path)
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        # pragma群
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def _memory_db(self) -> sqlite3.Connection:
        # 接続ライフサイクル
        conn = self._open_memory_db()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _apply_current_schema(self, conn: sqlite3.Connection) -> None:
        # 現行 schema 全体
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                cycle_id TEXT NOT NULL,
                memory_set_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                role TEXT,
                text TEXT,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_cycle_id
            ON events(cycle_id);

            CREATE INDEX IF NOT EXISTS idx_events_memory_set_created_at
            ON events(memory_set_id, created_at);

            CREATE INDEX IF NOT EXISTS idx_events_kind_created_at
            ON events(kind, created_at);

            CREATE TABLE IF NOT EXISTS retrieval_runs (
                cycle_id TEXT PRIMARY KEY,
                memory_set_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                result_status TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_retrieval_runs_memory_set_finished_at
            ON retrieval_runs(memory_set_id, finished_at);

            CREATE TABLE IF NOT EXISTS cycle_summaries (
                cycle_id TEXT PRIMARY KEY,
                server_id TEXT NOT NULL,
                trigger_kind TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                selected_persona_id TEXT NOT NULL,
                selected_memory_set_id TEXT NOT NULL,
                selected_model_preset_id TEXT NOT NULL,
                result_kind TEXT NOT NULL,
                failed INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_cycle_summaries_started_at
            ON cycle_summaries(started_at);

            CREATE INDEX IF NOT EXISTS idx_cycle_summaries_memory_set_started_at
            ON cycle_summaries(selected_memory_set_id, started_at);

            CREATE TABLE IF NOT EXISTS cycle_traces (
                cycle_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                selected_memory_set_id TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS episodes (
                episode_id TEXT PRIMARY KEY,
                cycle_id TEXT NOT NULL UNIQUE,
                memory_set_id TEXT NOT NULL,
                episode_type TEXT NOT NULL,
                episode_series_id TEXT,
                primary_scope_type TEXT NOT NULL,
                primary_scope_key TEXT NOT NULL,
                summary_text TEXT NOT NULL,
                outcome_text TEXT,
                open_loops_json TEXT NOT NULL,
                has_open_loops INTEGER NOT NULL DEFAULT 0,
                salience REAL NOT NULL,
                formed_at TEXT NOT NULL,
                linked_event_ids_json TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_episodes_memory_set_formed_at
            ON episodes(memory_set_id, formed_at);

            CREATE INDEX IF NOT EXISTS idx_episodes_scope_recent
            ON episodes(memory_set_id, primary_scope_type, primary_scope_key, formed_at);

            CREATE INDEX IF NOT EXISTS idx_episodes_open_loops_recent
            ON episodes(memory_set_id, has_open_loops, formed_at);

            CREATE TABLE IF NOT EXISTS memory_units (
                memory_unit_id TEXT PRIMARY KEY,
                memory_set_id TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                subject_ref TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_ref_or_value TEXT,
                summary_text TEXT NOT NULL,
                status TEXT NOT NULL,
                commitment_state TEXT,
                confidence REAL NOT NULL,
                salience REAL NOT NULL,
                formed_at TEXT NOT NULL,
                last_confirmed_at TEXT,
                valid_from TEXT,
                valid_to TEXT,
                evidence_event_ids_json TEXT NOT NULL,
                qualifiers_json TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_memory_units_compare_key
            ON memory_units(memory_set_id, memory_type, scope_type, scope_key, subject_ref, predicate);

            CREATE INDEX IF NOT EXISTS idx_memory_units_scope_status
            ON memory_units(memory_set_id, scope_type, status, salience);

            CREATE TABLE IF NOT EXISTS revisions (
                revision_id TEXT PRIMARY KEY,
                memory_set_id TEXT NOT NULL,
                memory_unit_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                operation TEXT NOT NULL,
                related_memory_unit_ids_json TEXT NOT NULL,
                before_snapshot_json TEXT,
                after_snapshot_json TEXT,
                reason TEXT NOT NULL,
                evidence_event_ids_json TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_revisions_memory_unit_occurred_at
            ON revisions(memory_unit_id, occurred_at);

            CREATE INDEX IF NOT EXISTS idx_revisions_memory_set_occurred_at
            ON revisions(memory_set_id, occurred_at);

            CREATE TABLE IF NOT EXISTS affect_state (
                affect_state_id TEXT PRIMARY KEY,
                memory_set_id TEXT NOT NULL,
                layer TEXT NOT NULL,
                target_scope_type TEXT NOT NULL,
                target_scope_key TEXT NOT NULL,
                affect_label TEXT NOT NULL,
                intensity REAL NOT NULL,
                observed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_affect_state_identity
            ON affect_state(memory_set_id, layer, target_scope_type, target_scope_key, affect_label);

            CREATE TABLE IF NOT EXISTS vector_index_entries (
                vector_entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_set_id TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                embedding_preset TEXT NOT NULL,
                source_text TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                source_type TEXT NOT NULL,
                status TEXT NOT NULL,
                salience REAL NOT NULL,
                has_open_loops INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                text_hash TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_vector_index_entries_source
            ON vector_index_entries(memory_set_id, source_kind, source_id, embedding_preset);

            CREATE INDEX IF NOT EXISTS idx_vector_index_entries_scope
            ON vector_index_entries(memory_set_id, source_kind, scope_type, scope_key, status, salience);

            CREATE TABLE IF NOT EXISTS reflection_runs (
                reflection_run_id TEXT PRIMARY KEY,
                memory_set_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                result_status TEXT NOT NULL,
                trigger_reasons_json TEXT NOT NULL,
                source_episode_ids_json TEXT NOT NULL,
                affected_memory_unit_ids_json TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_reflection_runs_memory_set_finished_at
            ON reflection_runs(memory_set_id, finished_at);
            """
        )
