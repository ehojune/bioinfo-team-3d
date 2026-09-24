"""Works with both `mcp<2` (FastMCP) and `mcp>=2` (FastMCP renamed to MCPServer)."""

from __future__ import annotations

import asyncio
import os
from typing import Any

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp < 2
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore


def make_server(name: str, instructions: str) -> Any:
    return _Server(name=name, instructions=instructions)


async def list_tools(spec: Any, timeout_s: float = 90) -> list[str]:
    """Start/connect an MCP server described by McpServerSpec and return its tool names."""
    from mcp import ClientSession

    async def _stdio() -> list[str]:
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=spec.command, args=list(spec.args),
            env={**os.environ, **{k: os.path.expandvars(v) for k, v in spec.env.items()}},
            cwd=spec.cwd,
        )
        async with stdio_client(params) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                res = await session.list_tools()
                return [t.name for t in res.tools]

    async def _http() -> list[str]:
        try:
            from mcp.client.streamable_http import streamable_http_client as http_client
        except ImportError:
            from mcp.client.streamable_http import streamablehttp_client as http_client  # type: ignore
        async with http_client(spec.url) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                res = await session.list_tools()
                return [t.name for t in res.tools]

    return await asyncio.wait_for(_stdio() if spec.type == "stdio" else _http(), timeout_s)
