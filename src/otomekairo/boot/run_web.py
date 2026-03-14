"""Web server launcher."""

from __future__ import annotations

import logging
import os
import signal
from pathlib import Path

import uvicorn

from otomekairo.infra.developer_config import load_developer_config
from otomekairo.infra.logging_setup import configure_access_logger_filter, configure_process_logging


# Block: Module logger
logger = logging.getLogger(__name__)


# Block: Managed Uvicorn server
class ManagedSignalUvicornServer(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        return


# Block: Uvicorn launcher
def main() -> None:
    developer_config = load_developer_config(_repo_root())
    configure_process_logging(
        process_name="web",
        developer_config=developer_config,
    )
    configure_access_logger_filter()
    host = os.environ.get("OTOMEKAIRO_HOST", "0.0.0.0")
    port = int(os.environ.get("OTOMEKAIRO_PORT", "8000"))
    logger.info("starting web server", extra={"host": host, "port": port})
    config = uvicorn.Config(
        "otomekairo.boot.compose_web:create_app",
        factory=True,
        host=host,
        port=port,
        access_log=True,
        lifespan="off",
        log_config=None,
    )
    server = ManagedSignalUvicornServer(config)
    _install_signal_handlers(server)
    server.run()


# Block: Web signal handlers
def _install_signal_handlers(server: ManagedSignalUvicornServer) -> None:
    def handle_signal(signum: int, _frame: object) -> None:
        server.handle_exit(signum, _frame)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


# Block: Repository root helper
def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


# Block: Module entrypoint
if __name__ == "__main__":
    main()
