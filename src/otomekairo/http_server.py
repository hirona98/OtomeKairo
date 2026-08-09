from __future__ import annotations

import errno
import json
import ssl
from importlib import resources
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

SUPPRESSED_HTTP_LOG_EXACT_PATHS = {
    "/api/status",
    "/api/bootstrap/probe",
    "/api/autonomous-runs",
    "/api/capability/result",
    "/api/audio/stream",
    "/api/audio/console-stream",
    "/api/audio/input-state",
    "/ui/api/audio/stream",
}
SUPPRESSED_HTTP_LOG_PATH_PREFIXES = (
    "/api/inspection",
    "/ui/api/inspection",
)
WEB_STATIC_PACKAGE = "otomekairo.web.static"
WEB_STATIC_FILES = {
    "/ui/": ("index.html", "text/html; charset=utf-8", "no-store"),
    "/ui/index.html": ("index.html", "text/html; charset=utf-8", "no-store"),
    "/ui/app.js": ("app.js", "text/javascript; charset=utf-8", "max-age=60"),
    "/ui/audio-worklet.js": (
        "audio-worklet.js",
        "text/javascript; charset=utf-8",
        "max-age=60",
    ),
    "/ui/logs": ("logs.html", "text/html; charset=utf-8", "no-store"),
    "/ui/logs.html": ("logs.html", "text/html; charset=utf-8", "no-store"),
    "/ui/logs.js": ("logs.js", "text/javascript; charset=utf-8", "max-age=60"),
    "/ui/cycles": ("cycles.html", "text/html; charset=utf-8", "no-store"),
    "/ui/cycles.html": ("cycles.html", "text/html; charset=utf-8", "no-store"),
    "/ui/cycles.js": ("cycles.js", "text/javascript; charset=utf-8", "max-age=60"),
    "/ui/styles.css": ("styles.css", "text/css; charset=utf-8", "max-age=60"),
}


# クライアント切断
class ClientDisconnectedError(RuntimeError):
    pass


