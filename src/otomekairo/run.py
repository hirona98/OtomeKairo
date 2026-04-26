from __future__ import annotations

import os
import ssl
from pathlib import Path

from otomekairo.http_server import OtomeKairoHttpServer
from otomekairo.service import OtomeKairoService
from otomekairo.service_common import debug_log


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
    debug_log("Run", f"starting host={host} port={port} data_dir={root_dir}")
    debug_log("Run", f"tls cert={cert_file} key={key_file}")
    service = OtomeKairoService(root_dir=root_dir)
    server = OtomeKairoHttpServer((host, port), service)

    # TLSコンテキスト
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)

    # スケジューラー開始
    debug_log("Run", "starting background workers")
    service.start_background_memory_postprocess_worker()
    service.start_background_wake_scheduler()
    service.start_background_desktop_watch()
    debug_log("Run", "background workers started")

    # 起動処理
    debug_log("Run", f"listening https://{host}:{port}")
    try:
        # ループ
        server.serve_forever()
    except KeyboardInterrupt:
        debug_log("Run", "shutdown requested by keyboard interrupt")
    finally:
        # 終了処理
        debug_log("Run", "shutdown begin")
        service.close_event_streams()
        service.stop_background_desktop_watch()
        service.stop_background_wake_scheduler()
        service.stop_background_memory_postprocess_worker()
        server.server_close()
        debug_log("Run", "shutdown complete")


if __name__ == "__main__":
    main()
