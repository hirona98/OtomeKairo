"""Web server launcher."""

from __future__ import annotations

import logging
import os

import uvicorn

from otomekairo.infra.logging_setup import configure_access_logger_filter, configure_process_logging


# Block: Module logger
logger = logging.getLogger(__name__)


# Block: Uvicorn launcher
def main() -> None:
    configure_process_logging(process_name="web")
    configure_access_logger_filter()
    host = os.environ.get("OTOMEKAIRO_HOST", "0.0.0.0")
    port = int(os.environ.get("OTOMEKAIRO_PORT", "8000"))
    logger.info("starting web server", extra={"host": host, "port": port})
    uvicorn.run(
        "otomekairo.web.app:create_app",
        factory=True,
        host=host,
        port=port,
        access_log=True,
        log_config=None,
    )


# Block: Module entrypoint
if __name__ == "__main__":
    main()
