from __future__ import annotations

import json
import os
from contextlib import nullcontext
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import McpServerConfig


async def list_tools(server: McpServerConfig) -> list[dict[str, Any]]:
    params = _server_params(server)
    with _working_directory(server):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
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
    params = _server_params(server)
    with _working_directory(server):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
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
                return {
                    "is_error": is_error,
                    "content": content if isinstance(content, list) else [],
                    "structured_content": structured_content if isinstance(structured_content, dict) else None,
                    "summary": _content_summary(content if isinstance(content, list) else []),
                }


def _server_params(server: McpServerConfig) -> StdioServerParameters:
    env = dict(os.environ)
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


def _content_summary(content: list[Any]) -> str:
    text_parts: list[str] = []
    for item in content:
        payload = _to_plain(item)
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "text" and isinstance(payload.get("text"), str):
            text_parts.append(payload["text"].strip())
    if text_parts:
        return " ".join(part for part in text_parts if part)
    if content:
        return json.dumps(_to_plain(content), ensure_ascii=False)
    return ""
