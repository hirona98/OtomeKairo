"""Run the deterministic memory write end-to-end verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from otomekairo.usecase.memory_write_e2e import (
    format_memory_write_e2e_report,
    run_memory_write_e2e,
)


# Block: CLI entrypoint
def main() -> None:
    args = _parse_args()
    db_path, cleanup_dir = _resolve_db_path(args)
    try:
        report = run_memory_write_e2e(db_path=db_path)
        if args.output_format == "json":
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(format_memory_write_e2e_report(report))
    finally:
        if cleanup_dir is not None:
            shutil.rmtree(cleanup_dir)


# Block: Argument parsing
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run scripted memory write e2e verification on a fresh SQLite database",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="path to a new core.sqlite3 to create for this verification run",
    )
    parser.add_argument(
        "--keep-db",
        action="store_true",
        help="keep the generated temporary database directory",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("text", "json"),
        default="text",
        help="output format",
    )
    return parser.parse_args()


# Block: Database path resolution
def _resolve_db_path(args: argparse.Namespace) -> tuple[Path, Path | None]:
    db_path = args.db_path
    if db_path is not None:
        return (db_path, None)
    temp_dir = Path(
        tempfile.mkdtemp(prefix="otomekairo-memory-write-e2e-")
    )
    if args.keep_db:
        return (temp_dir / "core.sqlite3", None)
    return (temp_dir / "core.sqlite3", temp_dir)


if __name__ == "__main__":
    main()
