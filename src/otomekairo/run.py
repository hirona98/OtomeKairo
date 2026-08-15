from __future__ import annotations

import errno
import os
import re
import shutil
import ssl
import subprocess
import sys
from pathlib import Path

from otomekairo.http_server import OtomeKairoHttpServer
from otomekairo.service.app import OtomeKairoService
from otomekairo.service.common import configure_debug_log_file, debug_log


def _port_in_use_hint(host: str, port: int) -> str:
    # bind 失敗時に、誰が掴んでいるかと対処手順をまとめて出す。
    holders: list[str] = []
    if shutil.which("ss"):
        try:
            completed = subprocess.run(
                ["ss", "-tlnp"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            for line in completed.stdout.splitlines():
                if re.search(rf":{port}\b", line):
                    holders.append(line.strip())
        except (OSError, subprocess.SubprocessError):
            pass

    holder_text = "\n".join(f"  {line}" for line in holders) if holders else "  (LISTEN プロセスを特定できませんでした)"
    return (
        f"OtomeKairo server cannot listen on {host}:{port}: address is already in use.\n"
        f"LISTEN holders:\n{holder_text}\n"
        "対処:\n"
        f"  1) ./scripts/free_server_port.sh {port}\n"
        f"  2) それでも残る場合: ./scripts/free_server_port.sh {port} --force\n"
        "  3) WSL2 でプロセスが無いのに失敗する場合: Windows 側で wsl --shutdown\n"
        f"  4) 別ポート: OTOMEKAIRO_PORT=<空きポート> を指定\n"
    )


# メイン
def main() -> None:
    # 環境
    host = os.environ.get("OTOMEKAIRO_HOST", "0.0.0.0")
    port = int(os.environ.get("OTOMEKAIRO_PORT", "55601"))
    cert_file = os.environ.get("OTOMEKAIRO_TLS_CERT_FILE")
    key_file = os.environ.get("OTOMEKAIRO_TLS_KEY_FILE")
    root_dir = Path(os.environ.get("OTOMEKAIRO_DATA_DIR", "var/otomekairo"))
    log_max_bytes = int(os.environ.get("OTOMEKAIRO_DEBUG_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
    log_backup_count = int(os.environ.get("OTOMEKAIRO_DEBUG_LOG_BACKUP_COUNT", "3"))

    # TLS検証
    if not cert_file or not key_file:
        raise SystemExit("OTOMEKAIRO_TLS_CERT_FILE and OTOMEKAIRO_TLS_KEY_FILE are required.")

    # ログファイル
    configure_debug_log_file(
        root_dir / "server.log",
        max_bytes=log_max_bytes,
        backup_count=log_backup_count,
    )

    # TLSコンテキスト
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)

    # サービス
    debug_log("Run", f"starting host={host} port={port} data_dir={root_dir}")
    debug_log("Run", f"tls cert={cert_file} key={key_file}")
    service = OtomeKairoService(root_dir=root_dir)
    try:
        server = OtomeKairoHttpServer((host, port), service, tls_context=context)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            message = _port_in_use_hint(host, port)
            debug_log("Run", message.replace("\n", " | "))
            print(message, file=sys.stderr)
            raise SystemExit(2) from None
        raise
    # スケジューラー開始
    service.start_background_memory_postprocess_worker()
    service.start_background_visual_daily_worker()
    service.start_background_autonomous_run_scheduler()
    service.start_background_thinking_scheduler()
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
        service.close_audio_runtime()
        service.close_tts_runtime()
        service.close_event_streams()
        service.stop_background_thinking_scheduler()
        service.stop_background_autonomous_run_scheduler()
        service.stop_background_visual_daily_worker()
        service.stop_background_memory_postprocess_worker()
        server.server_close()
        debug_log("Run", "shutdown complete")


if __name__ == "__main__":
    main()
