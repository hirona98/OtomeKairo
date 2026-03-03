"""Web server launcher."""

from __future__ import annotations

import logging
import os
import signal

import uvicorn

from otomekairo.infra.logging_setup import configure_access_logger_filter, configure_process_logging


# Block: Module logger
logger = logging.getLogger(__name__)


# Block: Managed Uvicorn server
class ManagedSignalUvicornServer(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        return


# Block: Uvicorn launcher
def main() -> None:
    configure_process_logging(process_name="web")
    configure_access_logger_filter()
    host = os.environ.get("OTOMEKAIRO_HOST", "0.0.0.0")
    port = int(os.environ.get("OTOMEKAIRO_PORT", "8000"))
    logger.info("starting web server", extra={"host": host, "port": port})
    config = uvicorn.Config(
        "otomekairo.web.app:create_app",
        factory=True,
        host=host,
        port=port,
        access_log=True,
        log_config=None,
    )
    server = ManagedSignalUvicornServer(config)
    _install_signal_handlers(server)
    server.run()


# Block: Web signal handlers
def _install_signal_handlers(server: ManagedSignalUvicornServer) -> None:
    signal_count = {"count": 0}

    def handle_signal(signum: int, _frame: object) -> None:
        signal_count["count"] += 1
        logger.info(
            "received shutdown signal",
            extra={
                "signal_number": signum,
                "signal_count": signal_count["count"],
            },
        )
        if signal_count["count"] == 1:
            server.should_exit = True
            return
        server.force_exit = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


# Block: Module entrypoint
if __name__ == "__main__":
    main()
