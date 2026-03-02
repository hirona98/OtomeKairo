"""Runtime boot entrypoint."""

from __future__ import annotations

import os

from otomekairo.runtime.main_loop import build_runtime_loop


# Block: Runtime main entrypoint
def main() -> None:
    runtime_loop = build_runtime_loop()
    poll_interval_ms = int(os.environ.get("OTOMEKAIRO_RUNTIME_POLL_MS", "500"))
    runtime_loop.run_forever(poll_interval_ms=poll_interval_ms)


if __name__ == "__main__":
    main()
