from __future__ import annotations

import os
from contextlib import asynccontextmanager, nullcontext
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from .config import McpServerConfig
from .observed_persons import content_summary


async def list_tools(server: McpServerConfig) -> list[dict[str, Any]]:
    async with _client_session(server) as session:
        response = await session.list_tools()
        tools = []
        for tool in response.tools:
            payload = _to_plain(tool)
            if not isinstance(payload, dict):
                continue
            name = payload.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            tools.append(
                {
                    "name": name.strip(),
                    "description": payload.get("description") if isinstance(payload.get("description"), str) else "",
                    "inputSchema": payload.get("inputSchema") if isinstance(payload.get("inputSchema"), dict) else {"type": "object"},
                }
            )
        return tools


async def call_tool(server: McpServerConfig, *, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with _client_session(server) as session:
        response = await session.call_tool(tool_name, arguments=arguments)
        payload = _to_plain(response)
        if not isinstance(payload, dict):
            payload = {"content": []}
        content = payload.get("content")
        structured_content = payload.get("structuredContent")
        if structured_content is None:
            structured_content = payload.get("structured_content")
        is_error = payload.get("isError")
        if not isinstance(is_error, bool):
            is_error = bool(payload.get("is_error"))
        normalized_content = content if isinstance(content, list) else []
        normalized_structured = structured_content if isinstance(structured_content, dict) else None
        return {
            "is_error": is_error,
            "content": normalized_content,
            "structured_content": normalized_structured,
            "summary": content_summary(normalized_content, normalized_structured),
        }


@asynccontextmanager
async def _client_session(server: McpServerConfig):
    if server.transport == "stdio":
        params = _server_params(server)
        with _working_directory(server):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        return
    if server.transport != "streamable_http" or server.url is None:
        raise RuntimeError(f"Unsupported MCP transport: {server.transport}")
    timeout = httpx.Timeout(30.0, read=300.0)
    async with httpx.AsyncClient(
        headers=server.headers,
        timeout=timeout,
        follow_redirects=False,
    ) as http_client:
        async with streamable_http_client(server.url, http_client=http_client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def _server_params(server: McpServerConfig) -> StdioServerParameters:
    if server.transport != "stdio" or server.command is None:
        raise RuntimeError("stdio server parameters require transport=stdio and command.")
    # MCP child へ親 process の credential を継承せず、実行に必要な PATH と server 固有値だけを渡す。
    env = {"PATH": os.environ["PATH"]}
    env.update(server.env)
    return StdioServerParameters(command=server.command, args=server.args, env=env)


def _working_directory(server: McpServerConfig):
    if not server.cwd:
        return nullcontext()
    return _Chdir(server.cwd)


class _Chdir:
    def __init__(self, path: str) -> None:
        self.path = path
        self.previous_path = ""

    def __enter__(self) -> None:
        self.previous_path = os.getcwd()
        os.chdir(self.path)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        os.chdir(self.previous_path)


def _to_plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_plain(item) for key, item in value.items()}
    return value


