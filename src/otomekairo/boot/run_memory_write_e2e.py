"""Run the deterministic memory write end-to-end verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from otomekairo.boot.compose_sqlite import create_sqlite_adapter_bundle
from otomekairo.schema.settings import build_default_settings
from otomekairo.usecase.memory_write_e2e import (
    MemoryWriteE2EStores,
    format_memory_write_e2e_report,
    run_memory_write_e2e,
)


# Block: CLI entrypoint
def main() -> None:
    args = _parse_args()
    db_path, cleanup_dir = _resolve_db_path(args)
    try:
        report = _build_memory_write_report(db_path=db_path)
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


# Block: Memory write report build
def _build_memory_write_report(*, db_path: Path) -> dict[str, object]:
    _ensure_new_db_path(db_path)
    sqlite_bundle = create_sqlite_adapter_bundle(db_path=db_path)
    return run_memory_write_e2e(
        db_path=db_path,
        default_settings=build_default_settings(),
        stores=MemoryWriteE2EStores(
            runtime_query_store=sqlite_bundle.runtime_query_store,
            cycle_commit_store=sqlite_bundle.cycle_commit_store,
            memory_job_store=sqlite_bundle.memory_job_store,
            write_memory_unit_of_work=sqlite_bundle.write_memory_unit_of_work,
        ),
    )


# Block: Fresh database validation
def _ensure_new_db_path(db_path: Path) -> None:
    if db_path.exists():
        raise RuntimeError("memory_write_e2e db_path must point to a new database")


if __name__ == "__main__":
    main()
