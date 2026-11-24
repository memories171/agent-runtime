from __future__ import annotations

import asyncio
from typing import Any

from mcp import Client

DEFAULT_MCP_TIMEOUT_SECONDS = 15.0


class MCPToolClient:
    """Thin adapter over the official MCP Python SDK v2 Streamable HTTP client.

    Every network operation is bounded by a timeout. An unreachable or hung MCP
    server must never block worker/API startup or stall an activity forever.
    """

    def __init__(self, timeout_seconds: float = DEFAULT_MCP_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds

    async def _call(self, operation) -> Any:
        return await asyncio.wait_for(operation(), timeout=self.timeout_seconds)

    async def list_tools(self, server_url: str) -> list[dict[str, Any]]:
        async def operation() -> list[dict[str, Any]]:
            async with Client(server_url, read_timeout_seconds=self.timeout_seconds) as client:
                result = await client.list_tools()
                return [
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "title": getattr(tool, "title", None) or getattr(tool, "displayName", None),
                        "input_schema": getattr(tool, "inputSchema", None)
                        or getattr(tool, "input_schema", None)
                        or {"type": "object"},
                    }
                    for tool in result.tools
                ]

        return await self._call(operation)

    async def call_tool(
        self, server_url: str, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            async with Client(server_url, read_timeout_seconds=self.timeout_seconds) as client:
                result = await client.call_tool(tool_name, arguments)
                if getattr(result, "structuredContent", None) is not None:
                    return result.structuredContent
                if getattr(result, "structured_content", None) is not None:
                    return result.structured_content
                text_blocks = [
                    item.text
                    for item in getattr(result, "content", [])
                    if getattr(item, "type", None) == "text"
                ]
                return {"content": text_blocks, "is_error": bool(getattr(result, "isError", False))}

        return await self._call(operation)
