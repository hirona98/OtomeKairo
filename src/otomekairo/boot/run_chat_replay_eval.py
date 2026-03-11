"""CLI entrypoint for chat replay evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from otomekairo.usecase.chat_replay_eval import (
    build_chat_replay_eval_report,
    format_chat_replay_eval_report,
)


# Block: CLI entrypoint
def main() -> None:
    args = _parse_args()
    report = build_chat_replay_eval_report(
        db_path=args.db_path,
        limit=args.limit,
    )
    if args.output_format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(format_chat_replay_eval_report(report))


# Block: Argument parsing
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build replay-oriented evaluation report from recent chat cycles",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=_default_db_path(),
        help="path to core.sqlite3",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="number of recent chat cycles to inspect",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("text", "json"),
        default="text",
        help="output format",
    )
    return parser.parse_args()


# Block: Default database path
def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "core.sqlite3"


if __name__ == "__main__":
    main()
