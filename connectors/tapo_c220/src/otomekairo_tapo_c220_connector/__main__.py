from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .app import TapoC220Connector
from .config import ConfigError, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the OtomeKairo Tapo C220 connector.")
    parser.add_argument("--config", type=Path, default=None, help="Path to connector config JSON.")
    parser.add_argument("--print-hello", action="store_true", help="Print the hello payload and exit.")
    parser.add_argument("--check-device", action="store_true", help="Check C220 control and RTSP access without moving.")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        connector = TapoC220Connector(config)
        if args.print_hello:
            connector.print_hello()
            return 0
        if args.check_device:
            return connector.check_device()
        connector.run_forever()
        return 0
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
