"""Combined launcher for the initial local development setup."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from otomekairo import __version__
from otomekairo.infra.sqlite_state_store import SqliteStateStore


# Block: Process labels
WEB_PROCESS = "web"
RUNTIME_PROCESS = "runtime"


# Block: Combined entrypoint
def main() -> None:
    repo_root = _repo_root()
    runtime_already_running = _runtime_already_running(repo_root)
    child_env = _child_environment(repo_root)
    web_url = _web_url(child_env)
    processes = {
        WEB_PROCESS: _start_child_process(
            repo_root=repo_root,
            child_env=child_env,
            module_name="otomekairo.boot.run_web",
        ),
    }
    if not runtime_already_running:
        processes[RUNTIME_PROCESS] = _start_child_process(
            repo_root=repo_root,
            child_env=child_env,
            module_name="otomekairo.boot.run_runtime",
        )
    _install_signal_handlers(processes)
    print("OtomeKairo started")
    print(f"  Web:     {web_url}")
    if runtime_already_running:
        print("  Runtime: already running (reuse existing lease)")
    else:
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


# Block: Browser URL
def _web_url(child_env: dict[str, str]) -> str:
    port = child_env.get("OTOMEKAIRO_PORT", "8000")
    return f"http://127.0.0.1:{port}/"


# Block: Runtime lease check
def _runtime_already_running(repo_root: Path) -> bool:
    store = SqliteStateStore(
        db_path=_default_db_path(repo_root),
        initializer_version=__version__,
    )
    store.initialize()
    status = store.read_status()
    runtime = status.get("runtime")
    if not isinstance(runtime, dict):
        raise RuntimeError("runtime status must be object")
    is_running = runtime.get("is_running")
    if not isinstance(is_running, bool):
        raise RuntimeError("runtime.is_running must be boolean")
    return is_running


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


# Block: Database path
def _default_db_path(repo_root: Path) -> Path:
    return repo_root / "data" / "core.sqlite3"


if __name__ == "__main__":
    main()
