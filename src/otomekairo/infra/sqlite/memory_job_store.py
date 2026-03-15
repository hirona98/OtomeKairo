"""SQLite-backed memory job adapter."""

from __future__ import annotations

from dataclasses import dataclass

from otomekairo.infra.sqlite.memory_job_impl import (
    claim_next_memory_job,
    ensure_claimed_memory_job,
    fail_claimed_memory_job,
    mark_memory_job_completed,
)
from otomekairo.infra.sqlite_store_job_helpers import (
    _normalize_embedding_scopes,
    _resolve_embedding_source_text,
)
from otomekairo.infra.sqlite_store_legacy_runtime import _now_ms
from otomekairo.infra.sqlite_store_vectors import (
    _build_embedding_blob,
    _delete_vec_index_row,
    _mark_vec_item_unsearchable,
    _replace_vec_index_row,
    _upsert_vec_item_row,
)
from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.schema.store_errors import StoreValidationError
from otomekairo.schema.runtime_types import MemoryJobRecord


# Block: Memory job adapter
@dataclass(frozen=True, slots=True)
class SqliteMemoryJobStore:
    backend: SqliteBackend

    def claim_next_memory_job(self) -> MemoryJobRecord | None:
        return claim_next_memory_job(self.backend)

    def fail_claimed_memory_job(
        self,
        *,
        memory_job: MemoryJobRecord,
        error: Exception,
        max_tries: int,
    ) -> None:
        fail_claimed_memory_job(
            self.backend,
            memory_job=memory_job,
            error=error,
            max_tries=max_tries,
        )

    def complete_embedding_sync_job(self, *, memory_job: MemoryJobRecord) -> int:
        if memory_job.job_kind != "embedding_sync":
            raise StoreValidationError("memory_job.job_kind must be embedding_sync")
        embedding_model = memory_job.payload["embedding_model"]
        requested_scopes = memory_job.payload["requested_scopes"]
        targets = memory_job.payload["targets"]
        if not isinstance(embedding_model, str) or not embedding_model:
            raise StoreValidationError("embedding_sync embedding_model must be non-empty string")
        if not isinstance(requested_scopes, list) or not requested_scopes:
            raise StoreValidationError("embedding_sync requested_scopes must not be empty")
        if not isinstance(targets, list) or not targets:
            raise StoreValidationError("embedding_sync targets must not be empty")
        normalized_scopes = _normalize_embedding_scopes(requested_scopes)
        now_ms = _now_ms()
        updated_scope_count = 0
        with self.backend._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            ensure_claimed_memory_job(
                connection=connection,
                job_id=memory_job.job_id,
            )
            for target in targets:
                entity_type = str(target["entity_type"])
                entity_id = str(target["entity_id"])
                source_updated_at = int(target["source_updated_at"])
                current_searchable = bool(target["current_searchable"])
                # Block: Scope application
                for embedding_scope in normalized_scopes:
                    if current_searchable:
                        embedding_blob = _build_embedding_blob(
                            source_text=_resolve_embedding_source_text(
                                connection=connection,
                                entity_type=entity_type,
                                entity_id=entity_id,
                            ),
                            embedding_model=embedding_model,
                            embedding_scope=embedding_scope,
                        )
                        vec_row_id = _upsert_vec_item_row(
                            connection=connection,
                            entity_type=entity_type,
                            entity_id=entity_id,
                            embedding_model=embedding_model,
                            embedding_scope=embedding_scope,
                            source_updated_at=source_updated_at,
                            embedding_blob=embedding_blob,
                        )
                        _replace_vec_index_row(
                            connection=connection,
                            vec_row_id=vec_row_id,
                            embedding_blob=embedding_blob,
                        )
                    else:
                        vec_row_id = _mark_vec_item_unsearchable(
                            connection=connection,
                            entity_type=entity_type,
                            entity_id=entity_id,
                            embedding_model=embedding_model,
                            embedding_scope=embedding_scope,
                            source_updated_at=source_updated_at,
                        )
                        if vec_row_id is not None:
                            _delete_vec_index_row(
                                connection=connection,
                                vec_row_id=vec_row_id,
                            )
                    updated_scope_count += 1
            mark_memory_job_completed(
                connection=connection,
                job_id=memory_job.job_id,
                completed_at=now_ms,
            )
        return updated_scope_count
