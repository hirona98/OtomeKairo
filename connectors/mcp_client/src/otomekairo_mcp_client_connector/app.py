from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from .config import AppConfig, McpServerConfig
from .http import HttpError, JsonApiClient
from .mcp_bridge import call_tool, list_tools
from .stream import EventStreamClient, StreamError
from .trace import trace_writer_from_env


class McpClientConnector:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._trace = trace_writer_from_env()
        self._http = JsonApiClient(
            base_url=config.server.base_url,
            access_token=config.server.access_token,
            tls_verify=config.server.tls_verify,
            timeout_seconds=config.server.request_timeout_seconds,
            trace=self._trace,
        )
        self._tools_by_server: dict[str, list[dict[str, Any]]] = {}
        self._servers_by_id = {server.mcp_server_id: server for server in config.mcp_servers}

    def hello_payload(self) -> dict[str, Any]:
        return {
            "type": "hello",
            "client_id": self.config.client_id,
            "caps": [{"id": "mcp.call_tool", "version": "1"}],
            "mcp_servers": [
                {
                    "mcp_server_id": server.mcp_server_id,
                    "transport": "stdio",
                    "tools": self._tools_by_server.get(server.mcp_server_id, []),
                }
                for server in self.config.mcp_servers
            ],
        }

    def refresh_tools(self) -> None:
        tools_by_server: dict[str, list[dict[str, Any]]] = {}
        for server in self.config.mcp_servers:
            tools_by_server[server.mcp_server_id] = asyncio.run(list_tools(server))
        self._tools_by_server = tools_by_server

    def print_hello(self) -> None:
        self.refresh_tools()
        print(json.dumps(self.hello_payload(), ensure_ascii=False, indent=2))

    def run_forever(self) -> None:
        while True:
            try:
                self.refresh_tools()
                stream = EventStreamClient(
                    base_url=self.config.server.base_url,
                    access_token=self.config.server.access_token,
                    tls_verify=self.config.server.tls_verify,
                    socket_timeout_seconds=self.config.server.request_timeout_seconds,
                    trace=self._trace,
                )
                stream.run(hello_payload=self.hello_payload(), on_event=self._handle_event)
            except (StreamError, OSError, HttpError, RuntimeError) as exc:
                print(f"mcp connector stream error: {exc}", flush=True)
                time.sleep(self.config.server.reconnect_delay_seconds)

    def _handle_event(self, event: dict[str, Any]) -> None:
        if event.get("type") != "mcp.call_tool_request":
            return
        data = event.get("data")
        if not isinstance(data, dict):
            return
        request_id = data.get("request_id")
        mcp_server_id = data.get("mcp_server_id")
        tool_name = data.get("tool_name")
        arguments = data.get("arguments")
        if not isinstance(request_id, str) or not isinstance(mcp_server_id, str) or not isinstance(tool_name, str):
            return
        if not isinstance(arguments, dict):
            arguments = {}
        if self._trace is not None:
            self._trace.write(
                boundary="otomekairo_event",
                direction="receive",
                kind="mcp.call_tool_request.normalized",
                payload={
                    "request_id": request_id,
                    "mcp_server_id": mcp_server_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                },
            )
        server = self._servers_by_id.get(mcp_server_id)
        if server is None:
            self._post_result(
                request_id=request_id,
                mcp_server_id=mcp_server_id,
                tool_name=tool_name,
                status="failed",
                is_error=True,
                content=[],
                structured_content=None,
                summary="MCP server is not configured.",
                error="mcp_server_not_configured",
            )
            return
        try:
            result = asyncio.run(call_tool(server, tool_name=tool_name, arguments=arguments))
            is_error = result["is_error"]
            self._post_result(
                request_id=request_id,
                mcp_server_id=mcp_server_id,
                tool_name=tool_name,
                status="failed" if is_error else "completed",
                is_error=is_error,
                content=result["content"],
                structured_content=result["structured_content"],
                summary=result["summary"],
                error="mcp_tool_error" if is_error else None,
            )
        except Exception as exc:  # MCP server errors are returned as capability failures.
            self._post_result(
                request_id=request_id,
                mcp_server_id=mcp_server_id,
                tool_name=tool_name,
                status="failed",
                is_error=True,
                content=[],
                structured_content=None,
                summary="MCP tool execution failed.",
                error=exc.__class__.__name__,
            )

    def _post_result(
        self,
        *,
        request_id: str,
        mcp_server_id: str,
        tool_name: str,
        status: str,
        is_error: bool,
        content: list[Any],
        structured_content: dict[str, Any] | None,
        summary: str,
        error: str | None,
    ) -> None:
        payload = {
            "request_id": request_id,
            "client_id": self.config.client_id,
            "capability_id": "mcp.call_tool",
            "result": {
                "status": status,
                "mcp_server_id": mcp_server_id,
                "tool_name": tool_name,
                "is_error": is_error,
                "content": content,
                "structured_content": structured_content,
                "client_context": {
                    "mcp_server_id": mcp_server_id,
                    "tool_name": tool_name,
                    "mcp_result_summary": summary,
                },
                "error": error,
            },
        }
        if self._trace is not None:
            self._trace.write(
                boundary="otomekairo_event",
                direction="send",
                kind="mcp.call_tool_result",
                payload=payload,
            )
        self._http.post("/api/capability/result", payload)
