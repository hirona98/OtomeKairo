"""Runtime boot entrypoint."""

from __future__ import annotations

from otomekairo.runtime.main_loop import build_runtime_loop


# Block: Runtime main entrypoint
def main() -> None:
    runtime_loop = build_runtime_loop()
    runtime_loop.run_forever()


if __name__ == "__main__":
    main()