# サーバー
class OtomeKairoHttpServer(ThreadingHTTPServer):
    # ソケット再利用
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        service: OtomeKairoService,
        *,
        tls_context: ssl.SSLContext | None = None,
    ) -> None:
        # 基底初期化
        super().__init__(server_address, OtomeKairoHandler)
        self.service = service
        self.tls_context = tls_context

    def process_request_thread(self, request, client_address) -> None:
        # TLS handshake は accept loop ではなく接続ごとの thread で行う。
        # ポート検出など TLS を開始しない TCP 接続が、他の接続受付を止めるのを防ぐ。
        if self.tls_context is None:
            super().process_request_thread(request, client_address)
            return

        try:
            request.settimeout(10)
            tls_request = self.tls_context.wrap_socket(request, server_side=True)
            tls_request.settimeout(None)
        except (OSError, ssl.SSLError):
            self.shutdown_request(request)
            return

        super().process_request_thread(tls_request, client_address)


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

            # ブラウザUI
            if method == "GET" and parsed.path == "/":
                self._redirect("/ui/")
                return
            if method == "GET" and parsed.path == "/ui":
                self._redirect("/ui/")
                return
            if parsed.path.startswith("/ui/api/"):
                self._handle_web_ui_api(method, parsed.path)
                return
            if method == "GET" and parsed.path.startswith("/ui"):
                self._handle_web_static(parsed.path)
                return

            # 起動時ルート
            if method == "GET" and parsed.path == "/api/bootstrap/probe":
                self._write_success(HTTPStatus.OK, self.server.service.probe_bootstrap())
                return
            if method == "GET" and parsed.path == "/api/bootstrap/server-identity":
                self._write_success(HTTPStatus.OK, self.server.service.read_server_identity())
                return
            if method == "POST" and parsed.path == "/api/bootstrap/acquire-console-access-token":
                payload = self._read_json_body()
                if payload:
                    raise ServiceError(
                        400,
                        "unsupported_console_connect_fields",
                        "CocoroConsole connect body must be an empty object.",
                    )
                self._write_success(HTTPStatus.OK, self.server.service.acquire_console_access_token())
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
            if method == "GET" and parsed.path == "/api/config/conversation-display-names":
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.list_conversation_display_names(token),
                )
                return
            if method == "GET" and parsed.path == "/api/config/editor-state":
                self._write_success(HTTPStatus.OK, self.server.service.get_editor_state(token))
                return
            if method == "GET" and parsed.path == "/api/config/avatar-speech":
                self._write_success(HTTPStatus.OK, self.server.service.get_avatar_speech(token))
                return
            if method == "GET" and parsed.path == "/api/config/avatar-speech/editor-state":
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.get_avatar_speech_editor_state(token),
                )
                return
            if method == "GET" and parsed.path == "/api/config/camera-sources":
                self._write_success(HTTPStatus.OK, self.server.service.list_camera_sources(token))
                return
            if method == "GET" and parsed.path == "/api/config/camera-sources/editor-state":
                self._write_success(HTTPStatus.OK, self.server.service.get_camera_sources_editor_state(token))
                return
            if method == "GET" and parsed.path == "/api/config/mcp-servers":
                self._write_success(HTTPStatus.OK, self.server.service.list_mcp_servers(token))
                return
            if method == "GET" and parsed.path == "/api/config/mcp-servers/editor-state":
                self._write_success(HTTPStatus.OK, self.server.service.get_mcp_servers_editor_state(token))
                return
            if (
                method == "GET"
                and parsed.path
                == "/api/config/console-clients/last-connected/editor-state"
            ):
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.get_last_connected_console_client_editor_state(token),
                )
                return
            if (
                method == "GET"
                and parsed.path.startswith("/api/config/console-clients/")
                and parsed.path.endswith("/editor-state")
            ):
                path_parts = parsed.path.split("/")
                if len(path_parts) != 6:
                    raise ServiceError(404, "route_not_found", "The requested route does not exist.")
                client_id = unquote(path_parts[4])
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.get_console_client_editor_state(token, client_id),
                )
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
            if method == "GET" and parsed.path.startswith("/api/config/watchers/") and parsed.path.endswith("/runtime-config"):
                path_parts = parsed.path.split("/")
                if len(path_parts) != 6 or path_parts[5] != "runtime-config":
                    raise ServiceError(404, "route_not_found", "The requested route does not exist.")
                watcher_id = unquote(path_parts[4])
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.get_watcher_runtime_config(token, watcher_id),
                )
                return
            if method == "GET" and parsed.path == "/api/catalog":
                self._write_success(HTTPStatus.OK, self.server.service.get_catalog(token))
                return
            if method == "GET" and parsed.path == "/api/docs":
                self._write_success(HTTPStatus.OK, self.server.service.get_docs(token))
                return
            if method == "GET" and parsed.path == "/api/audio/stream":
                self._handle_audio_stream(
                    token,
                    endpoint_source="local_microphone",
                )
                return
            if method == "GET" and parsed.path == "/api/audio/console-stream":
                self._handle_audio_stream(
                    token,
                    endpoint_source="console_microphone",
                )
                return
            if method == "GET" and parsed.path == "/api/audio/input-state":
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.get_audio_input_state(token),
                )
                return
            if method == "GET" and parsed.path == "/api/audio/stt-enabled":
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.get_audio_stt_enabled(token),
                )
                return
            if method == "PUT" and parsed.path == "/api/audio/stt-enabled":
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.replace_audio_stt_enabled(
                        token,
                        self._read_json_body(),
                    ),
                )
                return
            if method == "GET" and parsed.path == "/api/audio/input-devices":
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.list_audio_input_devices(token),
                )
                return
            if method == "GET" and parsed.path == "/api/audio/speakers":
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.list_audio_speakers(token),
                )
                return
            if method == "POST" and parsed.path == "/api/audio/speaker-enrollments":
                self._write_success(
                    HTTPStatus.CREATED,
                    self.server.service.start_audio_speaker_enrollment(
                        token,
                        self._read_json_body(),
                    ),
                )
                return
            if method == "POST" and parsed.path == "/api/config/conversation-display-names":
                self._write_success(
                    HTTPStatus.CREATED,
                    self.server.service.create_conversation_display_name(
                        token,
                        self._read_json_body(),
                    ),
                )
                return
            if (
                method == "DELETE"
                and parsed.path.startswith("/api/audio/speaker-enrollments/")
            ):
                enrollment_id = unquote(parsed.path.rsplit("/", 1)[-1])
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.cancel_audio_speaker_enrollment(
                        token,
                        enrollment_id,
                    ),
                )
                return
            if (
                method == "PUT"
                and parsed.path.startswith("/api/audio/speakers/")
                and parsed.path.endswith("/conversation-display-name")
            ):
                path_parts = parsed.path.split("/")
                if len(path_parts) != 6:
                    raise ServiceError(
                        404,
                        "route_not_found",
                        "The requested route does not exist.",
                    )
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.assign_audio_speaker_conversation_display_name(
                        token,
                        unquote(path_parts[4]),
                        self._read_json_body(),
                    ),
                )
                return
            if (
                method == "PUT"
                and parsed.path.startswith("/api/config/conversation-display-names/")
            ):
                display_name_id = unquote(parsed.path.rsplit("/", 1)[-1])
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.update_conversation_display_name(
                        token,
                        display_name_id,
                        self._read_json_body(),
                    ),
                )
                return
            if (
                method == "DELETE"
                and parsed.path.startswith("/api/config/conversation-display-names/")
            ):
                display_name_id = unquote(parsed.path.rsplit("/", 1)[-1])
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.delete_conversation_display_name(
                        token,
                        display_name_id,
                    ),
                )
                return
            if (
                method == "DELETE"
                and parsed.path.startswith("/api/audio/speakers/")
                and parsed.path.endswith("/registration")
            ):
                path_parts = parsed.path.split("/")
                if len(path_parts) != 6:
                    raise ServiceError(
                        404,
                        "route_not_found",
                        "The requested route does not exist.",
                    )
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.unregister_audio_speaker(
                        token,
                        unquote(path_parts[4]),
                    ),
                )
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
                self._handle_autonomous_run_operation(token, run_id, operation)
                return

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
            if (
                method == "POST"
                and parsed.path.startswith("/api/config/console-clients/")
                and parsed.path.endswith("/connect")
            ):
                path_parts = parsed.path.split("/")
                if len(path_parts) != 6:
                    raise ServiceError(404, "route_not_found", "The requested route does not exist.")
                self._read_json_body()
                client_id = unquote(path_parts[4])
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.connect_console_client(token, client_id),
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
            if method == "PUT" and parsed.path == "/api/config/avatar-speech/editor-state":
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.replace_avatar_speech_editor_state(token, payload),
                )
                return
            if method == "PUT" and parsed.path == "/api/config/camera-sources/editor-state":
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.replace_camera_sources_editor_state(token, payload),
                )
                return
            if method == "PUT" and parsed.path == "/api/config/mcp-servers/editor-state":
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.replace_mcp_servers_editor_state(token, payload),
                )
                return
            if (
                method == "PUT"
                and parsed.path.startswith("/api/config/console-clients/")
                and parsed.path.endswith("/editor-state")
            ):
                path_parts = parsed.path.split("/")
                if len(path_parts) != 6:
                    raise ServiceError(404, "route_not_found", "The requested route does not exist.")
                client_id = unquote(path_parts[4])
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.replace_console_client_editor_state(
                        token,
                        client_id,
                        payload,
                    ),
                )
                return
            if method == "PATCH" and parsed.path.startswith("/api/config/console-clients/"):
                path_parts = parsed.path.split("/")
                if len(path_parts) != 5:
                    raise ServiceError(404, "route_not_found", "The requested route does not exist.")
                client_id = unquote(path_parts[4])
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.patch_console_client_settings(
                        token,
                        client_id,
                        payload,
                    ),
                )
                return
            if method == "GET" and parsed.path.startswith("/api/config/camera-sources/"):
                vision_source_id = unquote(parsed.path.rsplit("/", 1)[-1])
                self._write_success(HTTPStatus.OK, self.server.service.get_camera_source(token, vision_source_id))
                return
            if method == "DELETE" and parsed.path.startswith("/api/config/camera-sources/"):
                vision_source_id = unquote(parsed.path.rsplit("/", 1)[-1])
                self._write_success(HTTPStatus.OK, self.server.service.delete_camera_source(token, vision_source_id))
                return
            if method == "GET" and parsed.path.startswith("/api/config/mcp-servers/"):
                mcp_server_id = unquote(parsed.path.rsplit("/", 1)[-1])
                self._write_success(HTTPStatus.OK, self.server.service.get_mcp_server(token, mcp_server_id))
                return
            if method == "PUT" and parsed.path.startswith("/api/config/mcp-servers/"):
                mcp_server_id = unquote(parsed.path.rsplit("/", 1)[-1])
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.replace_mcp_server(token, mcp_server_id, payload),
                )
                return
            if method == "DELETE" and parsed.path.startswith("/api/config/mcp-servers/"):
                mcp_server_id = unquote(parsed.path.rsplit("/", 1)[-1])
                self._write_success(HTTPStatus.OK, self.server.service.delete_mcp_server(token, mcp_server_id))
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
            if method == "GET" and parsed.path == "/api/inspection/memory-snapshot":
                unit_limit = int(query.get("unit_limit", ["12"])[0])
                episode_limit = int(query.get("episode_limit", ["8"])[0])
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.get_memory_snapshot_inspection(
                        token,
                        unit_limit=max(unit_limit, 1),
                        episode_limit=max(episode_limit, 1),
                    ),
                )
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
                limit = self._clamp_inspection_limit(query.get("limit", ["20"])[0], default=20)
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.list_cycle_summaries(token, limit=limit),
                )
                return
            if method == "GET" and parsed.path.startswith("/api/inspection/cycles/") and parsed.path.endswith("/cognitive-context"):
                cycle_id = parsed.path.removesuffix("/cognitive-context").rsplit("/", 1)[-1]
                self._write_success(HTTPStatus.OK, self.server.service.get_cycle_cognitive_context(token, cycle_id))
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
        debug_log("HTTP", f"events/stream connected session={session_id}", level="DEBUG")
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
            debug_log("HTTP", f"events/stream disconnected session={session_id}", level="DEBUG")

    def _handle_audio_stream(
        self,
        token: str | None,
        *,
        endpoint_source: str,
    ) -> None:
        # connectorとWeb UIは認証方法だけを分け、同じ音声protocolを使用する。
        self.server.service._require_token(token)
        upgrade = self.headers.get("Upgrade", "")
        connection = self.headers.get("Connection", "")
        websocket_key = self.headers.get("Sec-WebSocket-Key")
        websocket_version = self.headers.get("Sec-WebSocket-Version")
        if upgrade.lower() != "websocket" or "upgrade" not in connection.lower():
            raise ServiceError(
                400,
                "invalid_websocket_upgrade",
                "Upgrade: websocket is required.",
            )
        if not isinstance(websocket_key, str) or not websocket_key.strip():
            raise ServiceError(
                400,
                "missing_websocket_key",
                "Sec-WebSocket-Key is required.",
            )
        if websocket_version != "13":
            raise ServiceError(
                400,
                "invalid_websocket_version",
                "Sec-WebSocket-Version must be 13.",
            )

        accept_value = build_websocket_accept(websocket_key.strip())
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept_value)
        self.end_headers()
        self.wfile.flush()

        websocket = ServerWebSocket(self.connection)
        session_id = self.server.service.register_audio_stream_connection(
            websocket,
            endpoint_source=endpoint_source,
        )
        debug_log(
            "HTTP",
            f"audio/stream connected session={session_id} source={endpoint_source}",
            level="DEBUG",
        )
        try:
            while True:
                message = websocket.receive_message()
                if message is None:
                    break
                message_kind, message_payload = message
                self.server.service.handle_audio_stream_message(
                    session_id,
                    message_kind,
                    message_payload,
                )
        except ServiceError as exc:
            self.server.service.send_audio_stream_error(
                session_id,
                code=exc.error_code,
                message=exc.message,
            )
            websocket.close()
        except (ValueError, WebSocketProtocolError):
            self.server.service.send_audio_stream_error(
                session_id,
                code="invalid_audio_control",
                message="The audio stream protocol is invalid.",
            )
            websocket.close()
        finally:
            self.server.service.unregister_audio_stream_connection(session_id)
            debug_log(
                "HTTP",
                f"audio/stream disconnected session={session_id}",
                level="DEBUG",
            )

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
        debug_log("HTTP", f"logs/stream connected session={session_id}", level="DEBUG")
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
            debug_log("HTTP", f"logs/stream disconnected session={session_id}", level="DEBUG")

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

    # ブラウザUI補助
    def _redirect(self, location: str) -> None:
        try:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except Exception as exc:  # noqa: BLE001
            if self._is_client_disconnect(exc):
                self.close_connection = True
                raise ClientDisconnectedError(str(exc)) from exc
            raise
        debug_log("HTTP", f"{self.command} {urlparse(self.path).path} -> {HTTPStatus.FOUND}", level="DEBUG")

    def _handle_web_static(self, path: str) -> None:
        static_file = WEB_STATIC_FILES.get(path)
        if static_file is None:
            self._write_static_not_found()
            return

        file_name, content_type, cache_control = static_file
        try:
            body = resources.files(WEB_STATIC_PACKAGE).joinpath(file_name).read_bytes()
        except FileNotFoundError:
            self._write_static_not_found()
            return

        self._write_static_response(
            status=HTTPStatus.OK,
            body=body,
            content_type=content_type,
            cache_control=cache_control,
        )

    def _write_static_not_found(self) -> None:
        self._write_static_response(
            status=HTTPStatus.NOT_FOUND,
            body=b"Not Found",
            content_type="text/plain; charset=utf-8",
            cache_control="no-store",
        )

    def _write_static_response(
        self,
        *,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        cache_control: str,
    ) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", cache_control)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:  # noqa: BLE001
            if self._is_client_disconnect(exc):
                self.close_connection = True
                raise ClientDisconnectedError(str(exc)) from exc
            raise
        level = "ERROR" if status >= 500 else "WARNING" if status >= 400 else "DEBUG"
        debug_log("HTTP", f"{self.command} {urlparse(self.path).path} -> {status}", level=level)

    def _handle_web_ui_api(self, method: str, path: str) -> None:
        token = self._web_ui_console_token()

        # ブラウザへ token を渡さず、同一 server 内で event / log stream を認可する。
        if method == "GET" and path == "/ui/api/events/stream":
            self._require_web_ui_websocket_origin()
            self._handle_events_stream(token)
            return
        if method == "GET" and path == "/ui/api/logs/stream":
            self._require_web_ui_websocket_origin(subject="log stream")
            self._handle_logs_stream(token)
            return
        if method == "GET" and path == "/ui/api/audio/stream":
            self._require_web_ui_websocket_origin(
                error_code="invalid_audio_origin",
                subject="audio stream",
            )
            self._handle_audio_stream(
                token,
                endpoint_source="web_microphone",
            )
            return
        if method == "GET" and path == "/ui/api/audio/input-devices":
            self._write_success(
                HTTPStatus.OK,
                self.server.service.list_audio_input_devices(token),
            )
            return
        if method == "GET" and path == "/ui/api/audio/stt-enabled":
            self._write_success(
                HTTPStatus.OK,
                self.server.service.get_audio_stt_enabled(token),
            )
            return
        if method == "PUT" and path == "/ui/api/audio/stt-enabled":
            self._write_success(
                HTTPStatus.OK,
                self.server.service.replace_audio_stt_enabled(
                    token,
                    self._read_json_body(),
                ),
            )
            return
        if method == "POST" and path == "/ui/api/audio/input-sessions":
            self._write_success(
                HTTPStatus.CREATED,
                self.server.service.start_web_audio_input_session(
                    token,
                    self._read_json_body(),
                ),
            )
            return
        if (
            method == "DELETE"
            and path.startswith("/ui/api/audio/input-sessions/")
        ):
            self._write_success(
                HTTPStatus.OK,
                self.server.service.stop_web_audio_input_session(
                    token,
                    unquote(path.rsplit("/", 1)[-1]),
                ),
            )
            return
        if method == "GET" and path == "/ui/api/audio/speakers":
            self._write_success(
                HTTPStatus.OK,
                self.server.service.list_audio_speakers(token),
            )
            return
        if method == "GET" and path == "/ui/api/config/conversation-display-names":
            self._write_success(
                HTTPStatus.OK,
                self.server.service.list_conversation_display_names(token),
            )
            return
        if method == "POST" and path == "/ui/api/config/conversation-display-names":
            self._write_success(
                HTTPStatus.CREATED,
                self.server.service.create_conversation_display_name(
                    token,
                    self._read_json_body(),
                ),
            )
            return
        if method == "POST" and path == "/ui/api/audio/speaker-enrollments":
            self._write_success(
                HTTPStatus.CREATED,
                self.server.service.start_audio_speaker_enrollment(
                    token,
                    self._read_json_body(),
                ),
            )
            return
        if (
            method == "DELETE"
            and path.startswith("/ui/api/audio/speaker-enrollments/")
        ):
            self._write_success(
                HTTPStatus.OK,
                self.server.service.cancel_audio_speaker_enrollment(
                    token,
                    unquote(path.rsplit("/", 1)[-1]),
                ),
            )
            return
        if (
            method == "PUT"
            and path.startswith("/ui/api/audio/speakers/")
            and path.endswith("/conversation-display-name")
        ):
            path_parts = path.split("/")
            if len(path_parts) != 7:
                raise ServiceError(
                    404,
                    "route_not_found",
                    "The requested route does not exist.",
                )
            self._write_success(
                HTTPStatus.OK,
                self.server.service.assign_audio_speaker_conversation_display_name(
                    token,
                    unquote(path_parts[5]),
                    self._read_json_body(),
                ),
            )
            return
        if (
            method == "PUT"
            and path.startswith("/ui/api/config/conversation-display-names/")
        ):
            display_name_id = unquote(path.rsplit("/", 1)[-1])
            self._write_success(
                HTTPStatus.OK,
                self.server.service.update_conversation_display_name(
                    token,
                    display_name_id,
                    self._read_json_body(),
                ),
            )
            return
        if (
            method == "DELETE"
            and path.startswith("/ui/api/config/conversation-display-names/")
        ):
            display_name_id = unquote(path.rsplit("/", 1)[-1])
            self._write_success(
                HTTPStatus.OK,
                self.server.service.delete_conversation_display_name(
                    token,
                    display_name_id,
                ),
            )
            return
        if (
            method == "DELETE"
            and path.startswith("/ui/api/audio/speakers/")
            and path.endswith("/registration")
        ):
            path_parts = path.split("/")
            if len(path_parts) != 7:
                raise ServiceError(
                    404,
                    "route_not_found",
                    "The requested route does not exist.",
                )
            self._write_success(
                HTTPStatus.OK,
                self.server.service.unregister_audio_speaker(
                    token,
                    unquote(path_parts[5]),
                ),
            )
            return
        if method == "GET" and path == "/ui/api/bootstrap/server-identity":
            self._write_success(HTTPStatus.OK, self.server.service.read_server_identity())
            return
        if method == "GET" and path == "/ui/api/status":
            self._write_success(HTTPStatus.OK, self.server.service.get_status(token))
            return
        if method == "GET" and path == "/ui/api/docs":
            self._write_success(HTTPStatus.OK, self.server.service.get_docs(token))
            return
        if method == "GET" and path == "/ui/api/config":
            self._write_success(HTTPStatus.OK, self.server.service.get_config(token))
            return
        if method == "PATCH" and path == "/ui/api/config/current":
            payload = self._read_json_body()
            self._write_success(
                HTTPStatus.OK,
                self.server.service.patch_current(token, payload),
            )
            return
        if method == "GET" and path == "/ui/api/inspection/current-state":
            self._write_success(HTTPStatus.OK, self.server.service.get_current_state_inspection(token))
            return
        if method == "GET" and path == "/ui/api/inspection/memory-snapshot":
            self._write_success(HTTPStatus.OK, self.server.service.get_memory_snapshot_inspection(token))
            return
        if method == "GET" and path == "/ui/api/inspection/cycle-summaries":
            query = parse_qs(urlparse(self.path).query)
            limit = self._clamp_inspection_limit(query.get("limit", ["20"])[0], default=20)
            self._write_success(
                HTTPStatus.OK,
                self.server.service.list_cycle_summaries(token, limit=limit),
            )
            return
        if method == "GET" and path.startswith("/ui/api/inspection/cycles/") and path.endswith("/cognitive-context"):
            cycle_id = unquote(path.removesuffix("/cognitive-context").rsplit("/", 1)[-1])
            self._write_success(HTTPStatus.OK, self.server.service.get_cycle_cognitive_context(token, cycle_id))
            return
        if method == "GET" and path.startswith("/ui/api/inspection/cycles/"):
            cycle_id = unquote(path.rsplit("/", 1)[-1])
            self._write_success(HTTPStatus.OK, self.server.service.get_cycle_trace(token, cycle_id))
            return
        if method == "POST" and path.startswith("/ui/api/autonomous-runs/"):
            path_parts = path.split("/")
            if len(path_parts) != 6:
                raise ServiceError(404, "route_not_found", "The requested route does not exist.")
            run_id = unquote(path_parts[4])
            operation = path_parts[5]
            self._handle_autonomous_run_operation(token, run_id, operation)
            return
        if method == "POST" and path == "/ui/api/conversation":
            payload = self._read_json_body()
            self._write_success(HTTPStatus.OK, self.server.service.handle_conversation(token, payload))
            return
        if method == "GET" and path == "/ui/api/config/editor-state":
            self._write_success(HTTPStatus.OK, self.server.service.get_editor_state(token))
            return
        if method == "PUT" and path == "/ui/api/config/editor-state":
            payload = self._read_json_body()
            self._write_success(HTTPStatus.OK, self.server.service.replace_editor_state(token, payload))
            return
        if method == "POST" and path == "/ui/api/config/memory-sets/clone":
            payload = self._read_json_body()
            self._write_success(
                HTTPStatus.OK,
                self.server.service.clone_memory_set(token, payload),
            )
            return
        if method == "GET" and path == "/ui/api/config/avatar-speech/editor-state":
            self._write_success(
                HTTPStatus.OK,
                self.server.service.get_avatar_speech_editor_state(token),
            )
            return
        if method == "PUT" and path == "/ui/api/config/avatar-speech/editor-state":
            payload = self._read_json_body()
            self._write_success(
                HTTPStatus.OK,
                self.server.service.replace_avatar_speech_editor_state(token, payload),
            )
            return
        if method == "GET" and path == "/ui/api/config/camera-sources/editor-state":
            self._write_success(HTTPStatus.OK, self.server.service.get_camera_sources_editor_state(token))
            return
        if method == "PUT" and path == "/ui/api/config/camera-sources/editor-state":
            payload = self._read_json_body()
            self._write_success(HTTPStatus.OK, self.server.service.replace_camera_sources_editor_state(token, payload))
            return
        if method == "GET" and path == "/ui/api/config/mcp-servers/editor-state":
            self._write_success(HTTPStatus.OK, self.server.service.get_mcp_servers_editor_state(token))
            return
        if method == "PUT" and path == "/ui/api/config/mcp-servers/editor-state":
            payload = self._read_json_body()
            self._write_success(HTTPStatus.OK, self.server.service.replace_mcp_servers_editor_state(token, payload))
            return
        if (
            method == "GET"
            and path == "/ui/api/config/console-clients/last-connected/editor-state"
        ):
            self._write_success(
                HTTPStatus.OK,
                self.server.service.get_last_connected_console_client_editor_state(token),
            )
            return
        if (
            method == "PUT"
            and path.startswith("/ui/api/config/console-clients/")
            and path.endswith("/editor-state")
        ):
            path_parts = path.split("/")
            if len(path_parts) != 7:
                raise ServiceError(404, "route_not_found", "The requested route does not exist.")
            client_id = unquote(path_parts[5])
            payload = self._read_json_body()
            self._write_success(
                HTTPStatus.OK,
                self.server.service.replace_console_client_editor_state(
                    token,
                    client_id,
                    payload,
                ),
            )
            return
        if method == "PATCH" and path.startswith("/ui/api/config/console-clients/"):
            path_parts = path.split("/")
            if len(path_parts) != 6:
                raise ServiceError(404, "route_not_found", "The requested route does not exist.")
            client_id = unquote(path_parts[5])
            payload = self._read_json_body()
            self._write_success(
                HTTPStatus.OK,
                self.server.service.patch_console_client_settings(token, client_id, payload),
            )
            return

        raise ServiceError(404, "route_not_found", "The requested route does not exist.")

    def _require_web_ui_websocket_origin(
        self,
        *,
        error_code: str = "invalid_web_ui_origin",
        subject: str = "event stream",
    ) -> None:
        # UI 用 stream の server-held token を別 origin から利用させない。
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        parsed_origin = urlparse(origin) if isinstance(origin, str) else None
        if (
            parsed_origin is None
            or parsed_origin.scheme not in {"http", "https"}
            or not isinstance(host, str)
            or parsed_origin.netloc != host
        ):
            raise ServiceError(
                403,
                error_code,
                f"The Web UI {subject} requires a same-origin request.",
            )

    def _handle_autonomous_run_operation(self, token: str | None, run_id: str, operation: str) -> None:
        # 通常 API とブラウザ UI で同じ autonomous run 操作境界を使う。
        self._read_json_body()
        if operation == "pause":
            result = self.server.service.pause_autonomous_run_api(token, run_id)
        elif operation == "resume":
            result = self.server.service.resume_autonomous_run_api(token, run_id)
        elif operation == "cancel":
            result = self.server.service.cancel_autonomous_run_api(token, run_id)
        else:
            raise ServiceError(404, "route_not_found", "The requested route does not exist.")
        self._write_success(HTTPStatus.OK, result)

    def _clamp_inspection_limit(self, raw_value: str | None, *, default: int) -> int:
        # inspection 一覧の limit を閉じた範囲に固定する。
        try:
            value = int(raw_value) if raw_value is not None else default
        except (TypeError, ValueError):
            value = default
        return min(max(value, 1), 100)

    def _web_ui_console_token(self) -> str:
        # 初回画面が複数の UI API を並行取得しても token 発行を一度に固定する。
        with self.server.service._runtime_state_lock:
            state = self.server.service.store.read_state()
            token = state.get("console_access_token")
            if isinstance(token, str) and token:
                return token

            state["console_access_token"] = self.server.service._new_console_token()
            self.server.service.store.write_state(state)
            debug_log("Auth", "web_ui console token initialized")
            return state["console_access_token"]

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

        debug_log("HTTP", f"{self.command} {parsed.path} -> {status}", level="DEBUG")

    def _debug_log_client_disconnect(self, exc: BaseException) -> None:
        parsed = urlparse(self.path)
        if not self._should_log_http_path(parsed.path):
            return
        debug_log("HTTP", f"{self.command} {parsed.path} client_disconnected error={type(exc).__name__}", level="WARNING")

    def _should_log_http_path(self, path: str) -> bool:
        # 高頻度参照と観測返却は運用ログへ重複記録しない。
        if path in SUPPRESSED_HTTP_LOG_EXACT_PATHS:
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
