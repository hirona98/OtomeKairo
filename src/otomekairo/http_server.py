from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from otomekairo.event_stream import ServerWebSocket, WebSocketProtocolError, build_websocket_accept
from otomekairo.service import OtomeKairoService, ServiceError


# Block: Server
class OtomeKairoHttpServer(ThreadingHTTPServer):
    # Block: SocketReuse
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], service: OtomeKairoService) -> None:
        # Block: BaseInit
        super().__init__(server_address, OtomeKairoHandler)
        self.service = service


# Block: Handler
class OtomeKairoHandler(BaseHTTPRequestHandler):
    server: OtomeKairoHttpServer
    protocol_version = "HTTP/1.1"

    # Block: Methods
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

    # Block: Logging
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    # Block: Dispatcher
    def _dispatch(self, method: str) -> None:
        try:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            token = self._bearer_token()

            # Block: BootstrapRoutes
            if method == "GET" and parsed.path == "/api/bootstrap/probe":
                self._write_success(HTTPStatus.OK, self.server.service.probe_bootstrap())
                return
            if method == "GET" and parsed.path == "/api/bootstrap/server-identity":
                self._write_success(HTTPStatus.OK, self.server.service.read_server_identity())
                return
            if method == "POST" and parsed.path == "/api/bootstrap/register-first-console":
                self._write_success(HTTPStatus.CREATED, self.server.service.register_first_console())
                return
            if method == "POST" and parsed.path == "/api/bootstrap/reissue-console-access-token":
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.reissue_console_access_token(token),
                )
                return

            # Block: ReadRoutes
            if method == "GET" and parsed.path == "/api/status":
                self._write_success(HTTPStatus.OK, self.server.service.get_status(token))
                return
            if method == "GET" and parsed.path == "/api/config":
                self._write_success(HTTPStatus.OK, self.server.service.get_config(token))
                return
            if method == "GET" and parsed.path == "/api/config/editor-state":
                self._write_success(HTTPStatus.OK, self.server.service.get_editor_state(token))
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

            # Block: ObservationRoute
            if method == "POST" and parsed.path == "/api/observations/conversation":
                payload = self._read_json_body()
                self._write_success(HTTPStatus.OK, self.server.service.observe_conversation(token, payload))
                return
            if method == "POST" and parsed.path == "/api/observations/wake":
                payload = self._read_json_body()
                self._write_success(HTTPStatus.OK, self.server.service.observe_wake(token, payload))
                return
            if method == "POST" and parsed.path == "/api/v2/vision/capture-response":
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.submit_vision_capture_response(token, payload),
                )
                return

            # Block: ConfigRoutes
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
            if method == "GET" and parsed.path.startswith("/api/config/model-profiles/"):
                model_profile_id = parsed.path.rsplit("/", 1)[-1]
                self._write_success(HTTPStatus.OK, self.server.service.get_model_profile(token, model_profile_id))
                return
            if method == "PUT" and parsed.path.startswith("/api/config/model-profiles/"):
                model_profile_id = parsed.path.rsplit("/", 1)[-1]
                payload = self._read_json_body()
                self._write_success(
                    HTTPStatus.OK,
                    self.server.service.replace_model_profile(token, model_profile_id, payload),
                )
                return
            if method == "DELETE" and parsed.path.startswith("/api/config/model-profiles/"):
                model_profile_id = parsed.path.rsplit("/", 1)[-1]
                self._write_success(HTTPStatus.OK, self.server.service.delete_model_profile(token, model_profile_id))
                return

            # Block: InspectionRoutes
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

            # Block: NotFound
            raise ServiceError(404, "route_not_found", "The requested route does not exist.")
        except json.JSONDecodeError:
            self._write_error(HTTPStatus.BAD_REQUEST, "invalid_json", "The request body must be valid JSON.")
        except ServiceError as exc:
            self._write_error(exc.status_code, exc.error_code, exc.message)
        except Exception as exc:  # noqa: BLE001
            self._write_error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_server_error", str(exc))

    def _handle_events_stream(self, token: str | None) -> None:
        # Block: Authorization
        self.server.service._require_token(token)

        # Block: Headers
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

        # Block: Handshake
        accept_value = build_websocket_accept(websocket_key.strip())
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept_value)
        self.end_headers()
        self.wfile.flush()

        # Block: Connection
        websocket = ServerWebSocket(self.connection)
        session_id = self.server.service.register_event_stream_connection(websocket)
        try:
            # Block: ReceiveLoop
            while True:
                payload = websocket.receive_json()
                if payload is None:
                    break
                self.server.service.handle_event_stream_message(session_id, payload)
        except (json.JSONDecodeError, ServiceError, ValueError, WebSocketProtocolError):
            websocket.close()
        finally:
            # Block: Cleanup
            self.server.service.unregister_event_stream_connection(session_id)

    def _handle_logs_stream(self, token: str | None) -> None:
        # Block: Authorization
        self.server.service._require_token(token)

        # Block: Headers
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

        # Block: Handshake
        accept_value = build_websocket_accept(websocket_key.strip())
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept_value)
        self.end_headers()
        self.wfile.flush()

        # Block: Connection
        websocket = ServerWebSocket(self.connection)
        session_id = self.server.service.register_log_stream_connection(websocket)
        try:
            # Block: ReceiveLoop
            while True:
                text = websocket.receive_text()
                if text is None:
                    break
        except WebSocketProtocolError:
            websocket.close()
        finally:
            # Block: Cleanup
            self.server.service.remove_log_stream_connection(session_id)

    # Block: RequestHelpers
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

    # Block: ResponseHelpers
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

    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
