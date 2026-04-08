from __future__ import annotations

import os
import ssl
from pathlib import Path

from otomekairo.http_server import OtomeKairoHttpServer
from otomekairo.service import OtomeKairoService


# メイン
def main() -> None:
    # 環境
    host = os.environ.get("OTOMEKAIRO_HOST", "127.0.0.1")
    port = int(os.environ.get("OTOMEKAIRO_PORT", "55601"))
    cert_file = os.environ.get("OTOMEKAIRO_TLS_CERT_FILE")
    key_file = os.environ.get("OTOMEKAIRO_TLS_KEY_FILE")
    root_dir = Path(os.environ.get("OTOMEKAIRO_DATA_DIR", "var/otomekairo"))

    # TLS検証
    if not cert_file or not key_file:
        raise SystemExit("OTOMEKAIRO_TLS_CERT_FILE and OTOMEKAIRO_TLS_KEY_FILE are required.")

    # サービス
    service = OtomeKairoService(root_dir=root_dir)
    server = OtomeKairoHttpServer((host, port), service)

    # TLSコンテキスト
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)

    # スケジューラー開始
    service.start_background_wake_scheduler()
    service.start_background_desktop_watch()

    # 起動処理
    print(f"OtomeKairo listening on https://{host}:{port}")
    try:
        # ループ
        server.serve_forever()
    finally:
        # 終了処理
        service.close_event_streams()
        service.stop_background_desktop_watch()
        service.stop_background_wake_scheduler()
        server.server_close()


if __name__ == "__main__":
    main()
