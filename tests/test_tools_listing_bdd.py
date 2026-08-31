"""test_tools_listing_bdd.py — Lista las tools del MCP walker (tools/list real)."""

from __future__ import annotations

import asyncio
import json
import os

from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.session import ClientSession


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN = os.path.join(_ROOT, "bin", "walker-mcp")


def test_listar_tools():
    async def _go():
        async with stdio_client(
            StdioServerParameters(
                command="python3", args=[_BIN], cwd=_ROOT, env={**os.environ}
            )
        ) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.list_tools()

    result = asyncio.run(_go())
    print("\n===== MCP walker — tools/list (real stdio) =====")
    for t in result.tools:
        print(f"\n• {t.name}")
        print(f"  description: {t.description}")
        print(f"  inputSchema: {json.dumps(t.inputSchema, ensure_ascii=False)}")
    print(f"\nTOTAL: {len(result.tools)} tool(s)")
    print("===== fin =====")
