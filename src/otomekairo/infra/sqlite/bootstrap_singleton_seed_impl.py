"""SQLite bootstrap singleton seed 集約。"""

from __future__ import annotations

import sqlite3

from otomekairo.infra.sqlite.bootstrap_core_singleton_seed_impl import seed_core_singletons
from otomekairo.infra.sqlite.bootstrap_live_state_seed_impl import seed_live_state_singletons


# Block: singleton seed
def seed_singletons(
    *,
    connection: sqlite3.Connection,
    now_ms: int,
) -> None:
    seed_core_singletons(
        connection=connection,
        now_ms=now_ms,
    )
    seed_live_state_singletons(
        connection=connection,
        now_ms=now_ms,
    )
