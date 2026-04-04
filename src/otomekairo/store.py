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

CURRENT_MEMORY_DB_VERSION = 1


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

    def _initialize_memory_db(self) -> None:
        # Block: SchemaSetup
        with self._memory_db() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < CURRENT_MEMORY_DB_VERSION:
                self._apply_schema_v1(conn)
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

    def _to_json(self, payload: dict[str, Any]) -> str:
        # Block: Serialize
        return json.dumps(payload, ensure_ascii=False)
