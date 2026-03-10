"""retrieval triage report を出力する CLI。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from otomekairo import __version__
from otomekairo.infra.sqlite_state_store import SqliteStateStore
from otomekairo.usecase.retrieval_triage import (
    build_retrieval_triage_report,
    format_retrieval_triage_report,
)


# Block: CLI entrypoint
def main() -> None:
    args = _parse_args()
    store = SqliteStateStore(
        db_path=args.db_path,
        initializer_version=__version__,
    )
    store.initialize()
    retrieval_runs = store.read_recent_retrieval_runs(limit=args.limit)
    report = build_retrieval_triage_report(
        retrieval_runs,
        max_packets=args.max_packets,
        only_flagged=args.only_flagged,
    )
    if args.output_format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(format_retrieval_triage_report(report))


# Block: Argument parsing
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build retrieval triage report from retrieval_runs",
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
        default=200,
        help="number of recent retrieval runs to inspect",
    )
    parser.add_argument(
        "--max-packets",
        type=int,
        default=20,
        help="maximum number of review packets to return",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("text", "json"),
        default="text",
        help="output format",
    )
    parser.add_argument(
        "--include-all",
        dest="only_flagged",
        action="store_false",
        help="include unflagged runs as review packets too",
    )
    parser.set_defaults(only_flagged=True)
    return parser.parse_args()


# Block: Default database path
def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "core.sqlite3"


if __name__ == "__main__":
    main()
