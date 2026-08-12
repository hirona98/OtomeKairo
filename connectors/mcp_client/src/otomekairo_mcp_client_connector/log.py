from __future__ import annotations

import os
import sys
from typing import TextIO


LOG_LEVEL_ORDER = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
}
DEFAULT_LOG_MIN_LEVEL = "WARNING"


def read_log_min_level() -> str:
    raw = os.environ.get("OTOMEKAIRO_DEBUG_LOG_MIN_LEVEL")
    if raw is None or not raw.strip():
        return DEFAULT_LOG_MIN_LEVEL
    normalized = raw.strip().upper()
    if normalized not in LOG_LEVEL_ORDER:
        raise SystemExit(
            "OTOMEKAIRO_DEBUG_LOG_MIN_LEVEL must be one of DEBUG, INFO, WARNING, ERROR."
        )
    return normalized


def emit_log(
    component: str,
    message: str,
    *,
    level: str = "INFO",
    stream: TextIO | None = None,
) -> None:
    normalized_level = level.strip().upper() if isinstance(level, str) else "INFO"
    if normalized_level not in LOG_LEVEL_ORDER:
        normalized_level = "INFO"
    min_level = read_log_min_level()
    if LOG_LEVEL_ORDER[normalized_level] < LOG_LEVEL_ORDER[min_level]:
        return
    target = sys.stderr if stream is None else stream
    print(f"[{component}] {message}", file=target, flush=True)
