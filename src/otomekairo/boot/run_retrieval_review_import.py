"""編集済み retrieval triage report を quarantine_memory へ取り込む CLI。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from otomekairo import __version__
from otomekairo.infra.sqlite_state_store import SqliteStateStore
from otomekairo.usecase.retrieval_review_import import (
    apply_retrieval_review_import,
    format_retrieval_review_import_summary,
)


# Block: CLI entrypoint
def main() -> None:
    args = _parse_args()
    store = SqliteStateStore(
        db_path=args.db_path,
        initializer_version=__version__,
    )
    store.initialize()
    review_report = json.loads(args.input.read_text(encoding="utf-8"))
    summary = apply_retrieval_review_import(
        store=store,
        review_report=review_report,
    )
    if args.output_format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    print(format_retrieval_review_import_summary(summary))


# Block: Argument parsing
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import reviewed retrieval triage report into quarantine_memory jobs",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=_default_db_path(),
        help="path to core.sqlite3",
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="path to edited retrieval triage report json",
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
