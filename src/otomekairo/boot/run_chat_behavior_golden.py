"""CLI entrypoint for deterministic chat behavior golden verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from otomekairo.boot.compose_sqlite import create_sqlite_adapter_bundle
from otomekairo.schema.settings import build_default_settings
from otomekairo.usecase.chat_behavior_golden import (
    build_chat_behavior_golden_report,
    format_chat_behavior_golden_report,
)
from otomekairo.usecase.memory_write_e2e import (
    MemoryWriteE2EStores,
    run_memory_write_e2e,
)


# Block: CLI entrypoint
def main() -> None:
    args = _parse_args()
    report = build_chat_behavior_golden_report(
        keep_db=args.keep_db,
        build_memory_write_report=_build_memory_write_report,
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


# Block: Memory write report build
def _build_memory_write_report(db_path: Path) -> dict[str, object]:
    if db_path.exists():
        raise RuntimeError("memory_write_e2e db_path must point to a new database")
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


if __name__ == "__main__":
    main()
