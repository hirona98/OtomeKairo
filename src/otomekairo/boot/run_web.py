"""Web server launcher."""

from __future__ import annotations

import copy
import logging
import os

import uvicorn
from uvicorn.config import LOGGING_CONFIG


# Block: Access log filter
class SuppressFrequentAccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "\"GET /api/status " in message:
            return False
        if "\"GET /api/chat/stream " in message:
            return False
        return True


# Block: Uvicorn log config
def _uvicorn_log_config() -> dict[str, object]:
    log_config = copy.deepcopy(LOGGING_CONFIG)
    filters = log_config.setdefault("filters", {})
    if not isinstance(filters, dict):
        raise RuntimeError("uvicorn log_config.filters must be object")
    filters["suppress_frequent_access"] = {
        "()": SuppressFrequentAccessLogFilter,
    }
    handlers = log_config.get("handlers")
    if not isinstance(handlers, dict):
        raise RuntimeError("uvicorn log_config.handlers must be object")
    access_handler = handlers.get("access")
    if not isinstance(access_handler, dict):
        raise RuntimeError("uvicorn access handler must be object")
    access_handler["filters"] = ["suppress_frequent_access"]
    return log_config


# Block: Uvicorn launcher
def main() -> None:
    host = os.environ.get("OTOMEKAIRO_HOST", "0.0.0.0")
    port = int(os.environ.get("OTOMEKAIRO_PORT", "8000"))
    uvicorn.run(
        "otomekairo.web.app:create_app",
        factory=True,
        host=host,
        port=port,
        access_log=True,
        log_config=_uvicorn_log_config(),
    )


# Block: Module entrypoint
if __name__ == "__main__":
    main()
