from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


TRACE_PATH_ENV = "OTOMEKAIRO_MCP_TRACE_PATH"
MASKED = "<masked>"
_SECRET_KEY_PARTS = ("authorization", "api_key", "apikey", "x-api-key", "token", "password", "secret", "elyth_api_key")
_BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


class TraceWriter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def write(self, *, boundary: str, direction: str, kind: str, payload: Any) -> None:
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "boundary": boundary,
            "direction": direction,
            "kind": kind,
            "payload": mask_secrets(payload),
        }
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.write("\n")


def trace_writer_from_env(environ: Mapping[str, str] | None = None) -> TraceWriter | None:
    env = environ if environ is not None else os.environ
    path = env.get(TRACE_PATH_ENV, "").strip()
    if not path:
        return None
    return TraceWriter(path)


def mask_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        masked: dict[Any, Any] = {}
        for key, item in value.items():
            if _is_secret_key(str(key)):
                masked[key] = MASKED
            else:
                masked[key] = mask_secrets(item)
        return masked
    if isinstance(value, list):
        return [mask_secrets(item) for item in value]
    if isinstance(value, str):
        return _BEARER_PATTERN.sub(f"Bearer {MASKED}", value)
    return value


def _is_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part.replace("-", "_") in normalized for part in _SECRET_KEY_PARTS)
