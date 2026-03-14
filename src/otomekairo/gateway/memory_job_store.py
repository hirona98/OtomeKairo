"""Long-cycle memory job port."""

from __future__ import annotations

from typing import Protocol

from otomekairo.schema.runtime_types import MemoryJobRecord


# Block: Memory job contract
class MemoryJobStore(Protocol):
    def claim_next_memory_job(self) -> MemoryJobRecord | None:
        ...

    def fail_claimed_memory_job(
        self,
        *,
        memory_job: MemoryJobRecord,
        error: Exception,
        max_tries: int,
    ) -> None:
        ...

    def complete_embedding_sync_job(self, *, memory_job: MemoryJobRecord) -> int:
        ...
