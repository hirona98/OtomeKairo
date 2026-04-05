from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import sqlite_vec

from otomekairo.defaults import build_default_state, normalize_state


# Block: Constants
STATE_FILE_NAME = "server_state.json"
MEMORY_DB_FILE_NAME = "memory.db"

LEGACY_EVENTS_FILE_NAME = "events.jsonl"
LEGACY_RETRIEVAL_RUNS_FILE_NAME = "retrieval_runs.jsonl"
LEGACY_CYCLE_SUMMARIES_FILE_NAME = "cycle_summaries.jsonl"
LEGACY_CYCLE_TRACES_FILE_NAME = "cycle_traces.jsonl"

CURRENT_MEMORY_DB_VERSION = 4


# Block: Store
class FileStore:
    def __init__(self, root_dir: Path) -> None:
        # Block: Paths
        self.root_dir = root_dir
        self.state_path = root_dir / STATE_FILE_NAME
        self.memory_db_path = root_dir / MEMORY_DB_FILE_NAME

        # Block: Initialization
        self.root_dir.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self.write_state(build_default_state())
        self._initialize_memory_db()

    def read_state(self) -> dict:
        # Block: ReadState
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state, changed = normalize_state(state)
        if changed:
            self.write_state(state)
        return state

    def write_state(self, state: dict) -> None:
        # Block: AtomicWrite
        self.root_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.root_dir,
            delete=False,
        ) as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            temp_path = Path(handle.name)

        # Block: CommitWrite
        temp_path.replace(self.state_path)

    def persist_cycle_records(
        self,
        *,
        events: list[dict[str, Any]],
        retrieval_run: dict[str, Any],
        cycle_summary: dict[str, Any],
        cycle_trace: dict[str, Any],
    ) -> None:
        # Block: Transaction
        with self._memory_db() as conn:
            # Block: CycleSummaryInsert
            self._insert_cycle_summary(conn, cycle_summary)

            # Block: EventInsert
            for event in events:
                self._insert_event(conn, event)

            # Block: RetrievalInsert
            self._insert_retrieval_run(conn, retrieval_run)

            # Block: TraceInsert
            self._insert_cycle_trace(conn, cycle_trace)

    def persist_turn_consolidation(
        self,
        *,
        episode_digest: dict[str, Any] | None,
        memory_actions: list[dict[str, Any]],
        affect_updates: list[dict[str, Any]],
    ) -> None:
        # Block: Transaction
        with self._memory_db() as conn:
            # Block: EpisodeDigestInsert
            if episode_digest is not None:
                self._insert_episode_digest(conn, episode_digest)

            # Block: MemoryActions
            for action in memory_actions:
                self._apply_memory_action(conn, action)

            # Block: AffectUpdates
            for affect_update in affect_updates:
                self._upsert_affect_state(conn, affect_update)

    def list_cycle_summaries(self, limit: int) -> list[dict[str, Any]]:
        # Block: Query
        with self._memory_db() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM cycle_summaries
                ORDER BY started_at DESC, rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        # Block: Result
        return [json.loads(row["payload_json"]) for row in rows]

    def get_cycle_trace(self, cycle_id: str) -> dict[str, Any] | None:
        # Block: Query
        with self._memory_db() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM cycle_traces
                WHERE cycle_id = ?
                """,
                (cycle_id,),
            ).fetchone()

        # Block: Result
        if row is None:
            return None
        return json.loads(row["payload_json"])

    def load_recent_turns(
        self,
        *,
        memory_set_id: str,
        since_iso: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        # Block: Query
        with self._memory_db() as conn:
            rows = conn.execute(
                """
                SELECT role, text, created_at
                FROM events
                WHERE memory_set_id = ?
                  AND kind IN ('observation', 'reply')
                  AND text IS NOT NULL
                  AND created_at >= ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (memory_set_id, since_iso, limit),
            ).fetchall()

        # Block: Result
        turns = [
            {
                "role": row["role"],
                "text": row["text"],
                "created_at": row["created_at"],
            }
            for row in reversed(rows)
        ]
        return turns

    def find_memory_units_for_compare(
        self,
        *,
        memory_set_id: str,
        memory_type: str,
        scope_type: str,
        scope_key: str,
        subject_ref: str,
        predicate: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        # Block: Query
        with self._memory_db() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM memory_units
                WHERE memory_set_id = ?
                  AND memory_type = ?
                  AND scope_type = ?
                  AND scope_key = ?
                  AND subject_ref = ?
                  AND predicate = ?
                  AND status NOT IN ('superseded', 'revoked')
                ORDER BY salience DESC, confidence DESC, formed_at DESC, rowid DESC
                LIMIT ?
                """,
                (
                    memory_set_id,
                    memory_type,
                    scope_type,
                    scope_key,
                    subject_ref,
                    predicate,
                    limit,
                ),
            ).fetchall()

        # Block: Result
        return [json.loads(row["payload_json"]) for row in rows]

    def list_memory_units_for_recall(
        self,
        *,
        memory_set_id: str,
        scope_filters: list[tuple[str, str]] | None = None,
        scope_types: list[str] | None = None,
        include_memory_types: list[str] | None = None,
        exclude_memory_types: list[str] | None = None,
        statuses: list[str] | None = None,
        commitment_states: list[str] | None = None,
        limit: int,
    ) -> list[dict[str, Any]]:
        # Block: QueryParts
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]

        # Block: ScopeFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(scope_type = ? AND scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        # Block: ScopeTypes
        if scope_types:
            placeholders = ", ".join("?" for _ in scope_types)
            clauses.append(f"scope_type IN ({placeholders})")
            params.extend(scope_types)

        # Block: IncludeMemoryTypes
        if include_memory_types:
            placeholders = ", ".join("?" for _ in include_memory_types)
            clauses.append(f"memory_type IN ({placeholders})")
            params.extend(include_memory_types)

        # Block: ExcludeMemoryTypes
        if exclude_memory_types:
            placeholders = ", ".join("?" for _ in exclude_memory_types)
            clauses.append(f"memory_type NOT IN ({placeholders})")
            params.extend(exclude_memory_types)

        # Block: Statuses
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)

        # Block: CommitmentStates
        if commitment_states:
            placeholders = ", ".join("?" for _ in commitment_states)
            clauses.append(f"commitment_state IN ({placeholders})")
            params.extend(commitment_states)

        query = f"""
            SELECT payload_json
            FROM memory_units
            WHERE {" AND ".join(clauses)}
            ORDER BY salience DESC, confidence DESC, COALESCE(last_confirmed_at, formed_at) DESC, rowid DESC
            LIMIT ?
        """

        # Block: Query
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # Block: Result
        return [json.loads(row["payload_json"]) for row in rows]

    def list_episode_digests_for_recall(
        self,
        *,
        memory_set_id: str,
        scope_filters: list[tuple[str, str]] | None = None,
        require_open_loops: bool = False,
        limit: int,
    ) -> list[dict[str, Any]]:
        # Block: QueryParts
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]

        # Block: ScopeFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(primary_scope_type = ? AND primary_scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        # Block: OpenLoopFilter
        if require_open_loops:
            clauses.append("has_open_loops = 1")

        query = f"""
            SELECT payload_json
            FROM episode_digests
            WHERE {" AND ".join(clauses)}
            ORDER BY has_open_loops DESC, salience DESC, formed_at DESC, rowid DESC
            LIMIT ?
        """

        # Block: Query
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # Block: Result
        return [json.loads(row["payload_json"]) for row in rows]

    def list_affect_states_for_context(
        self,
        *,
        memory_set_id: str,
        scope_filters: list[tuple[str, str]] | None = None,
        layers: list[str] | None = None,
        limit: int,
    ) -> list[dict[str, Any]]:
        # Block: QueryParts
        clauses = ["memory_set_id = ?"]
        params: list[Any] = [memory_set_id]

        # Block: ScopeFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(target_scope_type = ? AND target_scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        # Block: Layers
        if layers:
            placeholders = ", ".join("?" for _ in layers)
            clauses.append(f"layer IN ({placeholders})")
            params.extend(layers)

        query = f"""
            SELECT payload_json
            FROM affect_state
            WHERE {" AND ".join(clauses)}
            ORDER BY intensity DESC, updated_at DESC, rowid DESC
            LIMIT ?
        """

        # Block: Query
        with self._memory_db() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()

        # Block: Result
        return [json.loads(row["payload_json"]) for row in rows]

    def upsert_vector_index_entries(
        self,
        *,
        entries: list[dict[str, Any]],
        embedding_dimension: int,
    ) -> None:
        # Block: Empty
        if not entries:
            return

        # Block: Transaction
        with self._memory_db() as conn:
            self._ensure_vector_tables(conn, embedding_dimension)

            # Block: Upserts
            for entry in entries:
                existing_row = conn.execute(
                    """
                    SELECT vector_entry_id
                    FROM vector_index_entries
                    WHERE memory_set_id = ?
                      AND source_kind = ?
                      AND source_id = ?
                      AND embedding_preset = ?
                    """,
                    (
                        entry["memory_set_id"],
                        entry["source_kind"],
                        entry["source_id"],
                        entry["embedding_preset"],
                    ),
                ).fetchone()

                # Block: Identity
                vector_entry_id: int
                if existing_row is None:
                    cursor = conn.execute(
                        """
                        INSERT INTO vector_index_entries (
                            memory_set_id,
                            source_kind,
                            source_id,
                            embedding_preset,
                            source_text,
                            scope_type,
                            scope_key,
                            source_type,
                            status,
                            salience,
                            has_open_loops,
                            updated_at,
                            text_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            entry["memory_set_id"],
                            entry["source_kind"],
                            entry["source_id"],
                            entry["embedding_preset"],
                            entry["source_text"],
                            entry["scope_type"],
                            entry["scope_key"],
                            entry["source_type"],
                            entry["status"],
                            entry["salience"],
                            int(bool(entry["has_open_loops"])),
                            entry["updated_at"],
                            entry["text_hash"],
                        ),
                    )
                    vector_entry_id = int(cursor.lastrowid)
                else:
                    vector_entry_id = int(existing_row["vector_entry_id"])
                    conn.execute(
                        """
                        UPDATE vector_index_entries
                        SET source_text = ?,
                            scope_type = ?,
                            scope_key = ?,
                            source_type = ?,
                            status = ?,
                            salience = ?,
                            has_open_loops = ?,
                            updated_at = ?,
                            text_hash = ?
                        WHERE vector_entry_id = ?
                        """,
                        (
                            entry["source_text"],
                            entry["scope_type"],
                            entry["scope_key"],
                            entry["source_type"],
                            entry["status"],
                            entry["salience"],
                            int(bool(entry["has_open_loops"])),
                            entry["updated_at"],
                            entry["text_hash"],
                            vector_entry_id,
                        ),
                    )

                # Block: VectorUpsert
                vector_table_name = self._vector_table_name(entry["source_kind"])
                conn.execute(
                    f"INSERT OR REPLACE INTO {vector_table_name}(id, embedding) VALUES (?, ?)",
                    (
                        vector_entry_id,
                        sqlite_vec.serialize_float32(entry["embedding"]),
                    ),
                )

    def search_memory_unit_vector_entries(
        self,
        *,
        memory_set_id: str,
        embedding_preset: str,
        query_embedding: list[float],
        embedding_dimension: int,
        limit: int,
        scope_filters: list[tuple[str, str]] | None = None,
        scope_types: list[str] | None = None,
        exclude_source_types: list[str] | None = None,
        statuses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        # Block: Empty
        if not query_embedding or limit <= 0:
            return []

        # Block: QueryParts
        clauses = [
            "meta.memory_set_id = ?",
            "meta.embedding_preset = ?",
        ]
        params: list[Any] = [
            sqlite_vec.serialize_float32(query_embedding),
            limit,
            memory_set_id,
            embedding_preset,
        ]

        # Block: ScopeFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(meta.scope_type = ? AND meta.scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        # Block: ScopeTypes
        if scope_types:
            placeholders = ", ".join("?" for _ in scope_types)
            clauses.append(f"meta.scope_type IN ({placeholders})")
            params.extend(scope_types)

        # Block: ExcludeSourceTypes
        if exclude_source_types:
            placeholders = ", ".join("?" for _ in exclude_source_types)
            clauses.append(f"meta.source_type NOT IN ({placeholders})")
            params.extend(exclude_source_types)

        # Block: Statuses
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"meta.status IN ({placeholders})")
            params.extend(statuses)

        query = f"""
            SELECT unit.payload_json, memory_unit_vec.distance
            FROM memory_unit_vec
            JOIN vector_index_entries AS meta
              ON meta.vector_entry_id = memory_unit_vec.id
            JOIN memory_units AS unit
              ON unit.memory_unit_id = meta.source_id
            WHERE memory_unit_vec.embedding MATCH ?
              AND k = ?
              AND {" AND ".join(clauses)}
            ORDER BY memory_unit_vec.distance ASC, meta.salience DESC
            LIMIT ?
        """

        # Block: Query
        with self._memory_db() as conn:
            self._ensure_vector_tables(conn, embedding_dimension)
            rows = conn.execute(query, (*params, limit)).fetchall()

        # Block: Result
        return [
            {
                "record": json.loads(row["payload_json"]),
                "distance": float(row["distance"]),
            }
            for row in rows
        ]

    def search_episode_digest_vector_entries(
        self,
        *,
        memory_set_id: str,
        embedding_preset: str,
        query_embedding: list[float],
        embedding_dimension: int,
        limit: int,
        scope_filters: list[tuple[str, str]] | None = None,
        require_open_loops: bool = False,
    ) -> list[dict[str, Any]]:
        # Block: Empty
        if not query_embedding or limit <= 0:
            return []

        # Block: QueryParts
        clauses = [
            "meta.memory_set_id = ?",
            "meta.embedding_preset = ?",
        ]
        params: list[Any] = [
            sqlite_vec.serialize_float32(query_embedding),
            limit,
            memory_set_id,
            embedding_preset,
        ]

        # Block: ScopeFilters
        if scope_filters:
            scope_clauses: list[str] = []
            for scope_type, scope_key in scope_filters:
                scope_clauses.append("(meta.scope_type = ? AND meta.scope_key = ?)")
                params.extend([scope_type, scope_key])
            clauses.append("(" + " OR ".join(scope_clauses) + ")")

        # Block: OpenLoops
        if require_open_loops:
            clauses.append("meta.has_open_loops = 1")

        query = f"""
            SELECT digest.payload_json, episode_digest_vec.distance
            FROM episode_digest_vec
            JOIN vector_index_entries AS meta
              ON meta.vector_entry_id = episode_digest_vec.id
            JOIN episode_digests AS digest
              ON digest.episode_digest_id = meta.source_id
            WHERE episode_digest_vec.embedding MATCH ?
              AND k = ?
              AND {" AND ".join(clauses)}
            ORDER BY episode_digest_vec.distance ASC, meta.has_open_loops DESC, meta.salience DESC
            LIMIT ?
        """

        # Block: Query
        with self._memory_db() as conn:
            self._ensure_vector_tables(conn, embedding_dimension)
            rows = conn.execute(query, (*params, limit)).fetchall()

        # Block: Result
        return [
            {
                "record": json.loads(row["payload_json"]),
                "distance": float(row["distance"]),
            }
            for row in rows
        ]

    def delete_memory_set_records(self, memory_set_id: str) -> None:
        # Block: Transaction
        with self._memory_db() as conn:
            # Block: VectorDelete
            self._delete_vector_index_entries(conn, memory_set_id)

            # Block: DeleteOrder
            conn.execute("DELETE FROM revisions WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM affect_state WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM memory_units WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM episode_digests WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM events WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM retrieval_runs WHERE memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM cycle_traces WHERE selected_memory_set_id = ?", (memory_set_id,))
            conn.execute("DELETE FROM cycle_summaries WHERE selected_memory_set_id = ?", (memory_set_id,))

    def _initialize_memory_db(self) -> None:
        # Block: SchemaSetup
        with self._memory_db() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                self._apply_schema_v1(conn)
            if version < 2:
                self._apply_schema_v2(conn)
            if version < 3:
                self._apply_schema_v3(conn)
            if version < 4:
                self._apply_schema_v4(conn)
            if version < CURRENT_MEMORY_DB_VERSION:
                conn.execute(f"PRAGMA user_version = {CURRENT_MEMORY_DB_VERSION}")

            # Block: LegacyImport
            self._import_legacy_jsonl_records(conn)

    def _open_memory_db(self) -> sqlite3.Connection:
        # Block: Connection
        conn = sqlite3.connect(self.memory_db_path)
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        # Block: Pragmas
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def _memory_db(self) -> sqlite3.Connection:
        # Block: ConnectionLifecycle
        conn = self._open_memory_db()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _apply_schema_v1(self, conn: sqlite3.Connection) -> None:
        # Block: Schema
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
            """
        )

    def _apply_schema_v2(self, conn: sqlite3.Connection) -> None:
        # Block: Schema
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS episode_digests (
                episode_digest_id TEXT PRIMARY KEY,
                cycle_id TEXT NOT NULL UNIQUE,
                memory_set_id TEXT NOT NULL,
                episode_type TEXT NOT NULL,
                primary_scope_type TEXT NOT NULL,
                primary_scope_key TEXT NOT NULL,
                summary_text TEXT NOT NULL,
                outcome_text TEXT,
                open_loops_json TEXT NOT NULL,
                salience REAL NOT NULL,
                formed_at TEXT NOT NULL,
                linked_event_ids_json TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_episode_digests_memory_set_formed_at
            ON episode_digests(memory_set_id, formed_at);

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
            """
        )

    def _apply_schema_v3(self, conn: sqlite3.Connection) -> None:
        # Block: ColumnLookup
        column_names = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(episode_digests)").fetchall()
        }

        # Block: ColumnAdd
        if "has_open_loops" not in column_names:
            conn.execute(
                """
                ALTER TABLE episode_digests
                ADD COLUMN has_open_loops INTEGER NOT NULL DEFAULT 0
                """
            )

        # Block: Backfill
        conn.execute(
            """
            UPDATE episode_digests
            SET has_open_loops = CASE
                WHEN open_loops_json = '[]' THEN 0
                ELSE 1
            END
            """
        )

        # Block: Indexes
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_episode_digests_scope_recent
            ON episode_digests(memory_set_id, primary_scope_type, primary_scope_key, formed_at);

            CREATE INDEX IF NOT EXISTS idx_episode_digests_open_loops_recent
            ON episode_digests(memory_set_id, has_open_loops, formed_at);
            """
        )

    def _apply_schema_v4(self, conn: sqlite3.Connection) -> None:
        # Block: Schema
        conn.executescript(
            """
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
            """
        )

    def _import_legacy_jsonl_records(self, conn: sqlite3.Connection) -> None:
        # Block: SummaryLookup
        legacy_summaries = self._read_jsonl_file(LEGACY_CYCLE_SUMMARIES_FILE_NAME)
        summary_by_cycle_id = {
            summary["cycle_id"]: summary
            for summary in legacy_summaries
            if isinstance(summary, dict) and isinstance(summary.get("cycle_id"), str)
        }

        # Block: ImportCycleSummaries
        if not self._table_has_rows(conn, "cycle_summaries"):
            for summary in legacy_summaries:
                if isinstance(summary, dict):
                    self._insert_cycle_summary(conn, summary)

        # Block: ImportCycleTraces
        if not self._table_has_rows(conn, "cycle_traces"):
            for trace in self._read_jsonl_file(LEGACY_CYCLE_TRACES_FILE_NAME):
                if isinstance(trace, dict):
                    self._insert_cycle_trace(conn, trace)

        # Block: ImportEvents
        if not self._table_has_rows(conn, "events"):
            for event in self._read_jsonl_file(LEGACY_EVENTS_FILE_NAME):
                if not isinstance(event, dict):
                    continue
                cycle_summary = summary_by_cycle_id.get(event.get("cycle_id"), {})
                legacy_record = {
                    **event,
                    "memory_set_id": cycle_summary.get("selected_memory_set_id", "memory_set:legacy"),
                }
                self._insert_event(conn, legacy_record)

        # Block: ImportRetrievalRuns
        if not self._table_has_rows(conn, "retrieval_runs"):
            for retrieval_run in self._read_jsonl_file(LEGACY_RETRIEVAL_RUNS_FILE_NAME):
                if not isinstance(retrieval_run, dict):
                    continue
                cycle_summary = summary_by_cycle_id.get(retrieval_run.get("cycle_id"), {})
                legacy_record = {
                    **retrieval_run,
                    "selected_memory_set_id": cycle_summary.get("selected_memory_set_id", "memory_set:legacy"),
                }
                self._insert_retrieval_run(conn, legacy_record)

    def _insert_event(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # Block: Insert
        conn.execute(
            """
            INSERT OR REPLACE INTO events (
                event_id,
                cycle_id,
                memory_set_id,
                kind,
                role,
                text,
                created_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["event_id"],
                record["cycle_id"],
                record["memory_set_id"],
                record["kind"],
                record.get("role"),
                record.get("text"),
                record["created_at"],
                self._to_json(record),
            ),
        )

    def _insert_retrieval_run(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # Block: Insert
        conn.execute(
            """
            INSERT OR REPLACE INTO retrieval_runs (
                cycle_id,
                memory_set_id,
                started_at,
                finished_at,
                result_status,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record["cycle_id"],
                record["selected_memory_set_id"],
                record["started_at"],
                record["finished_at"],
                record["result_status"],
                self._to_json(record),
            ),
        )

    def _insert_cycle_summary(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # Block: Insert
        conn.execute(
            """
            INSERT OR REPLACE INTO cycle_summaries (
                cycle_id,
                server_id,
                trigger_kind,
                started_at,
                finished_at,
                selected_persona_id,
                selected_memory_set_id,
                selected_model_preset_id,
                result_kind,
                failed,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["cycle_id"],
                record["server_id"],
                record["trigger_kind"],
                record["started_at"],
                record["finished_at"],
                record["selected_persona_id"],
                record["selected_memory_set_id"],
                record["selected_model_preset_id"],
                record["result_kind"],
                int(bool(record["failed"])),
                self._to_json(record),
            ),
        )

    def _insert_cycle_trace(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # Block: TraceFields
        cycle_summary = record.get("cycle_summary", {})

        # Block: Insert
        conn.execute(
            """
            INSERT OR REPLACE INTO cycle_traces (
                cycle_id,
                started_at,
                selected_memory_set_id,
                payload_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                record["cycle_id"],
                cycle_summary.get("started_at", ""),
                cycle_summary.get("selected_memory_set_id", "memory_set:legacy"),
                self._to_json(record),
            ),
        )

    def _insert_episode_digest(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # Block: DerivedFields
        open_loops = record.get("open_loops", [])

        # Block: Insert
        conn.execute(
            """
            INSERT OR REPLACE INTO episode_digests (
                episode_digest_id,
                cycle_id,
                memory_set_id,
                episode_type,
                primary_scope_type,
                primary_scope_key,
                summary_text,
                outcome_text,
                open_loops_json,
                has_open_loops,
                salience,
                formed_at,
                linked_event_ids_json,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["episode_digest_id"],
                record["cycle_id"],
                record["memory_set_id"],
                record["episode_type"],
                record["primary_scope_type"],
                record["primary_scope_key"],
                record["summary_text"],
                record.get("outcome_text"),
                self._to_json(open_loops),
                int(bool(open_loops)),
                record["salience"],
                record["formed_at"],
                self._to_json(record.get("linked_event_ids", [])),
                self._to_json(record),
            ),
        )

    def _ensure_vector_tables(self, conn: sqlite3.Connection, embedding_dimension: int) -> None:
        # Block: DimensionCheck
        if embedding_dimension <= 0:
            raise ValueError("embedding_dimension must be positive.")

        # Block: MemoryUnitTable
        self._ensure_vector_table(
            conn,
            table_name="memory_unit_vec",
            embedding_dimension=embedding_dimension,
        )

        # Block: EpisodeDigestTable
        self._ensure_vector_table(
            conn,
            table_name="episode_digest_vec",
            embedding_dimension=embedding_dimension,
        )

    def _ensure_vector_table(self, conn: sqlite3.Connection, *, table_name: str, embedding_dimension: int) -> None:
        # Block: ExistingSchema
        schema_row = conn.execute(
            """
            SELECT sql
            FROM sqlite_schema
            WHERE type = 'table'
              AND name = ?
            """,
            (table_name,),
        ).fetchone()

        # Block: Create
        if schema_row is None:
            conn.execute(
                f"""
                CREATE VIRTUAL TABLE {table_name}
                USING vec0(id INTEGER PRIMARY KEY, embedding FLOAT[{embedding_dimension}])
                """
            )
            return

        # Block: Validate
        schema_sql = schema_row["sql"] or ""
        if f"FLOAT[{embedding_dimension}]".lower() not in schema_sql.lower():
            raise ValueError(f"{table_name} dimension does not match current embedding_dimension.")

    def _delete_vector_index_entries(self, conn: sqlite3.Connection, memory_set_id: str) -> None:
        # Block: Query
        rows = conn.execute(
            """
            SELECT vector_entry_id, source_kind
            FROM vector_index_entries
            WHERE memory_set_id = ?
            """,
            (memory_set_id,),
        ).fetchall()

        # Block: Empty
        if not rows:
            return

        # Block: DeleteVectors
        for row in rows:
            table_name = self._vector_table_name(row["source_kind"])
            if self._table_exists(conn, table_name):
                conn.execute(
                    f"DELETE FROM {table_name} WHERE id = ?",
                    (row["vector_entry_id"],),
                )

        # Block: DeleteMetadata
        conn.execute(
            "DELETE FROM vector_index_entries WHERE memory_set_id = ?",
            (memory_set_id,),
        )

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        # Block: Query
        row = conn.execute(
            """
            SELECT 1
            FROM sqlite_schema
            WHERE name = ?
            """,
            (table_name,),
        ).fetchone()
        return row is not None

    def _vector_table_name(self, source_kind: str) -> str:
        # Block: Mapping
        if source_kind == "memory_unit":
            return "memory_unit_vec"
        if source_kind == "episode_digest":
            return "episode_digest_vec"
        raise ValueError(f"Unsupported source_kind: {source_kind}")

    def _apply_memory_action(self, conn: sqlite3.Connection, action: dict[str, Any]) -> None:
        # Block: OperationRead
        operation = action["operation"]
        memory_unit = action.get("memory_unit")

        # Block: Noop
        if operation == "noop":
            return

        # Block: UpsertMemoryUnit
        if memory_unit is not None:
            self._upsert_memory_unit(conn, memory_unit)

        # Block: RevisionInsert
        self._insert_revision(conn, action)

    def _upsert_memory_unit(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # Block: Insert
        conn.execute(
            """
            INSERT OR REPLACE INTO memory_units (
                memory_unit_id,
                memory_set_id,
                memory_type,
                scope_type,
                scope_key,
                subject_ref,
                predicate,
                object_ref_or_value,
                summary_text,
                status,
                commitment_state,
                confidence,
                salience,
                formed_at,
                last_confirmed_at,
                valid_from,
                valid_to,
                evidence_event_ids_json,
                qualifiers_json,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["memory_unit_id"],
                record["memory_set_id"],
                record["memory_type"],
                record["scope_type"],
                record["scope_key"],
                record["subject_ref"],
                record["predicate"],
                record.get("object_ref_or_value"),
                record["summary_text"],
                record["status"],
                record.get("commitment_state"),
                record["confidence"],
                record["salience"],
                record["formed_at"],
                record.get("last_confirmed_at"),
                record.get("valid_from"),
                record.get("valid_to"),
                self._to_json(record.get("evidence_event_ids", [])),
                self._to_json(record.get("qualifiers", {})),
                self._to_json(record),
            ),
        )

    def _insert_revision(self, conn: sqlite3.Connection, action: dict[str, Any]) -> None:
        # Block: PayloadBuild
        revision = {
            "revision_id": action["revision_id"],
            "memory_set_id": action["memory_set_id"],
            "memory_unit_id": action["memory_unit_id"],
            "occurred_at": action["occurred_at"],
            "operation": action["operation"],
            "related_memory_unit_ids": action.get("related_memory_unit_ids", []),
            "before_snapshot": action.get("before_snapshot"),
            "after_snapshot": action.get("after_snapshot"),
            "reason": action["reason"],
            "evidence_event_ids": action.get("evidence_event_ids", []),
        }

        # Block: Insert
        conn.execute(
            """
            INSERT OR REPLACE INTO revisions (
                revision_id,
                memory_set_id,
                memory_unit_id,
                occurred_at,
                operation,
                related_memory_unit_ids_json,
                before_snapshot_json,
                after_snapshot_json,
                reason,
                evidence_event_ids_json,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision["revision_id"],
                revision["memory_set_id"],
                revision["memory_unit_id"],
                revision["occurred_at"],
                revision["operation"],
                self._to_json(revision["related_memory_unit_ids"]),
                self._to_json(revision["before_snapshot"]) if revision["before_snapshot"] is not None else None,
                self._to_json(revision["after_snapshot"]) if revision["after_snapshot"] is not None else None,
                revision["reason"],
                self._to_json(revision["evidence_event_ids"]),
                self._to_json(revision),
            ),
        )

    def _upsert_affect_state(self, conn: sqlite3.Connection, record: dict[str, Any]) -> None:
        # Block: ExistingLookup
        existing_row = conn.execute(
            """
            SELECT affect_state_id, observed_at
            FROM affect_state
            WHERE memory_set_id = ?
              AND layer = ?
              AND target_scope_type = ?
              AND target_scope_key = ?
              AND affect_label = ?
            """,
            (
                record["memory_set_id"],
                record["layer"],
                record["target_scope_type"],
                record["target_scope_key"],
                record["affect_label"],
            ),
        ).fetchone()

        # Block: IdentityResolve
        affect_state_id = record["affect_state_id"]
        observed_at = record["observed_at"]
        if existing_row is not None:
            affect_state_id = existing_row["affect_state_id"]
            observed_at = existing_row["observed_at"]

        payload = {
            **record,
            "affect_state_id": affect_state_id,
            "observed_at": observed_at,
        }

        # Block: Upsert
        conn.execute(
            """
            INSERT OR REPLACE INTO affect_state (
                affect_state_id,
                memory_set_id,
                layer,
                target_scope_type,
                target_scope_key,
                affect_label,
                intensity,
                observed_at,
                updated_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["affect_state_id"],
                payload["memory_set_id"],
                payload["layer"],
                payload["target_scope_type"],
                payload["target_scope_key"],
                payload["affect_label"],
                payload["intensity"],
                payload["observed_at"],
                payload["updated_at"],
                self._to_json(payload),
            ),
        )

    def _table_has_rows(self, conn: sqlite3.Connection, table_name: str) -> bool:
        # Block: Query
        count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        return count > 0

    def _read_jsonl_file(self, file_name: str) -> list[dict[str, Any]]:
        # Block: ReadRecords
        path = self.root_dir / file_name
        if not path.exists():
            return []

        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    records.append(json.loads(stripped))
        return records

    def _to_json(self, payload: Any) -> str:
        # Block: Serialize
        return json.dumps(payload, ensure_ascii=False)
