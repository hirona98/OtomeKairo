from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .app import ApiError, run
from .config import ConfigError, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Report Pico USB environment sensors to OtomeKairo")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true", help="Measure and submit once, print the report, then exit")
    args = parser.parse_args()
    level = os.environ.get("OTOMEKAIRO_DEBUG_LOG_MIN_LEVEL", "WARNING").strip().upper() or "WARNING"
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        parser.error("OTOMEKAIRO_DEBUG_LOG_MIN_LEVEL must be DEBUG, INFO, WARNING or ERROR")
    logging.basicConfig(level=level, format="[pico-environment] %(levelname)s %(message)s")
    try:
        return run(load_config(args.config), once=args.once)
    except (ConfigError, ApiError) as exc:
        logging.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
