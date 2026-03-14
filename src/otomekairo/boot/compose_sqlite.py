"""Shared SQLite composition helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from otomekairo import __version__
from otomekairo.infra.sqlite.cycle_commit_store import SqliteCycleCommitStore
from otomekairo.infra.sqlite.memory_job_store import SqliteMemoryJobStore
from otomekairo.infra.sqlite.runtime_lease_store import SqliteRuntimeLeaseStore
from otomekairo.infra.sqlite.runtime_query_store import SqliteRuntimeQueryStore
from otomekairo.infra.sqlite.settings_editor_store import SqliteSettingsEditorStore
from otomekairo.infra.sqlite.settings_store import SqliteSettingsStore
from otomekairo.infra.sqlite.ui_event_store import SqliteUiEventStore
from otomekairo.infra.sqlite.unit_of_work import SqliteWriteMemoryUnitOfWork
from otomekairo.infra.sqlite_state_store import SqliteStateStore


# Block: Shared SQLite adapter bundle
@dataclass(frozen=True, slots=True)
class SqliteAdapterBundle:
    backend: SqliteStateStore
    runtime_query_store: SqliteRuntimeQueryStore
    cycle_commit_store: SqliteCycleCommitStore
    memory_job_store: SqliteMemoryJobStore
    settings_store: SqliteSettingsStore
    settings_editor_store: SqliteSettingsEditorStore
    ui_event_store: SqliteUiEventStore
    runtime_lease_store: SqliteRuntimeLeaseStore
    write_memory_unit_of_work: SqliteWriteMemoryUnitOfWork


# Block: Shared SQLite composition
def create_sqlite_adapter_bundle(*, db_path: Path) -> SqliteAdapterBundle:
    backend = SqliteStateStore(
        db_path=db_path,
        initializer_version=__version__,
    )
    backend.initialize()
    settings_editor_store = SqliteSettingsEditorStore(backend)
    return SqliteAdapterBundle(
        backend=backend,
        runtime_query_store=SqliteRuntimeQueryStore(backend),
        cycle_commit_store=SqliteCycleCommitStore(backend),
        memory_job_store=SqliteMemoryJobStore(backend),
        settings_store=SqliteSettingsStore(backend),
        settings_editor_store=settings_editor_store,
        ui_event_store=SqliteUiEventStore(backend),
        runtime_lease_store=SqliteRuntimeLeaseStore(backend),
        write_memory_unit_of_work=SqliteWriteMemoryUnitOfWork(backend),
    )


# Block: Default database path
def default_db_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "core.sqlite3"
