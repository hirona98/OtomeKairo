"""SQLite-backed runtime lease adapter."""

from __future__ import annotations

from dataclasses import dataclass

from otomekairo.infra.sqlite.backend import SqliteBackend
from otomekairo.infra.sqlite.runtime_lease_impl import (
    acquire_runtime_lease,
    release_runtime_lease,
    sync_pending_commit_logs,
)


# Block: Runtime lease adapter
@dataclass(frozen=True, slots=True)
class SqliteRuntimeLeaseStore:
    backend: SqliteBackend

    def acquire_runtime_lease(
        self,
        *,
        owner_token: str,
        lease_ttl_ms: int,
    ) -> None:
        acquire_runtime_lease(
            self.backend,
            owner_token=owner_token,
            lease_ttl_ms=lease_ttl_ms,
        )

    def release_runtime_lease(self, *, owner_token: str) -> None:
        release_runtime_lease(self.backend, owner_token=owner_token)

    def sync_pending_commit_logs(self, *, max_commits: int = 8) -> int:
        return sync_pending_commit_logs(self.backend, max_commits=max_commits)
