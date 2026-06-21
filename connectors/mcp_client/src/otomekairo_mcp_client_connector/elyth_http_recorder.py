from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .trace import trace_writer_from_env


READ_METHODS = {"GET"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class RecorderServer(ThreadingHTTPServer):
    trace = None


class RecorderHandler(BaseHTTPRequestHandler):
    server: RecorderServer

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        request_payload = {
            "method": self.command,
            "path": self.path,
            "headers": dict(self.headers.items()),
            "body": _decode_body(body),
        }
        if self.server.trace is not None:
            self.server.trace.write(
                boundary="elyth_http",
                direction="request",
                kind=f"{self.command} {urlparse(self.path).path}",
                payload=request_payload,
            )

        status_code, response_payload = _response_for(self.command, self.path)
        response_body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

        if self.server.trace is not None:
            self.server.trace.write(
                boundary="elyth_http",
                direction="response",
                kind=f"{status_code} {self.command} {urlparse(self.path).path}",
                payload=response_payload,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Record ELYTH MCP HTTP requests without forwarding them.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host.")
    parser.add_argument("--port", type=int, default=18080, help="Bind port.")
    args = parser.parse_args()

    server = RecorderServer((args.host, args.port), RecorderHandler)
    server.trace = trace_writer_from_env()
    print(f"ELYTH HTTP recorder listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
    return 0


def _response_for(method: str, path: str) -> tuple[int, dict[str, Any]]:
    parsed = urlparse(path)
    query = parse_qs(parsed.query)
    if method in WRITE_METHODS:
        return (
            HTTPStatus.CONFLICT,
            {
                "ok": False,
                "error": "dry_run_blocked: Write-like ELYTH request was blocked by the local recorder.",
                "error_detail": {
                    "code": "dry_run_blocked",
                    "message": "Write-like ELYTH request was blocked by the local recorder.",
                },
                "request": {"method": method, "path": parsed.path},
            },
        )
    if method not in READ_METHODS:
        return HTTPStatus.METHOD_NOT_ALLOWED, {"ok": False, "error": {"code": "method_not_allowed"}}
    return HTTPStatus.OK, _read_payload(parsed.path, query)


def _read_payload(path: str, query: dict[str, list[str]]) -> dict[str, Any]:
    base = {
        "ok": True,
        "recorder": True,
        "path": path,
        "query": query,
    }
    dummy_post = {
        "id": "00000000-0000-0000-0000-000000000000",
        "thread_id": "00000000-0000-0000-0000-000000000000",
        "content": "Local recorder dummy post.",
        "author_type": "aituber",
        "author_id": "local-recorder",
        "author_handle": "local_recorder",
        "author_name": "Local Recorder",
        "like_count": 0,
        "liked_by_me": False,
        "reply_count": 0,
        "created_at": "2026-06-21T09:46:14+09:00",
        "surface": "lobby" if "/api/mcp/lobby/" in path else "main",
    }
    if path.endswith("/api/mcp/information"):
        base.update(
            {
                "current_time": "2026-06-21T09:46:14+09:00",
                "platform_status": {"posts_last_hour": 0, "level": "local_recorder"},
                "timeline": [],
                "trends": {"posts": [], "hashtags": []},
                "glyph_ranking": {"ranking": []},
                "hot_aitubers": [],
                "active_aitubers": {"count": 0, "aitubers": []},
                "aituber_count": 0,
                "recent_updates": [
                    {
                        "id": "local-recorder-update",
                        "title": "Local recorder",
                        "content": "ELYTH HTTP recorder returned a dummy read response.",
                        "updated_at": "2026-06-21T09:46:14+09:00",
                    }
                ],
                "notifications": [],
                "elyth_news": [],
            }
        )
    elif path.endswith("/api/mcp/topic"):
        base["topic"] = {"title": "local recorder topic", "description": "dummy response"}
    elif path.endswith("/api/mcp/events/current"):
        base["event"] = None
    elif path.endswith("/thread"):
        base["posts"] = [dummy_post]
    elif path.endswith("/profile"):
        base["profile"] = {
            "handle": "local_recorder",
            "display_name": "Local Recorder",
            "bio": "Local recorder dummy profile.",
            "follower_count": 0,
            "following_count": 0,
            "post_count": 1,
            "followed_by_me": False,
            "follows_me": False,
            "is_live": False,
        }
        base["posts"] = [dummy_post]
    elif "/api/mcp/posts" in path:
        base["posts"] = []
    elif "/api/mcp/aitubers" in path:
        base["aituber"] = {"handle": "local-recorder", "display_name": "Local Recorder"}
        base["posts"] = []
    elif path.endswith("/api/mcp/followers") or path.endswith("/api/mcp/following"):
        base["items"] = []
        base["next_cursor"] = None
    return base


def _decode_body(body: bytes) -> Any:
    if not body:
        return None
    text = body.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


if __name__ == "__main__":
    raise SystemExit(main())
