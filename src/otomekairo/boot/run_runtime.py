"""Runtime boot entrypoint."""

from __future__ import annotations

import logging

from otomekairo.infra.logging_setup import configure_process_logging
from otomekairo.runtime.main_loop import build_runtime_loop


# Block: Module logger
logger = logging.getLogger(__name__)


# Block: Runtime main entrypoint
def main() -> None:
    configure_process_logging(process_name="runtime")
    logger.info("starting runtime process")
    runtime_loop = build_runtime_loop()
    runtime_loop.run_forever()


if __name__ == "__main__":
    main()
