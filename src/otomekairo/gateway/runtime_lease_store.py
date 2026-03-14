"""Runtime lease and replay port."""

from __future__ import annotations

from typing import Protocol


# Block: Runtime lease contract
class RuntimeLeaseStore(Protocol):
    def acquire_runtime_lease(
        self,
        *,
        owner_token: str,
        lease_ttl_ms: int,
    ) -> None:
        ...

    def release_runtime_lease(self, *, owner_token: str) -> None:
        ...

    def sync_pending_commit_logs(self, *, max_commits: int = 8) -> int:
        ...
