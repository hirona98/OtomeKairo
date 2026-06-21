from __future__ import annotations

import json
import sys
from typing import Any


READ_TOOLS = {
    "get_information",
    "get_event",
    "get_my_posts",
    "search_post",
    "get_thread",
    "get_aituber",
    "get_followers",
    "get_following",
}
WRITE_TOOLS = {
    "create_post",
    "create_reply",
    "create_image",
    "mark_notifications_read",
    "like_post",
    "unlike_post",
    "follow_aituber",
    "unfollow_aituber",
}


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(request, dict):
            continue
        response = _handle(request)
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0


def _handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "elyth-fake-mcp-server", "version": "0.1.0"},
            },
        )
    if method == "tools/list":
        return _result(request_id, {"tools": [_tool_payload(name) for name in sorted(READ_TOOLS | WRITE_TOOLS)]})
    if method == "tools/call":
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        name = params.get("name")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        return _result(request_id, _call_tool(str(name), arguments))
    return _error(request_id, -32601, f"Unsupported method: {method}")


def _tool_payload(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"ELYTH local test tool: {name}",
        "inputSchema": {"type": "object", "additionalProperties": True},
    }


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name in WRITE_TOOLS:
        return {
            "isError": True,
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "ok": False,
                            "error": "dry_run_blocked",
                            "tool_name": name,
                            "arguments": arguments,
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
        }
    if name not in READ_TOOLS:
        return {"isError": True, "content": [{"type": "text", "text": f"unknown tool: {name}"}]}
    return {
        "isError": False,
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "ok": True,
                        "recorder": True,
                        "tool_name": name,
                        "arguments": arguments,
                        "items": [],
                    },
                    ensure_ascii=False,
                ),
            }
        ],
    }


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    raise SystemExit(main())
