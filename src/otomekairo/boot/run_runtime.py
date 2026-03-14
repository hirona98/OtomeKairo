"""Runtime boot entrypoint."""

from __future__ import annotations

import logging
import signal
from pathlib import Path

from otomekairo.boot.compose_runtime import create_runtime_loop
from otomekairo.infra.developer_config import load_developer_config
from otomekairo.infra.logging_setup import configure_process_logging
from otomekairo.runtime.main_loop import RuntimeLoop


# Block: Module logger
logger = logging.getLogger(__name__)


# Block: Runtime main entrypoint
def main() -> None:
    developer_config = load_developer_config(_repo_root())
    configure_process_logging(
        process_name="runtime",
        developer_config=developer_config,
    )
    logger.info("starting runtime process")
    runtime_loop = create_runtime_loop()
    _install_signal_handlers(runtime_loop)
    runtime_loop.run_forever()


# Block: Runtime signal handlers
def _install_signal_handlers(runtime_loop: RuntimeLoop) -> None:
    def handle_signal(signum: int, _frame: object) -> None:
        del signum
        runtime_loop.request_stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


# Block: Repository root helper
def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


if __name__ == "__main__":
    main()
