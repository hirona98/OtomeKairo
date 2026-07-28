from __future__ import annotations

import argparse
from pathlib import Path

from .app import MicrophoneConnector
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the OtomeKairo microphone connector."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to connector config JSON.",
    )
    args = parser.parse_args()
    MicrophoneConnector(load_config(args.config)).run_forever()


if __name__ == "__main__":
    main()
