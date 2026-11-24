from __future__ import annotations

import asyncio

import pytest

from agent_runtime.tools.mcp_client import MCPToolClient


@pytest.mark.asyncio
async def test_list_tools_times_out_when_server_hangs(monkeypatch):
    """An MCP server that never completes discovery must not hang the caller."""

    class HangingClient:
        def __init__(self, server_url, **kwargs):
            assert kwargs.get("read_timeout_seconds") == 0.05

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def list_tools(self):
            await asyncio.sleep(10)

    import agent_runtime.tools.mcp_client as mcp_module
    monkeypatch.setattr(mcp_module, "Client", HangingClient)

    client = MCPToolClient(timeout_seconds=0.05)
    with pytest.raises(asyncio.TimeoutError):
        await client.list_tools("http://127.0.0.1:1/mcp")


@pytest.mark.asyncio
async def test_call_tool_times_out_when_server_hangs(monkeypatch):
    class HangingClient:
        def __init__(self, server_url, **kwargs):
            assert kwargs.get("read_timeout_seconds") == 0.05

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def call_tool(self, name, arguments):
            await asyncio.sleep(10)

    import agent_runtime.tools.mcp_client as mcp_module
    monkeypatch.setattr(mcp_module, "Client", HangingClient)

    client = MCPToolClient(timeout_seconds=0.05)
    with pytest.raises(asyncio.TimeoutError):
        await client.call_tool("http://127.0.0.1:1/mcp", "echo", {"text": "x"})
