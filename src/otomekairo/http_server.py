from __future__ import annotations

import errno
import json
import ssl
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from otomekairo.event_stream import ServerWebSocket, WebSocketProtocolError, build_websocket_accept
from otomekairo.service.app import OtomeKairoService, ServiceError
from otomekairo.service.common import debug_log


CLIENT_DISCONNECT_ERRNOS = {
    errno.ECONNABORTED,
    errno.ECONNRESET,
    errno.EPIPE,
    errno.ESHUTDOWN,
    errno.ETIMEDOUT,
}

CLIENT_DISCONNECT_SSL_REASONS = {
    "BAD_LENGTH",
    "EOF_OCCURRED",
}

SUPPRESSED_HTTP_LOG_PATH_PREFIXES = ("/api/inspection",)


# クライアント切断
class ClientDisconnectedError(RuntimeError):
    pass


# サーバー
class OtomeKairoHttpServer(ThreadingHTTPServer):
    # ソケット再利用
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], service: OtomeKairoService) -> None:
        # 基底初期化
        super().__init__(server_address, OtomeKairoHandler)
        self.service = service


# ハンドラー
class OtomeKairoHandler(BaseHTTPRequestHandler):
    server: OtomeKairoHttpServer
    protocol_version = "HTTP/1.1"

    # メソッド群
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    # ログ出力
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    # ディスパッチ
    def _dispatch(self, method: str) -> None:
        try:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            token = self._bearer_token()
            if self._should_log_http_path(parsed.path):
                debug_log("HTTP", f"{method} {parsed.path} begin query_keys={sorted(query)} auth={bool(token)}", level="DEBUG")

            # 起動時ルート
            if method == "GET" and parsed.path == "/api/bootstrap/probe":
                self._write_success(HTTPStatus.OK, self.server.service.probe_bootstrap())
                return
            if method == "GET" and parsed.path == "/api/bootstrap/server-identity":
                self._write_success(HTTPStatus.OK, self.server.service.read_server_identity())
                return
            if method == "POST" and parsed.path == "/api/bootstrap/register-first-console":
                self._read_json_body()
                self._write_success(HTTPStatus.CREATED, self.server.service.register_first_console())
                return
            if method == "POST" and parsed.path == "/api/bootstrap/reissue-console-access-token":
                self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.reissue_console_access_token(token),
                )
                return

            # 参照ルート
            if method == "GET" and parsed.path == "/api/status":
                self._write_success(HTTPStatus.OK, self.server.service.get_status(token))
                return
            if method == "GET" and parsed.path == "/api/config":
                self._write_success(HTTPStatus.OK, self.server.service.get_config(token))
                return
            if method == "GET" and parsed.path == "/api/config/editor-state":
                self._write_success(HTTPStatus.OK, self.server.service.get_editor_state(token))
                return
            if method == "GET" and parsed.path == "/api/config/camera-sources":
                self._write_success(HTTPStatus.OK, self.server.service.list_camera_sources(token))
                return
            if method == "GET" and parsed.path == "/api/config/camera-sources/editor-state":
                self._write_success(HTTPStatus.OK, self.server.service.get_camera_sources_editor_state(token))
                return
            if method == "GET" and parsed.path.startswith("/api/config/connectors/") and parsed.path.endswith("/runtime-config"):
                path_parts = parsed.path.split("/")
                if len(path_parts) != 6 or path_parts[5] != "runtime-config":
                    raise ServiceError(404, "route_not_found", "The requested route does not exist.")
                client_id = unquote(path_parts[4])
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.get_connector_runtime_config(token, client_id),
                )
                return
            if method == "GET" and parsed.path == "/api/catalog":
                self._write_success(HTTPStatus.OK, self.server.service.get_catalog(token))
                return
            if method == "GET" and parsed.path == "/api/events/stream":
                self._handle_events_stream(token)
                return
            if method == "GET" and parsed.path == "/api/logs/stream":
                self._handle_logs_stream(token)
                return
            if method == "GET" and parsed.path == "/api/autonomous-runs":
                self._write_success(HTTPStatus.OK, self.server.service.list_autonomous_runs_api(token))
                return
            if method == "POST" and parsed.path.startswith("/api/autonomous-runs/"):
                path_parts = parsed.path.split("/")
                if len(path_parts) != 5:
                    raise ServiceError(404, "route_not_found", "The requested route does not exist.")
                run_id = unquote(path_parts[3])
                operation = path_parts[4]
                self._read_json_body()
                if operation == "pause":
                    self._write_success(
                        HTTPStatus.OK,
                        self.server.service.pause_autonomous_run_api(token, run_id),
                    )
                    return
                if operation == "resume":
                    self._write_success(
                        HTTPStatus.OK,
                        self.server.service.resume_autonomous_run_api(token, run_id),
                    )
                    return
                if operation == "cancel":
                    self._write_success(
                        HTTPStatus.OK,
                        self.server.service.cancel_autonomous_run_api(token, run_id),
                    )
                    return
                raise ServiceError(404, "route_not_found", "The requested route does not exist.")

            # 入力ルート
            if method == "POST" and parsed.path == "/api/conversation":
                payload = self._read_json_body()
                self._write_success(HTTPStatus.OK, self.server.service.handle_conversation(token, payload))
                return
            if method == "POST" and parsed.path == "/api/wake":
                payload = self._read_json_body()
                self._write_success(HTTPStatus.OK, self.server.service.trigger_wake(token, payload))
                return
            if method == "POST" and parsed.path == "/api/capability/result":
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.submit_capability_result(token, payload),
                )
                return
            if method == "PATCH" and parsed.path.startswith("/api/capabilities/") and parsed.path.endswith("/state"):
                path_parts = parsed.path.split("/")
                if len(path_parts) != 5 or path_parts[4] != "state":
                    raise ServiceError(404, "route_not_found", "The requested route does not exist.")
                capability_id = unquote(path_parts[3])
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.patch_capability_state(token, capability_id, payload),
                )
                return

            # 設定ルート
            if method == "POST" and parsed.path == "/api/config/select-persona":
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.select_persona(token, payload.get("persona_id")),
                )
                return
            if method == "POST" and parsed.path == "/api/config/select-memory-set":
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.select_memory_set(token, payload.get("memory_set_id")),
                )
                return
            if method == "POST" and parsed.path == "/api/config/update-wake-policy":
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.update_wake_policy(token, payload.get("wake_policy")),
                )
                return
            if method == "POST" and parsed.path == "/api/config/select-model-preset":
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.select_model_preset(token, payload.get("model_preset_id")),
                )
                return
            if method == "PATCH" and parsed.path == "/api/config/current":
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.patch_current(token, payload),
                )
                return
            if method == "PUT" and parsed.path == "/api/config/editor-state":
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.replace_editor_state(token, payload),
                )
                return
            if method == "PUT" and parsed.path == "/api/config/camera-sources/editor-state":
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.replace_camera_sources_editor_state(token, payload),
                )
                return
            if method == "GET" and parsed.path.startswith("/api/config/camera-sources/"):
                vision_source_id = unquote(parsed.path.rsplit("/", 1)[-1])
                self._write_success(HTTPStatus.OK, self.server.service.get_camera_source(token, vision_source_id))
                return
            if method == "PUT" and parsed.path.startswith("/api/config/camera-sources/"):
                vision_source_id = unquote(parsed.path.rsplit("/", 1)[-1])
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.replace_camera_source(token, vision_source_id, payload),
                )
                return
            if method == "DELETE" and parsed.path.startswith("/api/config/camera-sources/"):
                vision_source_id = unquote(parsed.path.rsplit("/", 1)[-1])
                self._write_success(HTTPStatus.OK, self.server.service.delete_camera_source(token, vision_source_id))
                return
            if method == "GET" and parsed.path.startswith("/api/config/personas/"):
                persona_id = parsed.path.rsplit("/", 1)[-1]
                self._write_success(HTTPStatus.OK, self.server.service.get_persona(token, persona_id))
                return
            if method == "PUT" and parsed.path.startswith("/api/config/personas/"):
                persona_id = parsed.path.rsplit("/", 1)[-1]
                payload = self._read_json_body()
                self._write_success(HTTPStatus.OK, self.server.service.replace_persona(token, persona_id, payload))
                return
            if method == "DELETE" and parsed.path.startswith("/api/config/personas/"):
                persona_id = parsed.path.rsplit("/", 1)[-1]
                self._write_success(HTTPStatus.OK, self.server.service.delete_persona(token, persona_id))
                return
            if method == "GET" and parsed.path.startswith("/api/config/memory-sets/"):
                memory_set_id = parsed.path.rsplit("/", 1)[-1]
                self._write_success(HTTPStatus.OK, self.server.service.get_memory_set(token, memory_set_id))
                return
            if method == "PUT" and parsed.path.startswith("/api/config/memory-sets/"):
                memory_set_id = parsed.path.rsplit("/", 1)[-1]
                payload = self._read_json_body()
                self._write_success(HTTPStatus.OK, self.server.service.replace_memory_set(token, memory_set_id, payload))
                return
            if method == "POST" and parsed.path == "/api/config/memory-sets/clone":
                payload = self._read_json_body()
                self._write_success(HTTPStatus.OK, self.server.service.clone_memory_set(token, payload))
                return
            if method == "DELETE" and parsed.path.startswith("/api/config/memory-sets/"):
                memory_set_id = parsed.path.rsplit("/", 1)[-1]
                self._write_success(HTTPStatus.OK, self.server.service.delete_memory_set(token, memory_set_id))
                return
            if method == "GET" and parsed.path.startswith("/api/config/model-presets/"):
                model_preset_id = parsed.path.rsplit("/", 1)[-1]
                self._write_success(HTTPStatus.OK, self.server.service.get_model_preset(token, model_preset_id))
                return
            if method == "PUT" and parsed.path.startswith("/api/config/model-presets/"):
                model_preset_id = parsed.path.rsplit("/", 1)[-1]
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.replace_model_preset(token, model_preset_id, payload),
                )
                return
            if method == "DELETE" and parsed.path.startswith("/api/config/model-presets/"):
                model_preset_id = parsed.path.rsplit("/", 1)[-1]
                self._write_success(HTTPStatus.OK, self.server.service.delete_model_preset(token, model_preset_id))
                return

            # 検査ルート
            if method == "GET" and parsed.path == "/api/inspection/current-state":
                self._write_success(HTTPStatus.OK, self.server.service.get_current_state_inspection(token))
                return
            if method == "GET" and parsed.path == "/api/inspection/capabilities":
                self._write_success(HTTPStatus.OK, self.server.service.get_capability_inspection(token))
                return
            if method == "GET" and parsed.path == "/api/inspection/visual-digests":
                limit = int(query.get("limit", ["20"])[0])
                local_date = query.get("local_date", [None])[0]
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.get_visual_digest_inspection(
                        token,
                        limit=max(limit, 1),
                        local_date=local_date,
                    ),
                )
                return
            if method == "GET" and parsed.path == "/api/inspection/cycle-summaries":
                limit = int(query.get("limit", ["20"])[0])
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.list_cycle_summaries(token, limit=max(limit, 1)),
                )
                return
            if method == "GET" and parsed.path.startswith("/api/inspection/cycles/"):
                cycle_id = parsed.path.rsplit("/", 1)[-1]
                self._write_success(HTTPStatus.OK, self.server.service.get_cycle_trace(token, cycle_id))
                return

            # 未検出
            raise ServiceError(404, "route_not_found", "The requested route does not exist.")
        except ClientDisconnectedError as exc:
            self._debug_log_client_disconnect(exc.__cause__ or exc)
        except json.JSONDecodeError:
            self._write_error_safely(
                HTTPStatus.BAD_REQUEST,
                "invalid_json",
                "The request body must be valid JSON.",
            )
        except ServiceError as exc:
            self._write_error_safely(exc.status_code, exc.error_code, exc.message)
        except Exception as exc:  # noqa: BLE001
            if self._is_client_disconnect(exc):
                self._debug_log_client_disconnect(exc)
                return
            self._write_error_safely(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_server_error", str(exc))

    def _handle_events_stream(self, token: str | None) -> None:
        # 認可
        self.server.service._require_token(token)

        # ヘッダー群
        upgrade = self.headers.get("Upgrade", "")
        connection = self.headers.get("Connection", "")
        websocket_key = self.headers.get("Sec-WebSocket-Key")
        websocket_version = self.headers.get("Sec-WebSocket-Version")
        if upgrade.lower() != "websocket" or "upgrade" not in connection.lower():
            raise ServiceError(400, "invalid_websocket_upgrade", "Upgrade: websocket is required.")
        if not isinstance(websocket_key, str) or not websocket_key.strip():
            raise ServiceError(400, "missing_websocket_key", "Sec-WebSocket-Key is required.")
        if websocket_version != "13":
            raise ServiceError(400, "invalid_websocket_version", "Sec-WebSocket-Version must be 13.")

        # ハンドシェイク
        accept_value = build_websocket_accept(websocket_key.strip())
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept_value)
        self.end_headers()
        self.wfile.flush()

        # 接続
        websocket = ServerWebSocket(self.connection)
        session_id = self.server.service.register_event_stream_connection(websocket)
        debug_log("HTTP", f"events/stream connected session={session_id}")
        try:
            # 受信ループ
            while True:
                payload = websocket.receive_json()
                if payload is None:
                    break
                self.server.service.handle_event_stream_message(session_id, payload)
        except (json.JSONDecodeError, ServiceError, ValueError, WebSocketProtocolError):
            websocket.close()
        finally:
            # 後始末
            self.server.service.unregister_event_stream_connection(session_id)
            debug_log("HTTP", f"events/stream disconnected session={session_id}")

    def _handle_logs_stream(self, token: str | None) -> None:
        # 認可
        self.server.service._require_token(token)

        # ヘッダー群
        upgrade = self.headers.get("Upgrade", "")
        connection = self.headers.get("Connection", "")
        websocket_key = self.headers.get("Sec-WebSocket-Key")
        websocket_version = self.headers.get("Sec-WebSocket-Version")
        if upgrade.lower() != "websocket" or "upgrade" not in connection.lower():
            raise ServiceError(400, "invalid_websocket_upgrade", "Upgrade: websocket is required.")
        if not isinstance(websocket_key, str) or not websocket_key.strip():
            raise ServiceError(400, "missing_websocket_key", "Sec-WebSocket-Key is required.")
        if websocket_version != "13":
            raise ServiceError(400, "invalid_websocket_version", "Sec-WebSocket-Version must be 13.")

        # ハンドシェイク
        accept_value = build_websocket_accept(websocket_key.strip())
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept_value)
        self.end_headers()
        self.wfile.flush()

        # 接続
        websocket = ServerWebSocket(self.connection)
        session_id = self.server.service.register_log_stream_connection(websocket)
        debug_log("HTTP", f"logs/stream connected session={session_id}")
        try:
            # 受信ループ
            while True:
                text = websocket.receive_text()
                if text is None:
                    break
        except WebSocketProtocolError:
            websocket.close()
        finally:
            # 後始末
            self.server.service.remove_log_stream_connection(session_id)
            debug_log("HTTP", f"logs/stream disconnected session={session_id}")

    # リクエスト補助
    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length > 0 else b"{}"
        payload = json.loads(raw_body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ServiceError(400, "invalid_json_shape", "The request body must be a JSON object.")
        return payload

    def _bearer_token(self) -> str | None:
        authorization = self.headers.get("Authorization")
        if not authorization:
            return None
        if not authorization.startswith("Bearer "):
            return None
        return authorization.removeprefix("Bearer ").strip()

    # レスポンス補助
    def _write_success(self, status: int, data: dict) -> None:
        payload = {
            "ok": True,
            "data": data,
        }
        self._write_json(status, payload)

    def _write_error(self, status: int, error_code: str, message: str) -> None:
        payload = {
            "ok": False,
            "error": {
                "code": error_code,
                "message": message,
            },
        }
        self._write_json(status, payload)

    def _write_error_safely(self, status: int, error_code: str, message: str) -> None:
        # エラー応答中に切断された場合は、同じソケットへ再送しない。
        try:
            self._write_error(status, error_code, message)
        except ClientDisconnectedError as exc:
            self._debug_log_client_disconnect(exc.__cause__ or exc)

    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:  # noqa: BLE001
            if self._is_client_disconnect(exc):
                self.close_connection = True
                raise ClientDisconnectedError(str(exc)) from exc
            raise
        self._debug_log_response(status, payload)

    def _debug_log_response(self, status: int, payload: dict) -> None:
        parsed = urlparse(self.path)
        if not self._should_log_http_path(parsed.path):
            return

        if status >= 400:
            error = payload.get("error", {})
            error_code = error.get("code") if isinstance(error, dict) else None
            level = "ERROR" if status >= 500 else "WARNING"
            debug_log("HTTP", f"{self.command} {parsed.path} -> {status} error={error_code or '-'}", level=level)
            return

        debug_log("HTTP", f"{self.command} {parsed.path} -> {status}")

    def _debug_log_client_disconnect(self, exc: BaseException) -> None:
        parsed = urlparse(self.path)
        if not self._should_log_http_path(parsed.path):
            return
        debug_log("HTTP", f"{self.command} {parsed.path} client_disconnected error={type(exc).__name__}", level="WARNING")

    def _should_log_http_path(self, path: str) -> bool:
        # inspection は情報量が多く、正本は endpoint 応答側なので HTTP access log へ重複記録しない。
        if path in {"/api/status", "/api/bootstrap/probe"}:
            return False
        return not any(path.startswith(prefix) for prefix in SUPPRESSED_HTTP_LOG_PATH_PREFIXES)

    def _is_client_disconnect(self, exc: BaseException) -> bool:
        # レスポンス送信中の切断だけを通常の終了として扱う。
        if isinstance(
            exc,
            (
                BrokenPipeError,
                ConnectionAbortedError,
                ConnectionResetError,
                TimeoutError,
                ssl.SSLEOFError,
                ssl.SSLZeroReturnError,
            ),
        ):
            return True
        if isinstance(exc, OSError) and exc.errno in CLIENT_DISCONNECT_ERRNOS:
            return True
        if isinstance(exc, ssl.SSLError):
            reason = getattr(exc, "reason", None)
            if isinstance(reason, str) and reason in CLIENT_DISCONNECT_SSL_REASONS:
                return True
            message = str(exc)
            return any(marker in message for marker in CLIENT_DISCONNECT_SSL_REASONS)
        return False
