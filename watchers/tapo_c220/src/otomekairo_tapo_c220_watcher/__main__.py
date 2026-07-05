from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .app import TapoC220Watcher
from .config import ConfigError, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the OtomeKairo Tapo C220 watcher.")
    parser.add_argument("--config", type=Path, default=None, help="Path to watcher config JSON.")
    args = parser.parse_args()

    try:
        watcher = TapoC220Watcher(load_config(args.config))
        watcher.run_forever()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
