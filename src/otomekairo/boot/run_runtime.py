"""Runtime boot entrypoint."""

from __future__ import annotations

import logging
import signal

from otomekairo.infra.logging_setup import configure_process_logging
from otomekairo.runtime.main_loop import build_runtime_loop


# Block: Module logger
logger = logging.getLogger(__name__)


# Block: Runtime main entrypoint
def main() -> None:
    configure_process_logging(process_name="runtime")
    logger.info("starting runtime process")
    runtime_loop = build_runtime_loop()
    _install_signal_handlers()
    runtime_loop.run_forever()


# Block: Runtime signal handlers
def _install_signal_handlers() -> None:
    def handle_signal(signum: int, _frame: object) -> None:
        logger.info("received shutdown signal", extra={"signal_number": signum})
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


if __name__ == "__main__":
    main()
