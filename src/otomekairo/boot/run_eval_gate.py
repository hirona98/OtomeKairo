"""CLI entrypoint for the unified deterministic evaluation gate."""

from __future__ import annotations

import argparse
import json

from otomekairo.usecase.eval_gate import (
    format_eval_gate_report,
    run_eval_gate,
)


# Block: CLI entrypoint
def main() -> None:
    args = _parse_args()
    report = run_eval_gate(
        keep_db=args.keep_db,
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


if __name__ == "__main__":
    main()
