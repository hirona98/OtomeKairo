from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from connector_runtime_config_ready import FATAL, SKIP, PreflightError, RuntimeConfigNotFound  # noqa: E402
from otomekairo_tapo_c220_watcher.__main__ import main  # noqa: E402
from watcher_runtime_config_ready import fetch_runtime_config, load_settings  # noqa: E402


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _wait_for_server() -> None:
    # compound debug は各構成を並列起動するため、watcher の設定取得前に server の TCP 待機を待つ。
    server_url = os.environ.get("OTOMEKAIRO_SERVER_URL", "https://127.0.0.1:55601").strip()
    parsed = urlparse(server_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    deadline = time.monotonic() + 30.0

    while True:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError:
            if time.monotonic() >= deadline:
                print(f"server の起動待ちが timeout しました: {host}:{port}", file=sys.stderr)
                raise SystemExit(2)
            time.sleep(0.2)


def _runtime_config_ready() -> int:
    try:
        settings = load_settings(default_watcher_id="watcher:camera")
        runtime_config = fetch_runtime_config(settings)
    except RuntimeConfigNotFound as exc:
        print(f"Tapo C220 watcher debug をスキップします: {exc}", file=sys.stderr)
        print("先に camera source の watcher を登録してください。", file=sys.stderr)
        return SKIP
    except PreflightError as exc:
        print(f"Tapo C220 watcher debug の preflight に失敗しました: {exc}", file=sys.stderr)
        return FATAL

    watcher = runtime_config.get("watcher")
    camera_source = runtime_config.get("camera_source")
    if not isinstance(watcher, dict):
        print("Tapo C220 watcher debug をスキップします: watcher 定義がありません。", file=sys.stderr)
        return SKIP
    if not isinstance(camera_source, dict):
        print("Tapo C220 watcher debug をスキップします: camera source がありません。", file=sys.stderr)
        return SKIP
    # enabled=false でも idle 常駐する（本体 launcher と同じ）。
    return 0


if __name__ == "__main__":
    os.environ.setdefault("OTOMEKAIRO_SERVER_URL", "https://127.0.0.1:55601")
    os.environ.setdefault("OTOMEKAIRO_DATA_DIR", str(_repo_root() / "var" / "otomekairo"))
    _wait_for_server()
    status = _runtime_config_ready()
    if status == 0:
        raise SystemExit(main())
    if status == FATAL:
        raise SystemExit(2)
