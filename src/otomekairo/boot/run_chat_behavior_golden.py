"""CLI entrypoint for deterministic chat behavior golden verification."""

from __future__ import annotations

import argparse
import json

from otomekairo.usecase.chat_behavior_golden import (
    build_chat_behavior_golden_report,
    format_chat_behavior_golden_report,
)


# Block: CLI entrypoint
def main() -> None:
    args = _parse_args()
    report = build_chat_behavior_golden_report(
        keep_db=args.keep_db,
    )
    if args.output_format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(format_chat_behavior_golden_report(report))


# Block: Argument parsing
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic golden-pack verification for chat behavior",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("text", "json"),
        default="text",
        help="output format",
    )
    parser.add_argument(
        "--keep-db",
        action="store_true",
        help="keep the generated temporary database and include its path in the report",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
