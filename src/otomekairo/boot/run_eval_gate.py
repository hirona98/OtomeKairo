"""CLI entrypoint for the unified deterministic evaluation gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from otomekairo.boot.compose_sqlite import create_sqlite_adapter_bundle
from otomekairo.schema.settings import build_default_settings
from otomekairo.usecase.bootstrap_init_smoke import (
    BootstrapInitSmokeStores,
    run_bootstrap_init_smoke,
)
from otomekairo.usecase.chat_behavior_golden import build_chat_behavior_golden_report
from otomekairo.usecase.eval_gate import (
    format_eval_gate_report,
    run_eval_gate,
)
from otomekairo.usecase.memory_write_e2e import (
    MemoryWriteE2EStores,
    run_memory_write_e2e,
)
from otomekairo.usecase.stable_context_contract_smoke import (
    StableContextContractSmokeStores,
    run_stable_context_contract_smoke,
)


# Block: CLI entrypoint
def main() -> None:
    args = _parse_args()
    report = run_eval_gate(
        keep_db=args.keep_db,
        build_bootstrap_init_report=_build_bootstrap_init_report,
        build_stable_context_report=_build_stable_context_report,
        build_chat_behavior_report=_build_chat_behavior_report,
    )
    if args.output_format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(format_eval_gate_report(report))


# Block: Argument parsing
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic merge-time evaluation gate",
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
        help="keep the generated temporary database used by the golden chat behavior check",
    )
    return parser.parse_args()


# Block: Bootstrap smoke report build
def _build_bootstrap_init_report(keep_db: bool) -> dict[str, object]:
    return run_bootstrap_init_smoke(
        keep_db=keep_db,
        build_stores=_build_bootstrap_init_stores,
    )


# Block: Stable context smoke report build
def _build_stable_context_report(keep_db: bool) -> dict[str, object]:
    return run_stable_context_contract_smoke(
        keep_db=keep_db,
        build_stores=_build_stable_context_stores,
    )


# Block: Chat behavior golden report build
def _build_chat_behavior_report(keep_db: bool) -> dict[str, object]:
    return build_chat_behavior_golden_report(
        keep_db=keep_db,
        build_memory_write_report=_build_memory_write_report,
    )


# Block: Bootstrap store factory
def _build_bootstrap_init_stores(db_path: Path) -> BootstrapInitSmokeStores:
    sqlite_bundle = create_sqlite_adapter_bundle(db_path=db_path)
    return BootstrapInitSmokeStores(
        settings_editor_store=sqlite_bundle.settings_editor_store,
    )


# Block: Stable context store factory
def _build_stable_context_stores(db_path: Path) -> StableContextContractSmokeStores:
    sqlite_bundle = create_sqlite_adapter_bundle(db_path=db_path)
    return StableContextContractSmokeStores(
        runtime_query_store=sqlite_bundle.runtime_query_store,
    )


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
