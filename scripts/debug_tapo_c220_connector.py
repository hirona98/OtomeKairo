from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from otomekairo_tapo_c220_connector.__main__ import main


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_optional_env_file() -> None:
    # ローカル上書き用の .env を読む。未作成でも F5 起動は成立する。
    env_path = _repo_root() / "connectors" / "tapo_c220" / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _wait_for_server() -> None:
    # compound debug は各構成を並列起動するため、connector の設定取得前に server の TCP 待機を待つ。
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


if __name__ == "__main__":
    _load_optional_env_file()
    _wait_for_server()
    raise SystemExit(main())
