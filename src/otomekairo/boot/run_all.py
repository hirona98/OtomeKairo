"""Combined launcher for the initial local development setup."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


# Block: Process labels
WEB_PROCESS = "web"
RUNTIME_PROCESS = "runtime"


# Block: Combined entrypoint
def main() -> None:
    repo_root = _repo_root()
    child_env = _child_environment(repo_root)
    processes = {
        WEB_PROCESS: _start_child_process(
            repo_root=repo_root,
            child_env=child_env,
            module_name="otomekairo.boot.run_web",
        ),
        RUNTIME_PROCESS: _start_child_process(
            repo_root=repo_root,
            child_env=child_env,
            module_name="otomekairo.boot.run_runtime",
        ),
    }
    _install_signal_handlers(processes)
    print("OtomeKairo started")
    print("  Web:     http://127.0.0.1:8000/")
    print("  Runtime: running")
    try:
        _wait_for_exit(processes)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
    finally:
        _stop_child_processes(processes)


# Block: Child process start
def _start_child_process(
    *,
    repo_root: Path,
    child_env: dict[str, str],
    module_name: str,
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", module_name],
        cwd=repo_root,
        env=child_env,
        text=True,
    )


# Block: Child environment
def _child_environment(repo_root: Path) -> dict[str, str]:
    child_env = dict(os.environ)
    pythonpath_entries = [str(repo_root / "src")]
    existing_pythonpath = child_env.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    child_env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    return child_env


# Block: Signal registration
def _install_signal_handlers(processes: dict[str, subprocess.Popen[str]]) -> None:
    def handle_signal(signum: int, _frame: object) -> None:
        print(f"Received signal: {signum}")
        _stop_child_processes(processes)
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


# Block: Process wait
def _wait_for_exit(processes: dict[str, subprocess.Popen[str]]) -> None:
    while True:
        for process_name, process in processes.items():
            return_code = process.poll()
            if return_code is None:
                continue
            if return_code != 0:
                raise RuntimeError(f"{process_name} exited with code {return_code}")
            raise SystemExit(0)
        time.sleep(0.5)


# Block: Process stop
def _stop_child_processes(processes: dict[str, subprocess.Popen[str]]) -> None:
    for process in processes.values():
        if process.poll() is not None:
            continue
        process.terminate()
    deadline = time.time() + 5.0
    for process in processes.values():
        while process.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        if process.poll() is None:
            process.kill()
    for process in processes.values():
        if process.poll() is None:
            continue
        process.wait()


# Block: Repository root
def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


if __name__ == "__main__":
    main()
