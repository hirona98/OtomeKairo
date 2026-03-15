"""SQLite の memory job 実装集約。"""

from __future__ import annotations

from otomekairo.infra.sqlite.memory_job_claim_impl import (
    claim_next_memory_job,
    ensure_claimed_memory_job,
    fail_claimed_memory_job,
    mark_memory_job_completed,
)
from otomekairo.infra.sqlite.memory_job_enqueue_impl import (
    enqueue_embedding_sync_jobs,
    enqueue_write_memory_jobs,
    find_memory_job_id_by_idempotency_key,
    insert_memory_job,
)

__all__ = [
    "claim_next_memory_job",
    "ensure_claimed_memory_job",
    "enqueue_embedding_sync_jobs",
    "enqueue_write_memory_jobs",
    "fail_claimed_memory_job",
    "find_memory_job_id_by_idempotency_key",
    "insert_memory_job",
    "mark_memory_job_completed",
]
