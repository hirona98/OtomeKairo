"""SQLite-backed runtime lease adapter."""

from __future__ import annotations

from dataclasses import dataclass

from otomekairo.infra.sqlite_state_store import SqliteStateStore


# Block: Runtime lease adapter
@dataclass(frozen=True, slots=True)
class SqliteRuntimeLeaseStore:
    backend: SqliteStateStore

    def acquire_runtime_lease(
        self,
        *,
        owner_token: str,
        lease_ttl_ms: int,
    ) -> None:
        self.backend.acquire_runtime_lease(
            owner_token=owner_token,
            lease_ttl_ms=lease_ttl_ms,
        )

    def release_runtime_lease(self, *, owner_token: str) -> None:
        self.backend.release_runtime_lease(owner_token=owner_token)

    def sync_pending_commit_logs(self, *, max_commits: int = 8) -> int:
        return self.backend.sync_pending_commit_logs(max_commits=max_commits)
